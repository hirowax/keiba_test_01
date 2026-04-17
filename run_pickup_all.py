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
from race_pickup import scrape_shutuba, scrape_data_top, score_horses

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
HORSE_DB_PATH    = BASE_DIR / "output" / "horse_db.json"
HORSE_STYLE_PATH = BASE_DIR / "output" / "horse_style.json"
HORSE_DB_STALE_DAYS = 28  # キャッシュ有効期限（日）: 2週目以降のリクエスト数を大幅削減


def load_horse_db() -> dict:
    if HORSE_DB_PATH.exists():
        with open(HORSE_DB_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_horse_style() -> dict:
    if HORSE_STYLE_PATH.exists():
        with open(HORSE_STYLE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_horse_db(db: dict) -> None:
    HORSE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HORSE_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def is_cache_fresh(entry: dict, date: str) -> bool:
    """scraped_at が date から HORSE_DB_STALE_DAYS 以内かチェック"""
    scraped_at = entry.get("scraped_at", "")
    if not scraped_at:
        return False
    try:
        d_scraped = datetime.strptime(scraped_at, "%Y%m%d")
        d_target  = datetime.strptime(date, "%Y%m%d")
        return abs((d_target - d_scraped).days) <= HORSE_DB_STALE_DAYS
    except Exception:
        return False


def scrape_horse_prev_page(page, horse_id: str) -> dict:
    """db.netkeiba の馬ページから前走データを取得"""
    from bs4 import BeautifulSoup as BS
    url = f"https://db.netkeiba.com/horse/{horse_id}/"
    human_browse(page, url)
    _random_scroll(page)
    human_sleep(1.0, 3.0)
    soup = BS(page.content(), "html.parser")

    table = soup.find("table", class_="db_h_race_results")
    if not table:
        return {}
    rows = table.find_all("tr")
    data_rows = [r for r in rows if r.find("td")]
    if not data_rows:
        return {}

    cells = [c.get_text(strip=True) for c in data_rows[0].find_all("td")]

    def safe(idx):
        try: return cells[idx]
        except: return ""

    return {
        "prev_date":  safe(0),
        "prev_pop":   safe(10),
        "prev_rank":  safe(11),
        "prev_idx":   safe(20),
        "prev_idx_m": safe(21),
        "prev_dist":  safe(14),
    }


def main():
    date = sys.argv[1] if len(sys.argv) >= 2 else datetime.now().strftime("%Y%m%d")
    logger.info(f"対象日: {date}")

    csv_path = BASE_DIR / "output" / date / "全場_3指数重複馬.csv"
    if not csv_path.exists():
        logger.error(f"3指数重複馬CSVが見つかりません: {csv_path}")
        sys.exit(1)

    triple_df = pd.read_csv(csv_path, encoding="utf-8-sig")
    logger.info(f"3指数重複馬: {len(triple_df)}頭 / {triple_df.groupby(['開催場','レース番号']).ngroups}レース")

    email, password = load_env()

    # horse_db ロード
    horse_db = load_horse_db()
    logger.info(f"horse_db: {len(horse_db)}頭 キャッシュ済み")
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

            try:
                shutuba_data = scrape_shutuba(page, race_id)

                # ブロック検知
                if is_ip_blocked(page):
                    logger.error("❌ IPブロック検知 → 処理中断")
                    logger.error("   24時間待ってから再実行してください")
                    break

                # shutuba が空データなら page を再作成してリトライ
                if not shutuba_data.get("horse_map"):
                    logger.warning(f"  {race_label}: shutuba 空データ → ページ再作成してリトライ")
                    page.close()
                    page = context.new_page()
                    human_sleep(3.0, 6.0)
                    shutuba_data = scrape_shutuba(page, race_id)
                    if not shutuba_data.get("horse_map"):
                        logger.error(f"  {race_label}: リトライ後も空データ (スキップ)")
                        results[race_label] = {"error": "shutuba empty after retry"}
                        continue

                human_sleep(4.0, 10.0)
                data_top_data = scrape_data_top(page, race_id, shutuba_data["horse_map"])
                human_sleep(4.0, 10.0)

                # 前走データ: 3指数重複馬のみスクレイプ（非重複馬はキャッシュのみ利用）
                # → リクエスト数を最大16頭→3〜5頭に削減
                horse_id_map = shutuba_data.get("horse_id_map", {})
                triple_nums = {str(h["馬番"]) for h in triple_horses}
                for num, hid in horse_id_map.items():
                    if not hid:
                        continue
                    in_cache = hid in horse_db and is_cache_fresh(horse_db[hid], date)
                    is_triple = str(num) in triple_nums
                    # 重複馬: キャッシュ切れなら必ずスクレイプ
                    # 非重複馬: キャッシュがあれば使う、なければスキップ（race_max計算用のみ）
                    if is_triple and not in_cache:
                        try:
                            prev = scrape_horse_prev_page(page, hid)
                            prev["scraped_at"] = date
                            horse_db[hid] = prev
                            human_sleep(2.0, 6.0)
                        except Exception as e:
                            logger.warning(f"  前走データ取得失敗 {hid}: {e}")

                # レース内の前走指数最大値を計算（キャッシュ済みデータのみ利用）
                race_max_prev_idx = None
                prev_idxs = []
                for hid in horse_id_map.values():
                    if hid in horse_db:
                        try:
                            prev_idxs.append(float(horse_db[hid].get("prev_idx", "")))
                        except (ValueError, TypeError):
                            pass
                if prev_idxs:
                    race_max_prev_idx = max(prev_idxs)

                race_dist = shutuba_data.get("race_dist")
                scored = score_horses(triple_horses, shutuba_data, data_top_data,
                                      prev_db=horse_db, race_max_prev_idx=race_max_prev_idx,
                                      race_date=date, horse_style_db=horse_style_db,
                                      race_dist=race_dist)

                has_any_bonus = any(h["score"] > 0 for h in scored)
                advice = None
                if not has_any_bonus:
                    advice = "3指数重複馬全員のボーナス点が0です。他の指標を参考にしてください。"

                # ── 穴馬ライン: 前走1-2着 + 前走指数80+ + 今回5-10番人気 ──
                scored_nums = {str(h["馬番"]) for h in scored}
                pop_map = shutuba_data.get("pop_map", {})
                horse_map = shutuba_data.get("horse_map", {})
                anaba_list = []
                for num, hid in horse_id_map.items():
                    if num in scored_nums:
                        continue  # 3指数重複馬は除外（既にスコアリング済み）
                    pop_str = pop_map.get(num, "")
                    if not pop_str:
                        continue
                    try:
                        pop_val = int(pop_str)
                    except (ValueError, TypeError):
                        continue
                    if not (5 <= pop_val <= 10):
                        continue
                    prev = horse_db.get(hid, {})
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

                results[race_label] = {
                    "race_id": race_id,
                    "venue": venue,
                    "race_num": race_num,
                    "scored": scored,
                    "anaba": anaba_list,
                    "pop_map": shutuba_data.get("pop_map", {}),
                    "position_nums": shutuba_data["position_nums"],
                    "top3_hits": shutuba_data["top3_hits"],
                    "pickup_nums": data_top_data["pickup_nums"],
                    "analysis_hits": data_top_data["analysis_hits"],
                    "predicted_pace": shutuba_data.get("predicted_pace"),
                    "race_dist": shutuba_data.get("race_dist"),
                    "advice": advice,
                }

                max_score = max((h["score"] for h in scored), default=0)
                top_horse = next((h["馬名"] for h in scored if h["score"] == max_score), "")
                logger.info(f"  → {len(scored)}頭 最高{max_score}pt ({top_horse})")

            except Exception as e:
                logger.error(f"{race_label} 処理失敗: {e}")
                results[race_label] = {"error": str(e)}
                # ナビゲーションエラー等でページが壊れている可能性 → 再作成
                try:
                    page.close()
                except Exception:
                    pass
                page = context.new_page()
                logger.info(f"  ページ再作成完了（次のレースから復旧）")

        browser.close()

    # horse_db 保存
    save_horse_db(horse_db)
    logger.info(f"horse_db 保存: {HORSE_DB_PATH} ({len(horse_db)}頭)")

    # JSON 保存
    out_dir = BASE_DIR / "output" / date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "pickup_scores.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"date": date, "generated_at": datetime.now().isoformat(), "races": results},
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
