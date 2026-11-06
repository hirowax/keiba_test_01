"""
「除」の扱いの案（docs/prev_excluded_rank_plan_202611.md）のために、11/7 の採点で「除」が最新の行だった2頭の馬ページを保存する（2026-11-06）。
- 2020103876 モンドプリューム（京都11R 1番）、2021104929 ルトゥール（京都9R 2番）
- 本番と同じ cookies・ステルス設定で開き、HTML をそのまま保存する。採点・記録（prev_inputs/）には使わない。
- 保存したページから、対象日（20261107）より前の行を新しい順に、日付・レース名・着順・人気・距離・指数の元の文字で書き出す。

実行: python3 research/prev_excluded_rank_20261106/fetch_two_horses.py [--offline]
"""
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import scraper  # noqa: E402
import prev_inputs as pi  # noqa: E402

HERE = Path(__file__).resolve().parent
HORSES = {"2020103876": "モンドプリューム（11/7 京都11R 1番）", "2021104929": "ルトゥール（11/7 京都9R 2番）"}
TARGET = "20261107"


def rows_of(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="db_h_race_results")
    header = [pi._header_text(th) for th in table.find("tr").find_all("th")]
    col = {k: header.index(v) for k, v in pi.HEADERS.items()}
    out = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        raw = {k: pi._cell_text(tds[i]) for k, i in col.items()}
        out.append(raw)
    return out


def main():
    if "--offline" not in sys.argv:
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
            for hid in HORSES:
                page.goto(f"https://db.netkeiba.com/horse/{hid}/", wait_until="domcontentloaded")
                try:
                    page.wait_for_selector("table.db_h_race_results", timeout=15000)
                except Exception:
                    pass
                time.sleep(random.uniform(3.0, 5.0))
                (HERE / f"horse_{hid}_20261106.html").write_text(page.content(), encoding="utf-8")
                time.sleep(random.uniform(6.0, 10.0))
            browser.close()
    summary = {}
    for hid, name in HORSES.items():
        html = (HERE / f"horse_{hid}_20261106.html").read_text(encoding="utf-8")
        rows = rows_of(html)
        before = [r for r in rows if r["date"].replace("/", "") < TARGET]
        summary[hid] = {"name": name, "rows_total": len(rows), "rows_before_target_newest_first": before[:6],
                        "parse_results_table_now": {k: v for k, v in pi.parse_results_table(html, TARGET).items()
                                                    if k in ("status", "reason", "values")}}
    (HERE / "two_horses.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
