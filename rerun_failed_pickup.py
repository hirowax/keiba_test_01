#!/usr/bin/env python3
"""
失敗したレースのみ再ピックアップして pickup_scores.json にマージ。
usage: python3 rerun_failed_pickup.py 20260412

再実行の対象（needs_rerun）:
- レースの error
- 候補全員0点かつ人気が空（出馬表が読めていない）
- 前走データ: 候補または比較相手に「取得失敗」、候補に「入力が無い」「馬IDが無い」（docs/prev_cache_fix_plan_202611.md 案D）
  出馬表で取消・除外の候補（「取消・除外（出馬表）」）は対象にしない。
  指数の「**」（ログインしていない状態の表示）は取得失敗として扱うので、対象になる（2026-11-05 決定）。
1レース分の処理は run_pickup_all.score_race と同じ（同じ処理を1か所にまとめる）。
既にある pickup_scores.json の点数の版（scoring_version）・馬番の取り方の版（collector_version）が今のコードと違えば、
何もせずに止まる（2026-11-07 外部レビュー。新旧の点数が1つのファイルに混ざり、サイト・買い目がファイルの版で旧方式として
扱うのを防ぐ。ファイルの版だけ書き換えると、残った旧点数を新方式として表示するので、それもしない）。
違う版で採点し直すなら、別の実行の記録として保存する（このスクリプトではしない）。
前走データは、同じ対象日のために取った値のうち、馬ごとの最新の取得が成功しているもの（prev_inputs.reusable_entries）を
再利用し、それ以外を取り直す。
ロック（prev_inputs.DateLock）は pickup_scores.json の読み取りから、保存・派生ファイルの保存が終わるまで持つ。
"""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

from scraper import load_env, load_cookies, get_race_ids
from data_quality import load_index_sources, race_quality_issues
from race_pickup import SCORING_VERSION, COLLECTOR_VERSION
from prev_inputs import FAILED, DateLock, RunRecord, reusable_entries, write_json_atomic
from run_pickup_all import StyleContext, read_index_tables, score_race, write_prev_data_snapshot
from build_horse_style import StyleRecordError, load_for_rerun

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent


def needs_rerun(rdata: dict) -> list:
    """再実行が必要な理由のリスト（空なら不要）。点数からは推測せず、記録の状態で判断する"""
    reasons = []
    if "error" in rdata:
        return [f"採点エラー: {rdata['error']}"]
    if rdata.get("pop_map") is not None and len(rdata.get("pop_map", {})) == 0:
        scored = rdata.get("scored", [])
        if scored and all(h.get("score", 0) == 0 for h in scored):
            reasons.append("候補全員0点かつ人気が空")
    st = (rdata.get("quality") or {}).get("prev_status") or {}
    bad = {n: s for n, s in (st.get("candidates") or {}).items() if s in (FAILED, "入力が無い", "馬IDが無い")}
    if bad:
        reasons.append(f"候補の前走データ: {bad}")
    n_fail = (st.get("others") or {}).get(FAILED, 0)
    if n_fail:
        reasons.append(f"比較相手の前走データの取得失敗 {n_fail}頭")
    return reasons


def version_mismatch(existing: dict) -> list:
    """既にある pickup_scores.json の版が今のコードと違う理由のリスト（空なら部分再実行してよい）"""
    out = []
    for key, now in (("scoring_version", SCORING_VERSION), ("collector_version", COLLECTOR_VERSION)):
        if existing.get(key) != now:
            out.append(f"{key}: ファイル {existing.get(key)}・今のコード {now}")
    return out


