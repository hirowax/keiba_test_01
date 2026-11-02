#!/usr/bin/env python3
"""
過去の開催日の「全レース」の結果を取り直す（2026-11-02〜。docs/index_top3_definition_202611.md）。

これまでの race_results.json は、当時候補がいたレースだけを取っていて、着順が数字でない馬
（取消・除外・中止・失格）も捨てていた。3指数の候補の定義を変えたときの比較には、全レースの結果と
着順が数字でない馬の状態が要る。

- 保存先は output/{date}/race_results_all.json（既存の race_results.json は書き換えない）
  {"date", "complete", "races": {label: {"race_id", "condition", "horses", "others"}},
   "check_against_race_results": {...}}
- 1レースごとに保存するので、途中で止めても続きから再開できる（complete の日は飛ばす）
- 連続で失敗したら止める（IPブロック等を疑う）
- 血統は取らない

使い方:
  python3 rescrape_results_full.py 20261031 20261101     # 日付指定
  python3 rescrape_results_full.py --range 20250920 20261101 --max-races 250
"""
import argparse
import json
import logging
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from scraper import get_race_ids, human_sleep, load_cookies
from scrape_results import scrape_race_result

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"
OUT_NAME = "race_results_all.json"
MAX_CONSECUTIVE_FAIL = 5


def target_dates(start: str, end: str) -> list:
    """指数ファイル（output/YYYYMMDD.xlsx）がある日"""
    return sorted(f.stem for f in OUTPUT_DIR.glob("*.xlsx")
                  if re.fullmatch(r"\d{8}", f.stem) and start <= f.stem <= end)


def load_out(date: str) -> dict:
    path = OUTPUT_DIR / date / OUT_NAME
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"date": date, "complete": False, "races": {}}


def save_out(date: str, data: dict):
    path = OUTPUT_DIR / date / OUT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    tmp.replace(path)


def check_against_race_results(date: str, races: dict) -> dict:
    """既存の race_results.json と重なるレースで、着順・馬番・馬IDが一致するか"""
    path = OUTPUT_DIR / date / "race_results.json"
    if not path.exists():
        return {"races": 0, "match": 0, "mismatch": []}
    with open(path, encoding="utf-8") as f:
        old = json.load(f)
    key = lambda rows: sorted((int(h["rank"]), str(h["num"]), str(h["horse_id"])) for h in rows)
    res = {"races": 0, "match": 0, "mismatch": []}
    for label, rows in old.items():
        if label not in races:
            continue
        res["races"] += 1
        if key(rows) == key(races[label]["horses"]):
            res["match"] += 1
        else:
            res["mismatch"].append(label)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="*")
    ap.add_argument("--range", nargs=2, metavar=("START", "END"))
    ap.add_argument("--max-races", type=int, default=0, help="この回で取るレース数の上限（0=無制限）")
    args = ap.parse_args()
    dates = args.dates or (target_dates(*args.range) if args.range else [])
    if not dates:
        ap.error("日付か --range を指定してください")
    todo = [d for d in dates if not load_out(d).get("complete")]
    log.info(f"対象 {len(dates)}日・未完了 {len(todo)}日")
    if not todo:
        return

    fetched = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}, locale="ja-JP", timezone_id="Asia/Tokyo",
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        if not load_cookies(context):
            log.error("cookies.json が見つかりません")
            sys.exit(1)
        page.goto("https://www.netkeiba.com/", wait_until="domcontentloaded")
        human_sleep(2.0, 4.0)

        fails = 0
        for date in todo:
            data = load_out(date)
            race_ids = {r["label"]: r["race_id"] for r in get_race_ids(page, date)}
            human_sleep(2.0, 4.0)
            if not race_ids:
                log.error(f"{date}: レース一覧が取れない → 中断")
                break
            data["race_ids"] = race_ids
            left = [l for l in sorted(race_ids) if l not in data["races"]]
            log.info(f"{date}: {len(race_ids)}レース（取得済み {len(race_ids) - len(left)}）")
            for label in left:
                if args.max_races and fetched >= args.max_races:
                    break
                result = scrape_race_result(page, race_ids[label], label)
                if not result or not result["horses"]:
                    fails += 1
                    log.warning(f"  {date} {label}: 取得失敗（連続 {fails}）")
                    if fails >= MAX_CONSECUTIVE_FAIL:
                        log.error("連続で失敗したので中断（IPブロック等を疑う）")
                        save_out(date, data)
                        browser.close()
                        sys.exit(2)
                    human_sleep(10.0, 20.0)
                    continue
                fails = 0
                fetched += 1
                data["races"][label] = {"race_id": race_ids[label], **result}
                save_out(date, data)
                human_sleep(3.0, 6.0)
                if fetched % 20 == 0:
                    human_sleep(15.0, 30.0)
            data["complete"] = all(l in data["races"] for l in race_ids)
            data["check_against_race_results"] = check_against_race_results(date, data["races"])
            save_out(date, data)
            c = data["check_against_race_results"]
            n_others = sum(len(r["others"]) for r in data["races"].values())
            log.info(f"{date}: 完了={data['complete']} 取得{len(data['races'])}/{len(race_ids)}"
                     f" 着順なし{n_others}頭 既存結果と照合 {c['match']}/{c['races']}一致 不一致{c['mismatch']}")
            if args.max_races and fetched >= args.max_races:
                log.info(f"この回の上限 {args.max_races}レースに達したので終了")
                break
        browser.close()


if __name__ == "__main__":
    main()
