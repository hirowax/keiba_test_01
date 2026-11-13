"""直線のレース（新潟の芝1000m）の通過順の1つの値が、何の地点の位置かを確かめるための取得（2026-11-13。残りのタスク6）。
取るのは 2026-10-25 新潟11R（202604040411）の結果ページ1つだけ。上限 1。トップページ・レース一覧には寄らない。
取得日時・URL・SHA-256 を fetch_log.jsonl に、ページを pages/ に残す（pages/ は git に入れない）。

usage: cd ~/Desktop/netkeiba && python3 research/horse_style_check_20261112/fetch_straight_20261113.py
"""
import hashlib
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[1]
sys.path.insert(0, str(BASE))

JST = timezone(timedelta(hours=9))
RACE_ID = "202604040411"
URL = f"https://race.netkeiba.com/race/result.html?race_id={RACE_ID}"
LOG = HERE / "fetch_log.jsonl"


def main():
    from playwright.sync_api import sync_playwright
    from scraper import load_cookies
    path = HERE / "pages" / f"result_{RACE_ID}.html"
    if path.exists():
        raise SystemExit("もう保存してある → 取らない")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        try:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800}, locale="ja-JP", timezone_id="Asia/Tokyo")
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = context.new_page()
            if not load_cookies(context):
                raise SystemExit("cookies.json が無い")
            t = datetime.now(JST).isoformat(timespec="seconds")
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)   # 1回だけ
            time.sleep(random.uniform(3.0, 7.0))
            html = page.content()
        finally:
            browser.close()
    b = html.encode("utf-8")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "result", "key": RACE_ID, "url": URL, "fetched_at": t,
                            "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b),
                            "file": str(path.relative_to(BASE)), "run": "straight_20261113"}, ensure_ascii=False) + "\n")
    print("保存", path.name, len(b))


if __name__ == "__main__":
    main()
