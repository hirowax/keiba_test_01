"""
タスク8の試し（docs/rebuild_trial_plan_202611.md）: 対象6レースのページを取って、そのまま保存する（2026-11-07 ユーザー承認）。
読み取り・採点はしない（保存した HTML を後で読む）。成績は見ない。

- 対象: selected_races.json の6レース。
- 馬: 採点時点の出走予定の馬（計画の 2.1）。
  - 10/31 以降（11/7・10/31・11/1）: pickup_scores.json の horse_id_map から scratched_nums（出馬表の取消の印）を除いた馬。
  - 10/30 以前（9/12・10/25）: 保存した馬番→馬IDは馬番の取り違えの影響を受けているので使わず、race_results_all.json の全馬
    （着順のある馬と、取消・除外・中止などの馬）の馬IDを使う。取消・除外の馬がいても取る（そのレースは「当時の採点を再現できない」と
    後で記録する。取消・除外の判定は後の読み取りで行う）。
- ページ:
  - 馬ページ https://db.netkeiba.com/horse/{馬ID}/ （本番の prev_inputs.fetch_entry と同じ URL・同じ待ち方）
  - 出馬表 https://race.netkeiba.com/race/shutuba.html?race_id={ID}
  - データ分析 https://race.netkeiba.com/race/data_top.html?race_id={ID}&rf=race_submenu
- 取り方: 本番と同じ cookies（自動ログインしない）・ステルス設定。ページごとに 6〜12 秒あける。IP ブロックの画面で止める。
- 保存: pages/horse/{馬ID}.html.gz、pages/race/{レースID}_{shutuba|data_top}.html.gz（一時ファイルから置き換え）。
  保存済みは取り直さない（再開できる）。ログは fetch_log.jsonl（日時・URL・保存先・sha256）。
- ログインの確認: 馬ページごとに、成績の表（table.db_h_race_results）の中にプレミアム登録の案内のリンクがあれば止める
  （ログインしていない表示。tests/fixtures のログインした状態のページでは表の中に0個、ログアウトの状態では行ごとにある）。

実行: python3 research/rebuild_trial_202611/fetch_pages.py
"""
import gzip
import hashlib
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import scraper  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

HERE = Path(__file__).resolve().parent
PAGES = HERE / "pages"
LOG = HERE / "fetch_log.jsonl"
FIXED_FROM = "20261031"   # 馬番の取り違えを直した後の最初の開催日


def log(**kw):
    kw["at"] = datetime.now().isoformat(timespec="seconds")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def horses_for(race):
    d, lab = race["date"], race["label"]
    if d >= FIXED_FROM:
        r = json.load(open(ROOT / "output" / d / "pickup_scores.json", encoding="utf-8"))["races"][lab]
        scr = {str(k) for k in (r.get("scratched_nums") or {})}
        return [h for n, h in r["horse_id_map"].items() if n not in scr and h], "pickup_scores の horse_id_map（取消の印を除く）"
    r = json.load(open(ROOT / "output" / d / "race_results_all.json", encoding="utf-8"))["races"][lab]
    return [str(h["horse_id"]) for h in r["horses"] + r.get("others", []) if h.get("horse_id")], "race_results_all の全馬"


def targets():
    races = json.load(open(HERE / "selected_races.json", encoding="utf-8"))
    out = []
    for race in races:
        rid = race["race_id"]
        out.append(("race", f"https://race.netkeiba.com/race/shutuba.html?race_id={rid}", PAGES / "race" / f"{rid}_shutuba.html.gz", race))
        out.append(("race", f"https://race.netkeiba.com/race/data_top.html?race_id={rid}&rf=race_submenu",
                    PAGES / "race" / f"{rid}_data_top.html.gz", race))
        hs, src = horses_for(race)
        log(event="horses", date=race["date"], label=race["label"], n=len(hs), source=src)
        for hid in hs:
            out.append(("horse", f"https://db.netkeiba.com/horse/{hid}/", PAGES / "horse" / f"{hid}.html.gz", race))
    seen, uniq = set(), []
    for t in out:
        if t[2] not in seen:
            seen.add(t[2])
            uniq.append(t)
    return uniq


def save(path, html):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        f.write(html)
    tmp.replace(path)


def main():
    tg = targets()
    todo = [t for t in tg if not t[2].exists()]
    log(event="start", pages_total=len(tg), pages_todo=len(todo))
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
        fails = 0
        for kind, url, path, race in todo:
            try:
                page.goto(url, wait_until="domcontentloaded")
                time.sleep(random.uniform(2.0, 4.0))
                html = page.content()
            except Exception as e:
                log(event="error", url=url, error=str(e)[:200])
                fails += 1
                if fails >= 5:
                    log(event="stop", reason="続けて5回失敗")
                    break
                continue
            fails = 0
            if scraper.is_ip_blocked(page):
                log(event="stop", reason="IPブロックの画面", url=url)
                break
            if kind == "horse":
                t = BeautifulSoup(html, "html.parser").find("table", class_="db_h_race_results")
                stars = len(t.select('a[href*="pid=premium"]')) if t else 0
                if stars:
                    log(event="stop", reason=f"馬ページにログインしていない表示（成績の表の中にプレミアム登録の案内 {stars} 個）", url=url)
                    break
            save(path, html)
            log(event="page", kind=kind, url=url, path=str(path.relative_to(ROOT)), date=race["date"], label=race["label"],
                sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(), bytes=len(html))
            time.sleep(random.uniform(6.0, 12.0))
        browser.close()
    left = sum(1 for t in tg if not t[2].exists())
    log(event="end", pages_left=left)


if __name__ == "__main__":
    main()
