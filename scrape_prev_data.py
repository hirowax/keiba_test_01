#!/usr/bin/env python3
"""
前走データ収集スクリプト
race_results.json から horse_id を取得し、各馬の前走データをスクレイプ
output/horse_db.json にグローバルキャッシュとして保存（7日以内のデータは再取得しない）
usage: python3 scrape_prev_data.py [YYYYMMDD]
"""

import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

from scraper import load_cookies, save_cookies, is_logged_in, login, load_env

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
ACCESS_INTERVAL = 2
HORSE_DB_PATH = BASE_DIR / "output" / "horse_db.json"
STALE_DAYS = 7


def load_horse_db() -> dict:
    if HORSE_DB_PATH.exists():
        with open(HORSE_DB_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_horse_db(db: dict) -> None:
    HORSE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HORSE_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def is_cache_fresh(entry: dict, date: str) -> bool:
    scraped_at = entry.get("scraped_at", "")
    if not scraped_at:
        return False
    try:
        d_scraped = datetime.strptime(scraped_at, "%Y%m%d")
        d_target  = datetime.strptime(date, "%Y%m%d")
        return abs((d_target - d_scraped).days) <= STALE_DAYS
    except Exception:
        return False


def scrape_horse_prev(page, horse_id: str) -> dict:
    url = f"https://db.netkeiba.com/horse/{horse_id}/"
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(2)
    soup = BeautifulSoup(page.content(), "html.parser")

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
    results_path = BASE_DIR / "output" / date / "race_results.json"
    if not results_path.exists():
        logger.error(f"race_results.json が見つかりません: {results_path}")
        sys.exit(1)

    with open(results_path, encoding="utf-8") as f:
        race_results = json.load(f)

    # 全 horse_id を収集
    horse_ids = {}
    for race_label, horses in race_results.items():
        for h in horses:
            hid = h.get("horse_id", "")
            name = h.get("name", "")
            if hid and hid not in horse_ids:
                horse_ids[hid] = name

    logger.info(f"収集対象: {len(horse_ids)}頭")

    # horse_db ロード（グローバルキャッシュ）
    horse_db = load_horse_db()
    logger.info(f"horse_db: {len(horse_db)}頭 キャッシュ済み")

    # 新規/期限切れの馬を特定
    need_scrape = {
        hid: name for hid, name in horse_ids.items()
        if hid not in horse_db or not is_cache_fresh(horse_db[hid], date)
    }
    logger.info(f"新規取得が必要: {len(need_scrape)}頭 (キャッシュ利用: {len(horse_ids) - len(need_scrape)}頭)")

    if not need_scrape:
        logger.info("全馬キャッシュ済み。スクレイプ不要。")
        # per-date prev_data.json も出力
        _write_per_date(horse_ids, horse_db, date)
        return

    email, password = load_env()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        if load_cookies(context) and is_logged_in(context.new_page()):
            logger.info("cookies でログイン済み")
            page = context.new_page()
        else:
            page = context.new_page()
            login(page, email, password)
            save_cookies(context)

        total = len(need_scrape)
        for i, (hid, name) in enumerate(need_scrape.items(), 1):
            logger.info(f"[{i}/{total}] {name} ({hid})")
            try:
                data = scrape_horse_prev(page, hid)
                data["scraped_at"] = date
                horse_db[hid] = data
                logger.info(f"  → 前走: {data.get('prev_date','')} 人気{data.get('prev_pop','')} 着順{data.get('prev_rank','')} 指数{data.get('prev_idx','')}")
            except Exception as e:
                logger.error(f"  エラー: {e}")
                horse_db[hid] = {"scraped_at": date}

            # 50頭ごとに中間保存
            if i % 50 == 0:
                save_horse_db(horse_db)
                logger.info(f"中間保存: horse_db {len(horse_db)}頭")

            time.sleep(ACCESS_INTERVAL)

        browser.close()

    save_horse_db(horse_db)
    logger.info(f"完了: horse_db {len(horse_db)}頭 → {HORSE_DB_PATH}")

    # per-date prev_data.json も出力（後方互換）
    _write_per_date(horse_ids, horse_db, date)


def _write_per_date(horse_ids: dict, horse_db: dict, date: str) -> None:
    """解析対象日の馬のみを抽出して per-date prev_data.json に書き出す"""
    out_path = BASE_DIR / "output" / date / "prev_data.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    per_date = {hid: horse_db[hid] for hid in horse_ids if hid in horse_db}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(per_date, f, ensure_ascii=False, indent=2)
    logger.info(f"per-date 保存: {out_path} ({len(per_date)}頭)")


if __name__ == "__main__":
    main()
