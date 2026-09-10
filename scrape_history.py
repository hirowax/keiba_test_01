#!/usr/bin/env python3
"""
過去日付のレース結果を一括スクレイプして保存するスクリプト。
pickup_scores.json が不要（get_race_ids で全レースを取得）。

保存先:
  output/{date}/race_results.json    全馬着順・人気・馬体重等
  output/{date}/race_conditions.json レース条件

usage:
  python3 scrape_history.py 20260307 20260308 20260314 20260315
  python3 scrape_history.py 20260307  # 1日だけ
"""

import json
import logging
import random
import sys
import time
from pathlib import Path

from scraper import (
    human_sleep, human_browse, load_cookies, login, load_env,
    get_race_ids, COOKIES_FILE,
)
from scrape_results import scrape_race_result, RESULT_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

from playwright.sync_api import sync_playwright


def scrape_date(page, date: str) -> None:
    out_dir = OUTPUT_DIR / date
    results_path = out_dir / "race_results.json"
    conditions_path = out_dir / "race_conditions.json"

    # 既存ファイルがあればスキップ確認
    if results_path.exists():
        with open(results_path, encoding="utf-8") as f:
            existing = json.load(f)
        if len(existing) > 0:
            log.info(f"{date}: race_results.json 既存 ({len(existing)}レース) → スキップ")
            return

    log.info(f"=== {date} 開始 ===")
    races = get_race_ids(page, date)
    if not races:
        log.warning(f"{date}: レースが見つかりません")
        return

    log.info(f"{date}: {len(races)}レース取得")

    race_results = {}
    race_conditions = {}

    for r in races:
        race_id = r["race_id"]
        label = r["label"]
        log.info(f"  スクレイプ中: {label} ({race_id})")

        human_browse(page, RESULT_URL.format(race_id=race_id))
        time.sleep(random.uniform(2.0, 5.0))

        result = scrape_race_result(page, race_id, label)
        if not result:
            log.warning(f"  {label}: 取得失敗")
            continue

        horses = result["horses"]
        condition = result["condition"]
        condition["race_id"] = race_id

        race_results[label] = [
            {
                "rank":              h["rank"],
                "num":               h["num"],
                "name":              h["name"],
                "horse_id":          h["horse_id"],
                "pop":               h["pop"],
                "odds":              h["odds"],
                "jockey":            h["jockey"],
                "trainer":           h["trainer"],
                "weight_carried":    h["weight_carried"],
                "horse_weight":      h["horse_weight"],
                "horse_weight_diff": h["horse_weight_diff"],
                "time":              h["time"],
                "margin":            h["margin"],
                "last3f":            h["last3f"],
                "corners":           h["corners"],
                "sex_age":           h["sex_age"],
                "gate":              h["gate"],
            }
            for h in horses
        ]
        race_conditions[label] = condition

        human_sleep(4.0, 9.0)

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(race_results, f, ensure_ascii=False, indent=2)
    with open(conditions_path, "w", encoding="utf-8") as f:
        json.dump(race_conditions, f, ensure_ascii=False, indent=2)

    log.info(f"{date}: 保存完了 ({len(race_results)}レース) → {results_path}")


def main():
    if len(sys.argv) < 2:
        print("usage: python3 scrape_history.py YYYYMMDD [YYYYMMDD ...]")
        sys.exit(1)

    dates = sys.argv[1:]
    email, password = load_env()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = browser.new_page()

        if not load_cookies(context):
            login(page, email, password)
        else:
            page.goto("https://www.netkeiba.com/", wait_until="domcontentloaded")
            human_sleep(1.0, 2.0)

        for date in dates:
            try:
                scrape_date(page, date)
            except Exception as e:
                log.error(f"{date}: エラー {e}")
            if date != dates[-1]:
                log.info("次の日付まで待機中...")
                human_sleep(8.0, 15.0)

        browser.close()

    log.info("全日付完了")


if __name__ == "__main__":
    main()
