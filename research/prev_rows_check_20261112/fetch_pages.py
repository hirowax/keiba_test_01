"""
前走データの読み取りの修正（障害戦の前走・着順「取」・初出走の馬）の前に、実ページを確かめる（2026-11-12）。
外部レビュー（2026-11-12）の指示: 必要なページを絞り、取得日時・URL・ハッシュを残し、生の HTML は公開リポジトリに入れない
（このフォルダの *.html は .gitignore の research/**/*.html で外れる）。採点・記録（prev_inputs/）には使わない。

取るページ（順に。ページの間は 6〜10 秒待つ）:
  1. 着順「取」の行のレースの結果のページ（浦和 2026/07/16 9R。馬 2023100770 チェストー）
  2. 前走が障害戦で、指数が空だった3頭の馬ページ（11/7・11/8 の東京8R）
  3. 存在しない馬IDのページ（異常なページの見え方を知るため）
  4. 11/14 のレース一覧 → 新馬戦があれば、その出馬表 → 出走予定の馬2頭の馬ページ（初出走の馬のページの見え方）

実行: python3 research/prev_rows_check_20261112/fetch_pages.py
記録: fetch_log.jsonl（URL・開いた後の URL・取得日時・SHA-256・バイト数・ページの題名）
"""
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import scraper  # noqa: E402

HERE = Path(__file__).resolve().parent
LOG = HERE / "fetch_log.jsonl"
JST = timezone(timedelta(hours=9))

FIXED = [
    ("race_取", "https://db.netkeiba.com/race/202642071609/"),
    ("horse_障害_2020100233", "https://db.netkeiba.com/horse/2020100233/"),
    ("horse_障害_2019101351", "https://db.netkeiba.com/horse/2019101351/"),
    ("horse_障害_2022106999", "https://db.netkeiba.com/horse/2022106999/"),
    ("horse_存在しない_0000000000", "https://db.netkeiba.com/horse/0000000000/"),
]
RACE_LIST = "https://race.netkeiba.com/top/race_list_sub.html?kaisai_date=20261114"


def save(page, name, url):
    started = datetime.now(JST).isoformat(timespec="seconds")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(random.uniform(3.0, 5.0))
        html = page.content()
        err = None
    except Exception as ex:
        html, err = "", f"{type(ex).__name__}: {ex}"
    path = HERE / f"{name}.html"
    path.write_text(html, encoding="utf-8")
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    rec = {"name": name, "url": url, "final_url": page.url, "started_at": started,
           "fetched_at": datetime.now(JST).isoformat(timespec="seconds"),
           "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(), "bytes": len(html.encode("utf-8")),
           "title": (m.group(1).strip() if m else None), "error": err, "file": path.name}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False))
    time.sleep(random.uniform(6.0, 10.0))
    return html


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 800}, locale="ja-JP", timezone_id="Asia/Tokyo")
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        if not scraper.load_cookies(context):
            raise SystemExit("cookies.json が無い")
        page = context.new_page()
        for name, url in FIXED:
            save(page, name, url)
        # 4. 初出走の馬
        html = save(page, "race_list_20261114", RACE_LIST)
        shinba = []
        for m in re.finditer(r'<li class="RaceList_DataItem[^"]*">(.*?)</li>', html, re.S):
            block = m.group(1)
            rid = re.search(r"race_id=(\d{12})", block)
            if rid and "新馬" in block:
                shinba.append(rid.group(1))
        print("新馬戦の race_id:", shinba)
        if not shinba:
            print("11/14 の新馬戦が一覧に無い（出馬表が出ていない可能性）。4 は後で取る")
        else:
            rid = shinba[0]
            sh = save(page, f"shutuba_{rid}", f"https://race.netkeiba.com/race/shutuba.html?race_id={rid}")
            ids = []
            for hid in re.findall(r"db\.netkeiba\.com/horse/(\d{10})", sh):
                if hid not in ids:
                    ids.append(hid)
            print("出馬表の馬ID:", ids)
            for hid in ids[:2]:
                save(page, f"horse_初出走予定_{hid}", f"https://db.netkeiba.com/horse/{hid}/")
        browser.close()


if __name__ == "__main__":
    main()
