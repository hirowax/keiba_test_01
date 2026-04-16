#!/usr/bin/env python3
"""
全日付の race_results.json を再取得するスクリプト
騎手名・正確なオッズ・正確な人気を取得するための修正版再スクレイプ
"""
import json
import random
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from scraper import load_cookies, human_sleep, human_browse
from scrape_results import scrape_race_result, RESULT_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"


def load_race_ids(date: str) -> dict:
    """pickup_scores.json から race_id マップを取得"""
    pickup_path = OUTPUT_DIR / date / "pickup_scores.json"
    if not pickup_path.exists():
        return {}
    with open(pickup_path, encoding="utf-8") as f:
        pickup = json.load(f)
    return {
        label: rdata["race_id"]
        for label, rdata in pickup.get("races", {}).items()
        if rdata.get("race_id")
    }


def save_results(date: str, race_results: dict, race_conditions: dict):
    out_dir = OUTPUT_DIR / date
    with open(out_dir / "race_results.json", "w", encoding="utf-8") as f:
        json.dump(race_results, f, ensure_ascii=False, indent=2)
    with open(out_dir / "race_conditions.json", "w", encoding="utf-8") as f:
        json.dump(race_conditions, f, ensure_ascii=False, indent=2)


def main():
    # 対象日付：race_results.json が存在する全日付
    dates = sorted(
        d.name for d in OUTPUT_DIR.iterdir()
        if d.is_dir()
        and (OUTPUT_DIR / d.name / "race_results.json").exists()
        and (OUTPUT_DIR / d.name / "pickup_scores.json").exists()
    )
    log.info(f"対象日付: {len(dates)}日分 ({dates[0]} 〜 {dates[-1]})")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        if not load_cookies(context):
            log.error("cookies.json が見つかりません")
            browser.close()
            sys.exit(1)

        # 初回アクセス（ウォームアップ）
        page.goto("https://www.netkeiba.com/", wait_until="domcontentloaded")
        human_sleep(3.0, 5.0)

        total_races = 0
        total_done = 0

        for date_idx, date in enumerate(dates):
            race_id_map = load_race_ids(date)
            if not race_id_map:
                log.warning(f"{date}: race_id が取得できません (スキップ)")
                continue

            log.info(f"[{date_idx+1}/{len(dates)}] {date}: {len(race_id_map)}レース")
            total_races += len(race_id_map)

            race_results = {}
            race_conditions = {}
            failed = []

            for label, race_id in sorted(race_id_map.items()):
                try:
                    human_browse(page, RESULT_URL.format(race_id=race_id))
                    human_sleep(3.0, 7.0)

                    result = scrape_race_result(page, race_id, label)
                    if not result:
                        log.warning(f"  {label}: 取得失敗")
                        failed.append(label)
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
                    total_done += 1

                    # 10レースごとに少し長めの休憩
                    if total_done % 10 == 0:
                        rest = random.uniform(15.0, 30.0)
                        log.info(f"  --- {total_done}レース完了 ({rest:.0f}秒休憩) ---")
                        time.sleep(rest)

                except Exception as e:
                    log.error(f"  {label}: 例外 {e}")
                    failed.append(label)
                    try:
                        page.close()
                    except Exception:
                        pass
                    page = context.new_page()
                    human_sleep(5.0, 10.0)

            # 日付ごとに保存
            save_results(date, race_results, race_conditions)
            log.info(f"  → {date} 保存完了 ({len(race_results)}レース)")
            if failed:
                log.warning(f"  → 失敗: {failed}")

            # 日付をまたぐ際に長めの休憩
            if date_idx < len(dates) - 1:
                rest = random.uniform(20.0, 40.0)
                log.info(f"  === 次の日付まで {rest:.0f}秒休憩 ===")
                time.sleep(rest)

        browser.close()

    log.info(f"完了: {total_done}/{total_races}レース取得")


if __name__ == "__main__":
    main()
