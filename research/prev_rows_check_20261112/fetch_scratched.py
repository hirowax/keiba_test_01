"""結果の記録（race_results_all.json）で「取消」の中央の馬の馬ページを取り、その行の着順の表記を確かめる（2026-11-12）。
fetch_pages.py と同じ設定・記録（fetch_log.jsonl）。2024106490 は新馬戦で取消になった馬（「取」の行しか無いページの見え方）。
実行: python3 research/prev_rows_check_20261112/fetch_scratched.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import fetch_pages as fp  # noqa: E402
import scraper  # noqa: E402

HORSES = ["2023100582", "2022106199", "2024102799", "2024106490"]


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
        for hid in HORSES:
            fp.save(page, f"horse_取消_{hid}", f"https://db.netkeiba.com/horse/{hid}/")
        browser.close()


if __name__ == "__main__":
    main()
