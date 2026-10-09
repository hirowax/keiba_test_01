#!/usr/bin/env python3
"""result.html の実際のHTML構造を確認する"""
import json
import time
import random
from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).parent
from scraper import load_cookies, human_sleep

# 4/11 中山10R
RACE_ID = "202606030510"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True,
        args=["--disable-blink-features=AutomationControlled"])
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
        locale="ja-JP", timezone_id="Asia/Tokyo",
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = context.new_page()
    load_cookies(context)

    url = f"https://race.netkeiba.com/race/result.html?race_id={RACE_ID}"
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(random.uniform(4, 7))

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # 修正後のパースをテスト
    import sys
    sys.path.insert(0, str(BASE_DIR))
    from scrape_results import scrape_race_result

    result = scrape_race_result(page, RACE_ID, "中山10R")
    if result:
        for h in result["horses"][:5]:
            print(f"  {h['rank']}着 {h['num']}番 {h['name']} "
                  f"騎手:{h['jockey']} 厩舎:{h['trainer']} "
                  f"人気:{h['pop']} オッズ:{h['odds']} 斤量:{h['weight_carried']}")

    browser.close()
