"""
タスク3: 指数の表が馬番1〜3だけだった6日（2025-11-02、2026-01-10・11・17・18・24）について、
いまも全頭の表が取れるかを1レースで試す（2026-11-06）。

- 対象: 2026-01-10 中山11R（race_id 202606010311、結果は17頭）。
- 本番と同じ取り方（scraper.SPEED_URL の type=rank、cookies のみ、ステルス設定）で3つの指数のページを開き、
  HTML を保存して scraper.parse_rank_html で読む。
- 照合: 表の馬番・馬名を、取り直した結果（race_results_all.json）の全馬と比べる。値は classify_index_text で分類する。
- 既存の output/20260110.xlsx は書き換えない。保存先はこのフォルダだけ。

実行: python3 research/task3_index_refetch_20261106/try_one_race.py [--offline]
（2026-11-06 は1回だけページを取り、数え方を直した後は --offline で保存した HTML を読み直した）
"""
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import scraper  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

HERE = Path(__file__).resolve().parent
DATE, LABEL, RACE_ID = "20260110", "中山11R", "202606010311"
MODES = ["average", "distance", "course"]
scraper_SUB = ("5走前", "着度数")
OFFLINE = "--offline" in sys.argv   # 保存した HTML を読み直すだけ（ページを取りに行かない）


def norm(s):
    return re.sub(r"\s+", "", str(s or ""))


def read_mode(mode, html, truth):
    df = scraper.parse_rank_html(html)
    rec = {"parsed": df is not None}
    if df is not None:
        rec["valid_rank_table"] = bool(scraper._is_valid_speed_df(df))
        rows = []
        for _, r in df.iterrows():
            vals = list(r.values)
            if str(vals[0]) in scraper_SUB:   # 2段目の見出し行（「5走前」「着度数」）。馬の行は順位が「N位」か「-」
                continue
            if not (re.fullmatch(r"\d+位", str(vals[0])) or str(vals[0]) == "-"):
                raise SystemExit(f"{mode}: 順位の形が分からない行 {vals[:6]}")
            rows.append({"rank": str(vals[0]), "num": str(vals[2]), "name": str(vals[4]),
                         "value": str(vals[5]), "class": scraper.classify_index_text(vals[5])[0]})
        nums = [x["num"] for x in rows]
        rec["rows"] = rows
        rec["n_rows"] = len(rows)
        rec["missing"] = sorted(set(truth) - set(nums), key=int)
        rec["extra"] = sorted(set(nums) - set(truth), key=lambda x: int(x) if x.isdigit() else 999)
        rec["dup"] = sorted({n for n in nums if nums.count(n) > 1})
        rec["name_mismatch"] = [x["num"] for x in rows if x["num"] in truth and norm(x["name"]) != norm(truth[x["num"]])]
        rec["classes"] = {c: sum(1 for x in rows if x["class"] == c) for c in {x["class"] for x in rows}}

    return rec


def finish(out):
    (HERE / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for m, r in out["modes"].items():
        print(m, {k: v for k, v in r.items() if k != "rows"})


def main():
    res = json.load(open(ROOT / "output" / DATE / "race_results_all.json", encoding="utf-8"))["races"][LABEL]
    assert res["race_id"] == RACE_ID
    truth = {str(h["num"] if "num" in h else h.get("umaban")): h.get("name") or h.get("horse_name")
             for h in res.get("horses", []) + res.get("others", [])}
    out = {"date": DATE, "label": LABEL, "race_id": RACE_ID, "result_horses": len(truth), "modes": {}}
    if OFFLINE:
        for mode in MODES:
            out["modes"][mode] = read_mode(mode, (HERE / f"{RACE_ID}_{mode}.html").read_text(encoding="utf-8"), truth)
        finish(out)
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
        for mode in MODES:
            url = scraper.SPEED_URL.format(race_id=RACE_ID, mode=mode)
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_selector("table", timeout=15000)
            except Exception:
                pass
            time.sleep(random.uniform(3.0, 5.0))
            html = page.content()
            (HERE / f"{RACE_ID}_{mode}.html").write_text(html, encoding="utf-8")
            rec = read_mode(mode, html, truth)
            rec["url"] = url
            out["modes"][mode] = rec
            time.sleep(random.uniform(5.0, 9.0))
        browser.close()
    finish(out)


if __name__ == "__main__":
    main()