def main():
    date = sys.argv[1] if len(sys.argv) >= 2 else datetime.now().strftime("%Y%m%d")
    out_dir = BASE_DIR / "output" / date
    pickup_path = out_dir / "pickup_scores.json"

    if not pickup_path.exists():
        logger.error(f"pickup_scores.json が見つかりません: {pickup_path}")
        sys.exit(1)

    lock = DateLock(out_dir)   # 読み取りから保存までの間に、同じ日の別の実行が書かないようにする
    with open(pickup_path, encoding="utf-8") as f:
        existing = json.load(f)

    mismatch = version_mismatch(existing)
    if mismatch:
        logger.error(f"版が違うので部分再実行しません（新旧の点数が混ざるため）: {mismatch}")
        lock.release()
        sys.exit(1)

    failed = {label: r for label, rdata in existing.get("races", {}).items() if (r := needs_rerun(rdata))}
    if not failed:
        logger.info("再取得が必要なレースはありません")
        lock.release()
        return
    for label, r in sorted(failed.items()):
        logger.info(f"再取得対象: {label} {r}")

    csv_path = out_dir / "全場_3指数重複馬.csv"
    if not csv_path.exists():
        logger.error(f"CSVが見つかりません: {csv_path}")
        sys.exit(1)

    triple_df = pd.read_csv(csv_path, encoding="utf-8-sig")
    # 脚質: 作らずに、その日の記録を使う（確定した作成成功、無ければ最新の作成失敗）。どちらも無ければ止める（計画 2.6）
    try:
        style = StyleContext(*load_for_rerun(date))
    except StyleRecordError as e:
        logger.error(f"脚質の記録を使えないので部分再実行しません: {e}")
        lock.release()
        sys.exit(1)
    index_sources = load_index_sources(date)
    index_tables = read_index_tables(date)
    reusable = reusable_entries(out_dir, date)
    logger.info(f"前走データ: 再利用できる値 {len(reusable)}頭")
    record = RunRecord(out_dir, date, "rerun_failed_pickup.py", SCORING_VERSION, lock)
    logger.info(f"前走データの記録: {record.path}")

    load_env()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        if not load_cookies(context):
            logger.error("❌ cookies.json が見つかりません → python3 save_cookies.py")
            browser.close()
            sys.exit(1)

        races_all = get_race_ids(page, date)
        race_id_map = {r["label"]: r["race_id"] for r in races_all}
        logger.info(f"レース一覧: {len(race_id_map)}レース取得")

        for (venue, race_num_raw), group in triple_df.groupby(["開催場", "レース番号"]):
            race_num = race_num_raw.strip()
            race_label = f"{venue}{race_num}"
            if race_label not in failed:
                continue
            race_id = race_id_map.get(race_label)
            if not race_id:
                logger.warning(f"{race_label}: race_id が見つかりません (スキップ)")
                continue
            logger.info(f"再取得中: {race_label} ({race_id})")
            result, page, blocked = score_race(page, context, date, race_label, race_id, venue, race_num,
                                               group.to_dict(orient="records"), index_sources, reusable,
                                               record, style, index_tables)
            if blocked:
                break
            if "error" in result and "error" not in existing["races"].get(race_label, {}):
                # 前は採点できていたレースが今回エラーなら、前の結果を残す（エラーで上書きしない）
                logger.error(f"  {race_label}: 再取得がエラー（{result['error']}）→ 前の結果を残す")
                continue
            existing["races"][race_label] = result

        browser.close()

    record.finish()
    for label in failed:
        rdata = existing["races"].get(label)
        if isinstance(rdata, dict) and "error" not in rdata:
            rdata["quality_issues"] = race_quality_issues(rdata)

    existing["generated_at"] = datetime.now().isoformat()
    write_json_atomic(pickup_path, existing)
    logger.info(f"マージ保存完了: {pickup_path}")

    n_snap = write_prev_data_snapshot(out_dir, existing.get("races", {}))
    logger.info(f"prev_data.json 保存（記録から作った派生ファイル）: {n_snap}頭")
    lock.release()

    for label in sorted(failed):
        rdata = existing["races"].get(label, {})
        left = needs_rerun(rdata)
        if left:
            logger.info(f"  {label}: まだ再実行が必要 {left}")
        else:
            for h in rdata.get("scored", []):
                logger.info(f"  {label}: {h['馬名']} {h['score']}pt (人気{h.get('today_pop','')})")


if __name__ == "__main__":
    main()
