#!/usr/bin/env python3
"""
全レース一括ピックアップスコアリング
usage: python3 run_pickup_all.py [YYYYMMDD]
"""

import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from scraper import (
    load_env, load_cookies, save_cookies, is_logged_in, login,
    get_race_ids,
)
from race_pickup import scrape_shutuba, scrape_data_top, score_horses

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
ACCESS_INTERVAL = 3


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

    results = {}  # race_label -> 結果dict

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        # ログイン
        if load_cookies(context) and is_logged_in(page):
            logger.info("cookies でログイン済み")
        else:
            login(page, email, password)
            save_cookies(context)

        # レース一覧取得 → race_id マップ
        races_all = get_race_ids(page, date)
        race_id_map = {r["label"]: r["race_id"] for r in races_all}
        logger.info(f"レース一覧: {len(race_id_map)}レース取得")

        # 3指数重複馬があるレースのみ処理
        race_groups = list(triple_df.groupby(["開催場", "レース番号"]))
        logger.info(f"処理対象: {len(race_groups)}レース")

        for (venue, race_num_raw), group in race_groups:
            # "2R" / "10R" など正規化
            race_num = race_num_raw.strip()
            race_label = f"{venue}{race_num}"

            race_id = race_id_map.get(race_label)
            if not race_id:
                logger.warning(f"{race_label}: race_id が見つかりません (スキップ)")
                continue

            logger.info(f"処理中: {race_label} ({race_id})")
            triple_horses = group.to_dict(orient="records")

            try:
                shutuba_data = scrape_shutuba(page, race_id)
                time.sleep(ACCESS_INTERVAL)
                data_top_data = scrape_data_top(page, race_id, shutuba_data["horse_map"])
                time.sleep(ACCESS_INTERVAL)

                scored = score_horses(triple_horses, shutuba_data, data_top_data)

                has_any_bonus = any(h["score"] > 0 for h in scored)
                advice = None
                if not has_any_bonus:
                    advice = "3指数重複馬全員のボーナス点が0です。他の指標を参考にしてください。"

                results[race_label] = {
                    "race_id": race_id,
                    "venue": venue,
                    "race_num": race_num,
                    "scored": scored,
                    "position_nums": shutuba_data["position_nums"],
                    "top3_hits": shutuba_data["top3_hits"],
                    "pickup_nums": data_top_data["pickup_nums"],
                    "analysis_hits": data_top_data["analysis_hits"],
                    "advice": advice,
                }

                max_score = max((h["score"] for h in scored), default=0)
                top_horse = next((h["馬名"] for h in scored if h["score"] == max_score), "")
                logger.info(f"  → {len(scored)}頭 最高{max_score}pt ({top_horse})")

            except Exception as e:
                logger.error(f"{race_label} 処理失敗: {e}")
                results[race_label] = {"error": str(e)}

        browser.close()

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
