#!/usr/bin/env python3
"""
ブラウザを表示してログインし、cookies.json を保存するスクリプト。
実行後、ブラウザが開くので手動でログインして Enter を押してください。
"""
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

COOKIES_FILE = Path("cookies.json")
TARGET_URL = "https://regist.netkeiba.com/account/?pid=login"
VERIFY_URL = "https://race.netkeiba.com/race/speed.html?race_id=202606020709&type=rank&mode=average"

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print("ブラウザを開いています...")
        page.goto(TARGET_URL)
        print("\n▶ ブラウザでログインしてください。")
        print("  ログイン完了後、このターミナルで Enter を押してください。")
        input()

        # タイム指数ページにアクセスして認証確認
        print("タイム指数ページで認証確認中...")
        page.goto(VERIFY_URL, wait_until="domcontentloaded")
        time.sleep(3)

        if "premium_new" in page.url:
            print("❌ タイム指数ページにアクセスできません。プレミアム会員でログインされているか確認してください。")
        else:
            print("✅ タイム指数ページにアクセスできました！")
            cookies = context.cookies()
            COOKIES_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
            print(f"✅ cookies.json を保存しました（{len(cookies)} 件）")

        browser.close()

if __name__ == "__main__":
    main()
