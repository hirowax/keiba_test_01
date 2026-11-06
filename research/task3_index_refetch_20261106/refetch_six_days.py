"""
タスク3: 指数の表が馬番1〜3だけだった6日の全レースについて、3つの指数の表を取り直す（2026-11-06 ユーザー承認）。

- 対象: 2025-11-02、2026-01-10・11・17・18・24 の、race_results_all.json にある全レース（156レース・468ページ）。
- 取り方: try_one_race.py と同じ（scraper.SPEED_URL の type=rank、cookies のみ、ステルス設定）。
- 保存: このフォルダの pages/{date}/{race_id}_{mode}.html.gz（取った HTML そのもの）。既存の output/{date}.xlsx は書き換えない。
  取ったページがあれば取り直さない（再開できる）。
- 照合は check_six_days.py（ページを取りに行かない）で、保存した HTML を読んで行う。
- 安全弁:
  - 開始時に1ページ目で「N位」の表が読めなければ止める（ログアウト・ブロック）。
  - IP ブロックの画面（scraper.is_ip_blocked）が出たら止める。
  - 表が読めない・ランキング表でないページが続けて5回出たら止める。
  - 表の馬の行が3行以下のページは、ログアウト時のプレビュー版の疑いとして止める（実際に3頭以下のレースは無い）。
- 間隔: ページごとに 6〜12 秒、10レースごとに 90〜150 秒休む。

実行: python3 research/task3_index_refetch_20261106/refetch_six_days.py
"""
import gzip
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import scraper  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

HERE = Path(__file__).resolve().parent
PAGES = HERE / "pages"
DAYS = ["20251102", "20260110", "20260111", "20260117", "20260118", "20260124"]
MODES = ["average", "distance", "course"]
SUB = ("5走前", "着度数")
LOG = HERE / "refetch_log.jsonl"


def log(**kw):
    kw["at"] = datetime.now().isoformat(timespec="seconds")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def horse_rows(df):
    n = 0
    for _, r in df.iterrows():
        v = str(r.values[0])
        if v in SUB:
            continue
        if re.fullmatch(r"\d+位", v) or v == "-":
            n += 1
    return n


def targets():
    out = []
    for d in DAYS:
        races = json.load(open(ROOT / "output" / d / "race_results_all.json", encoding="utf-8"))["races"]
        for label, r in sorted(races.items(), key=lambda kv: kv[1]["race_id"]):
            out.append((d, label, r["race_id"]))
    return out


def main():
    tg = targets()
    todo = [(d, lab, rid, m) for d, lab, rid in tg for m in MODES
            if not (PAGES / d / f"{rid}_{m}.html.gz").exists()]
    log(event="start", races=len(tg), pages_total=len(tg) * len(MODES), pages_todo=len(todo))
    if not todo:
        return
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
        bad_streak = 0
        races_done = set()
        for i, (d, lab, rid, mode) in enumerate(todo):
            url = scraper.SPEED_URL.format(race_id=rid, mode=mode)
            try:
                page.goto(url, wait_until="domcontentloaded")
                try:
                    page.wait_for_selector("table", timeout=15000)
                except Exception:
                    pass
                time.sleep(random.uniform(2.0, 4.0))
                html = page.content()
            except Exception as e:
                log(event="error", date=d, label=lab, race_id=rid, mode=mode, error=str(e)[:200])
                bad_streak += 1
                if bad_streak >= 5:
                    log(event="stop", reason="続けて5回失敗")
                    break
                continue
            if scraper.is_ip_blocked(page):
                log(event="stop", reason="IPブロックの画面", date=d, label=lab, mode=mode)
                break
            df = scraper.parse_rank_html(html)
            valid = bool(df is not None and scraper._is_valid_speed_df(df))
            n = horse_rows(df) if valid else 0
            if valid and n <= 3:
                log(event="stop", reason=f"馬の行が{n}行（プレビュー版の疑い）", date=d, label=lab, mode=mode)
                break
            (PAGES / d).mkdir(parents=True, exist_ok=True)
            tmp = PAGES / d / f".{rid}_{mode}.html.gz.tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                f.write(html)
            tmp.replace(PAGES / d / f"{rid}_{mode}.html.gz")
            log(event="page", date=d, label=lab, race_id=rid, mode=mode, rank_table=valid, horse_rows=n)
            if i == 0 and not valid:
                log(event="stop", reason="最初のページで順位の表が読めない（ログアウト・ブロックの疑い）")
                break
            bad_streak = 0 if valid else bad_streak + 1
            if bad_streak >= 5:
                log(event="stop", reason="ランキング表でないページが続けて5回")
                break
            races_done.add(rid)
            time.sleep(random.uniform(6.0, 12.0))
            if mode == MODES[-1] and len(races_done) % 10 == 0:
                rest = random.uniform(90, 150)
                log(event="rest", seconds=round(rest))
                time.sleep(rest)
        browser.close()
    left = sum(1 for d, lab, rid in tg for m in MODES if not (PAGES / d / f"{rid}_{m}.html.gz").exists())
    log(event="end", pages_left=left)


if __name__ == "__main__":
    main()
