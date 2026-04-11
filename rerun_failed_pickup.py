#!/usr/bin/env python3
"""
失敗したレースのみ再ピックアップして pickup_scores.json にマージ。
usage: python3 rerun_failed_pickup.py 20260412
"""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

from scraper import load_env, load_cookies, get_race_ids, human_sleep, human_browse, _random_scroll
from race_pickup import scrape_shutuba, scrape_data_top, score_horses
from run_pickup_all import load_horse_db, load_horse_style, save_horse_db, is_cache_fresh, scrape_horse_prev_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent


def main():
    date = sys.argv[1] if len(sys.argv) >= 2 else datetime.now().strftime("%Y%m%d")
    out_dir = BASE_DIR / "output" / date
    pickup_path = out_dir / "pickup_scores.json"

    if not pickup_path.exists():
        logger.error(f"pickup_scores.json が見つかりません: {pickup_path}")
        sys.exit(1)

    with open(pickup_path, encoding="utf-8") as f:
        existing = json.load(f)

    # 失敗レースを特定: error あり、または scored 全員 0pt かつ pop_map 空
    failed_labels = []
    for label, rdata in existing.get("races", {}).items():
        if "error" in rdata:
            failed_labels.append(label)
        elif rdata.get("pop_map") is not None and len(rdata.get("pop_map", {})) == 0:
            scored = rdata.get("scored", [])
            if scored and all(h.get("score", 0) == 0 for h in scored):
                failed_labels.append(label)

    if not failed_labels:
        logger.info("再取得が必要なレースはありません")
        return

    logger.info(f"再取得対象: {sorted(failed_labels)}")

    csv_path = out_dir / "全場_3指数重複馬.csv"
    if not csv_path.exists():
        logger.error(f"CSVが見つかりません: {csv_path}")
        sys.exit(1)

    triple_df = pd.read_csv(csv_path, encoding="utf-8-sig")
    horse_db = load_horse_db()
    horse_style_db = load_horse_style()

    email, password = load_env()

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

        # レース一覧取得
        races_all = get_race_ids(page, date)
        race_id_map = {r["label"]: r["race_id"] for r in races_all}
        logger.info(f"レース一覧: {len(race_id_map)}レース取得")

        race_groups = list(triple_df.groupby(["開催場", "レース番号"]))

        for (venue, race_num_raw), group in race_groups:
            race_num = race_num_raw.strip()
            race_label = f"{venue}{race_num}"

            if race_label not in failed_labels:
                continue

            race_id = race_id_map.get(race_label)
            if not race_id:
                logger.warning(f"{race_label}: race_id が見つかりません (スキップ)")
                continue

            logger.info(f"再取得中: {race_label} ({race_id})")
            triple_horses = group.to_dict(orient="records")

            try:
                shutuba_data = scrape_shutuba(page, race_id)

                if not shutuba_data.get("horse_map"):
                    logger.warning(f"  {race_label}: shutuba 空 → ページ再作成してリトライ")
                    page.close()
                    page = context.new_page()
                    human_sleep(3.0, 6.0)
                    shutuba_data = scrape_shutuba(page, race_id)
                    if not shutuba_data.get("horse_map"):
                        logger.error(f"  {race_label}: リトライ後も空 (スキップ)")
                        continue

                human_sleep(4.0, 10.0)
                data_top_data = scrape_data_top(page, race_id, shutuba_data["horse_map"])
                human_sleep(4.0, 10.0)

                # 前走データ
                horse_id_map = shutuba_data.get("horse_id_map", {})
                for num, hid in horse_id_map.items():
                    if hid and (hid not in horse_db or not is_cache_fresh(horse_db[hid], date)):
                        try:
                            prev = scrape_horse_prev_page(page, hid)
                            prev["scraped_at"] = date
                            horse_db[hid] = prev
                            human_sleep(2.0, 6.0)
                        except Exception as e:
                            logger.warning(f"  前走データ取得失敗 {hid}: {e}")

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

                existing["races"][race_label] = {
                    "race_id": race_id,
                    "venue": venue,
                    "race_num": race_num,
                    "scored": scored,
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
                try:
                    page.close()
                except Exception:
                    pass
                page = context.new_page()

        browser.close()

    save_horse_db(horse_db)
    existing["generated_at"] = datetime.now().isoformat()
    with open(pickup_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2, default=str)

    logger.info(f"マージ保存完了: {pickup_path}")

    # サマリー
    for label in sorted(failed_labels):
        rdata = existing["races"].get(label, {})
        if "error" in rdata:
            logger.info(f"  {label}: まだエラー")
        else:
            for h in rdata.get("scored", []):
                logger.info(f"  {label}: {h['馬名']} {h['score']}pt (人気{h.get('today_pop','')})")


if __name__ == "__main__":
    main()
