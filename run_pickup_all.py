#!/usr/bin/env python3
"""
全レース一括ピックアップスコアリング
usage: python3 run_pickup_all.py [YYYYMMDD]
"""

import sys
import json
import time
import random
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from scraper import (
    load_env, load_cookies, save_cookies, is_logged_in, login,
    get_race_ids, human_sleep, human_browse, _random_scroll, is_ip_blocked,
)
from data_quality import build_quality, load_index_sources, race_quality_issues
from race_pickup import (scrape_shutuba, scrape_data_top, score_horses, active_horse_ids,
                         SCORING_VERSION, COLLECTOR_VERSION)
from prev_inputs import (FAILED, RunRecord, fetch_entry, load_runs, prev_db_entry, prev_status_summary,
                         race_top1, reusable_entries, PREV_FETCH_VERSION)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
HORSE_STYLE_PATH = BASE_DIR / "output" / "horse_style.json"


def load_horse_style() -> dict:
    if HORSE_STYLE_PATH.exists():
        with open(HORSE_STYLE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _settle_horse_page(page) -> None:
    """馬ページを開いた後の待機（今までの取得と同じ）"""
    _random_scroll(page)
    human_sleep(1.0, 3.0)


def collect_prev_for_race(page, date: str, race_label: str, race_id: str, shutuba_data: dict,
                          candidate_nums, reusable: dict, record) -> tuple:
    """1レース分の前走データ（取消・除外を除く全馬）をそろえて記録する。
    再利用してよい値（prev_inputs.reusable_entries）が無い馬だけ馬ページを取る。失敗しても前の値に戻らない。
    戻り値: (採点に渡す前走の値 {馬ID: dict}, 前走指数レース内1位の判定, quality.prev_status)"""
    active = active_horse_ids(shutuba_data)
    entries = {}
    for num, hid in active.items():
        if not hid:
            continue
        if hid in reusable:
            entries[hid] = dict(reusable[hid], source="再利用")
            continue
        e = fetch_entry(page, hid, date, human_browse, _settle_horse_page)
        if e["status"] == FAILED:
            logger.warning(f"  {race_label} {num}番 前走データ取得失敗: {e['reason']}")
        entries[hid] = e
        human_sleep(2.0, 6.0)
    top1 = race_top1(active, entries)
    record.add_race(race_label, race_id, entries, top1)
    prev_db = {}
    for hid, e in entries.items():
        v = prev_db_entry(e)
        if v is not None:
            prev_db[hid] = v
    status = prev_status_summary([str(n) for n in candidate_nums], active, entries, top1)
    return prev_db, top1, status


def write_prev_data_snapshot(out_dir: Path, races: dict) -> int:
    """prev_data.json（互換のための派生ファイル）を、各レースが参照する実行の記録だけから作る"""
    runs = {r["run_id"]: r for r in load_runs(out_dir)}
    snapshot = {}
    for label, rdata in races.items():
        run = runs.get((rdata or {}).get("prev_inputs_run"))
        if not run or label not in run.get("races", {}):
            continue
        for hid, e in run["races"][label]["horses"].items():
            v = prev_db_entry(e)
            if v is not None:
                snapshot[hid] = dict(v, target_date=e["target_date"], status=e["status"])
    with open(out_dir / "prev_data.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return len(snapshot)


def score_race(page, context, date: str, race_label: str, race_id: str, venue: str, race_num: str,
               triple_horses: list, index_sources: dict, reusable: dict, record, horse_style_db: dict) -> tuple:
    """1レース分: 出馬表・データ分析・前走データ（全馬）・採点・穴馬ライン。
    run_pickup_all.py と rerun_failed_pickup.py の両方から呼ぶ（同じ処理を1か所にまとめる）。
    戻り値: (pickup_scores.json のレースの値。IPブロックなら None, 使い続ける page, IPブロックか)"""
    try:
        shutuba_data = scrape_shutuba(page, race_id)

        # ブロック検知
        if is_ip_blocked(page):
            logger.error("❌ IPブロック検知 → 処理中断")
            logger.error("   24時間待ってから再実行してください")
            return None, page, True

        # shutuba が空データなら page を再作成してリトライ
        if not shutuba_data.get("horse_map"):
            logger.warning(f"  {race_label}: shutuba 空データ → ページ再作成してリトライ")
            page.close()
            page = context.new_page()
            human_sleep(3.0, 6.0)
            shutuba_data = scrape_shutuba(page, race_id)
            if not shutuba_data.get("horse_map"):
                logger.error(f"  {race_label}: リトライ後も空データ (スキップ)")
                return {"error": "shutuba empty after retry"}, page, False

        # 馬番↔馬ID対応が検証できないレースはスコアリングしない（枠番誤認・欠損で別馬のデータが付くため）
        # ページの読み込み途中の可能性があるので1回だけ待って再取得する
        if shutuba_data.get("map_issues"):
            logger.warning(f"  {race_label}: 出馬表の対応検証NG {shutuba_data['map_issues']} → 待機して再取得")
            human_sleep(8.0, 15.0)
            shutuba_data = scrape_shutuba(page, race_id)
        if shutuba_data.get("map_issues"):
            logger.error(f"  {race_label}: 出馬表の対応検証NG {shutuba_data['map_issues']} (スキップ)")
            return {"error": "shutuba map invalid", "map_issues": shutuba_data["map_issues"]}, page, False

        human_sleep(4.0, 10.0)
        data_top_data = scrape_data_top(page, race_id, shutuba_data["horse_map"])
        human_sleep(4.0, 10.0)

        # 前走データ: 取消・除外を除く全馬（比較相手を含む）。同じ対象日の値だけ再利用する
        horse_id_map = shutuba_data.get("horse_id_map", {})
        triple_nums = {str(h["馬番"]) for h in triple_horses}
        prev_db, top1, prev_status = collect_prev_for_race(
            page, date, race_label, race_id, shutuba_data, triple_nums, reusable, record)
        race_max_prev_idx = top1["race_max"]   # 保留なら None（1位の加点なし）

        race_dist = shutuba_data.get("race_dist")
        scored = score_horses(triple_horses, shutuba_data, data_top_data,
                              prev_db=prev_db, race_max_prev_idx=race_max_prev_idx,
                              race_date=date, horse_style_db=horse_style_db,
                              race_dist=race_dist,
                              predicted_pace=shutuba_data.get("predicted_pace"))

        has_any_bonus = any(h["score"] > 0 for h in scored)
        advice = None
        if not has_any_bonus:
            advice = "3指数重複馬全員のボーナス点が0です。他の指標を参考にしてください。"

        # ── 穴馬ライン: 前走1-2着 + 前走指数80+ + 今回5-10番人気 ──
        scored_nums = {str(h["馬番"]) for h in scored}
        pop_map = shutuba_data.get("pop_map", {})
        horse_map = shutuba_data.get("horse_map", {})
        anaba_list = []
        for num, hid in active_horse_ids(shutuba_data).items():  # 取消・除外は候補外
            if num in scored_nums:
                continue  # 3指数重複馬は除外（既にスコアリング済み）
            pop_str = pop_map.get(num, "")
            if not pop_str:
                continue
            try:
                pop_val = int(pop_str)
            except (ValueError, TypeError):
                continue
            if pop_val < 5:
                continue
            prev = prev_db.get(hid, {})   # その対象日のために取った値だけ（取得失敗・前走なしは空）
            try:
                prev_rank = int(prev.get("prev_rank", "") or 99)
            except (ValueError, TypeError):
                prev_rank = 99
            try:
                prev_idx = float(prev.get("prev_idx", "") or 0)
            except (ValueError, TypeError):
                prev_idx = 0
            if prev_rank <= 2 and prev_idx >= 80:
                anaba_list.append({
                    "num": num,
                    "name": horse_map.get(num, ""),
                    "pop": pop_val,
                    "prev_rank": prev_rank,
                    "prev_idx": prev_idx,
                })
        if anaba_list:
            anaba_list.sort(key=lambda x: x["prev_idx"], reverse=True)
            logger.info(f"  穴馬ライン: {len(anaba_list)}頭 "
                        + ", ".join(f"{a['num']}番{a['name']}({a['pop']}人気)" for a in anaba_list))

        result = {
            "race_id": race_id,
            "venue": venue,
            "race_num": race_num,
            "scored": scored,
            "anaba": anaba_list,
            "pop_map": shutuba_data.get("pop_map", {}),
            "odds_map": shutuba_data.get("odds_map", {}),
            "odds_status": shutuba_data.get("odds_status", {}),
            "scratched_nums": shutuba_data.get("scratched_nums", {}),
            "horse_name_full": shutuba_data.get("horse_name_full", {}),
            "collector_version": COLLECTOR_VERSION,
            "quality": build_quality(COLLECTOR_VERSION, index_sources, race_label,
                                     prev=dict(prev_fetch=PREV_FETCH_VERSION, prev_inputs_run=record.run_id,
                                               prev_status=prev_status)),
            "prev_inputs_run": record.run_id,
            "horse_id_map": horse_id_map,
            "position_nums": shutuba_data["position_nums"],
            "top3_hits": shutuba_data["top3_hits"],
            "pickup_nums": data_top_data["pickup_nums"],
            "analysis_hits": data_top_data["analysis_hits"],
            "predicted_pace": shutuba_data.get("predicted_pace"),
            "race_dist": shutuba_data.get("race_dist"),
            "race_surface": shutuba_data.get("race_surface"),
            "advice": advice,
        }

        max_score = max((h["score"] for h in scored), default=0)
        top_horse = next((h["馬名"] for h in scored if h["score"] == max_score), "")
        logger.info(f"  → {len(scored)}頭 最高{max_score}pt ({top_horse})")
        return result, page, False

    except Exception as e:
        logger.error(f"{race_label} 処理失敗: {e}")
        result = {"error": str(e)}
        # ナビゲーションエラー等でページが壊れている可能性 → 再作成
        try:
            page.close()
        except Exception:
            pass
        page = context.new_page()
        logger.info(f"  ページ再作成完了（次のレースから復旧）")
        return result, page, False


def main():
    date = sys.argv[1] if len(sys.argv) >= 2 else datetime.now().strftime("%Y%m%d")
    logger.info(f"対象日: {date}")

    csv_path = BASE_DIR / "output" / date / "全場_3指数重複馬.csv"
    if not csv_path.exists():
        logger.error(f"3指数重複馬CSVが見つかりません: {csv_path}")
        sys.exit(1)

    triple_df = pd.read_csv(csv_path, encoding="utf-8-sig")
    index_sources = load_index_sources(date)  # 指数の取得経路（scraper.py が保存。無ければ quality に記録されない）
    logger.info(f"3指数重複馬: {len(triple_df)}頭 / {triple_df.groupby(['開催場','レース番号']).ngroups}レース")

    email, password = load_env()

    # 前走データ: 同じ対象日のために取った値だけを再利用する（docs/prev_cache_fix_plan_202611.md）
    out_dir = BASE_DIR / "output" / date
    out_dir.mkdir(parents=True, exist_ok=True)
    reusable = reusable_entries(out_dir, date)
    logger.info(f"前走データ: 再利用できる値 {len(reusable)}頭")
    record = RunRecord(out_dir, date, "run_pickup_all.py", SCORING_VERSION)
    logger.info(f"前走データの記録: {record.path}")
    horse_style_db = load_horse_style()
    logger.info(f"horse_style: {len(horse_style_db)}頭")

    results = {}  # race_label -> 結果dict

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

        # cookiesのみ（自動ログインしない → プレミアムcookies保護）
        if not load_cookies(context):
            logger.error("❌ cookies.json が見つかりません。")
            logger.error("   → python3 save_cookies.py を実行してログインしてください")
            browser.close()
            sys.exit(1)

        # レース一覧取得 → race_id マップ
        races_all = get_race_ids(page, date)
        race_id_map = {r["label"]: r["race_id"] for r in races_all}
        logger.info(f"レース一覧: {len(race_id_map)}レース取得")

        # 3指数重複馬があるレースのみ処理
        race_groups = list(triple_df.groupby(["開催場", "レース番号"]))
        logger.info(f"処理対象: {len(race_groups)}レース")

        REST_EVERY = 5   # N レースごとに長休憩
        REST_SEC   = 90  # 長休憩の秒数

        for race_idx, ((venue, race_num_raw), group) in enumerate(race_groups):
            # "2R" / "10R" など正規化
            race_num = race_num_raw.strip()
            race_label = f"{venue}{race_num}"

            race_id = race_id_map.get(race_label)
            if not race_id:
                logger.warning(f"{race_label}: race_id が見つかりません (スキップ)")
                continue

            # 5レースごとに長休憩
            if race_idx > 0 and race_idx % REST_EVERY == 0:
                logger.info(f"  [{race_idx}/{len(race_groups)}] {REST_SEC}秒休憩中...")
                time.sleep(REST_SEC)

            logger.info(f"処理中: {race_label} ({race_id})")
            triple_horses = group.to_dict(orient="records")

            result, page, blocked = score_race(page, context, date, race_label, race_id, venue, race_num,
                                               triple_horses, index_sources, reusable, record, horse_style_db)
            if blocked:
                break
            results[race_label] = result

        browser.close()

    record.close()

    # quality_issues: 品質の判定（data_quality.race_quality_issues）をこの時点で記録する。サイト・買い目はこれを使う
    for label, rdata in results.items():
        if isinstance(rdata, dict) and "error" not in rdata:
            rdata["quality_issues"] = race_quality_issues(rdata)

    n_snap = write_prev_data_snapshot(out_dir, results)
    logger.info(f"prev_data.json 保存（記録から作った派生ファイル）: {n_snap}頭")
    out_path = out_dir / "pickup_scores.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"date": date, "generated_at": datetime.now().isoformat(),
             "scoring_version": SCORING_VERSION, "collector_version": COLLECTOR_VERSION,
             "races": results},
            f, ensure_ascii=False, indent=2, default=str
        )

    logger.info(f"保存: {out_path} ({len(results)}レース)")

    # サマリー表示
    high_stars = []
    for label, data in results.items():
        for h in data.get("scored", []):
            if h["score"] >= 5:
                high_stars.append(f"{label} {h['馬番']}番{h['馬名']} ({h['score']}pt)")
    if high_stars:
        logger.info("★★★ 最注目馬:")
        for s in high_stars:
            logger.info(f"  {s}")
    else:
        logger.info("★★★ 該当馬なし")


if __name__ == "__main__":
    main()
