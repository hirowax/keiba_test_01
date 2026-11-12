"""脚質の設計の確かめの追加の取得（2026-11-13。外部レビュー3回目で認められた範囲だけ）。
取る前にURLと最大のアクセス数を確定し、超えそうなら取らずに止める。

- netkeiba: 11/8 福島11R の結果ページ1つだけ（https://race.netkeiba.com/race/result.html?race_id=202603030211）。
  トップページ・レース一覧には寄らない。上限 1。成功済みの71レースは取り直さない。
  形の異常（parse_result_html の problems）があれば保存しない。保存先は output/20261108/race_results_all.json の
  そのレースだけ（rescrape_results_full.py と同じ形。complete と既存の結果との照合を計算し直す）。
- JRA 公式: 11/7 の結果の一覧。月ごとのページ（キャッシュにある pw01skl00202611/91）1つと、そこに載る 11/7 の
  開催場・日のページ。上限 4。月ページに載る 11/7 の開催場が3つ（東京・京都・福島。netkeiba の一覧と同じ数）でなければ、
  開催場のページは取らずに止める。キャッシュ output/jra_cname_cache.json に足す。
- 取得日時・URL・SHA-256 を fetch_log.jsonl に、ページを pages/ に残す（pages/ は git に入れない）。

usage: cd ~/Desktop/netkeiba && python3 research/horse_style_check_20261112/fetch_additional_20261113.py
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
import rescrape_results_full as rfull  # noqa: E402
import scrape_jra_payouts as J  # noqa: E402
import scrape_results  # noqa: E402

JST = timezone(timedelta(hours=9))
LOG = HERE / "fetch_log.jsonl"
PAGES = HERE / "pages"
NK_RACE = ("20261108", "福島11R", "202603030211")
NK_MAX = 1
JRA_DATE = "20261107"
JRA_MONTH = "202611"
JRA_VENUES_EXPECTED = {"05", "08", "03"}   # netkeiba の 11/7 の一覧（race_results_all.json の race_ids）と同じ
JRA_MAX = 4
count = {"netkeiba": 0, "jra": 0}


def now():
    return datetime.now(JST).isoformat(timespec="seconds")


def log_rec(rec):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def save_page(kind, key, url, html, t):
    PAGES.mkdir(parents=True, exist_ok=True)
    b = html.encode("utf-8")
    path = PAGES / f"{kind}_{key}.html"
    path.write_bytes(b)
    log_rec({"kind": kind, "key": key, "url": url, "fetched_at": t, "sha256": hashlib.sha256(b).hexdigest(),
             "bytes": len(b), "file": str(path.relative_to(BASE)), "run": "additional_20261113"})


def jra_fetch(cname, kind, key):
    if count["jra"] >= JRA_MAX:
        raise SystemExit(f"JRA 公式の上限 {JRA_MAX} に達したので止める（{cname} は取らない）")
    count["jra"] += 1
    t = now()
    html = J.fetch_post(cname)   # 1〜2秒待ってから POST（今の払戻の取得と同じ）
    save_page(kind, key, f"{J.BASE_URL} cname={cname}", html, t)
    if J.is_param_error_page(html):
        raise SystemExit(f"JRA 公式がパラメータエラーを返した: {cname}")
    return html


def fetch_jra():
    cache = J.load_cache()
    month_cname = cache["monthly"][JRA_MONTH]["cname"]
    html = jra_fetch(month_cname, "jra_month", JRA_MONTH)
    srl = {}
    for link in J.extract_links(html, J.SRL_PATTERN):
        vv, yyyy, kk, dd, ymd = J.parse_srl_components(link)
        cache["srl"].setdefault(f"{ymd}_{vv}", {"cname": link})
        if ymd == JRA_DATE:
            srl[vv] = link
    J.save_cache(cache)
    if set(srl) != JRA_VENUES_EXPECTED:
        print(f"月ページの 11/7 の開催場が想定と違う {sorted(srl)} → 開催場のページは取らずに止める")
        return
    for vv, link in sorted(srl.items()):
        h = jra_fetch(link, "jra_srl", f"{JRA_DATE}_{vv}")
        for sde in J.extract_links(h, J.SDE_PATTERN):
            race_id, ymd, v2, rr = J.parse_sde_components(sde)
            cache["sde"].setdefault(race_id, {"cname": sde, "date": ymd})
    J.save_cache(cache)
    n = sum(1 for v in cache["sde"].values() if v["date"] == JRA_DATE)
    print(f"JRA 公式: {JRA_DATE} の結果のリンク {n}件（アクセス {count['jra']}）")


def fetch_netkeiba():
    from playwright.sync_api import sync_playwright
    from scraper import load_cookies
    date, label, race_id = NK_RACE
    path = BASE / "output" / date / rfull.OUT_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    if label in data["races"]:
        print(f"{label} はもう保存されている → 取らない")
        return
    url = scrape_results.RESULT_URL.format(race_id=race_id)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}, locale="ja-JP", timezone_id="Asia/Tokyo")
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = context.new_page()
        if not load_cookies(context):
            raise SystemExit("cookies.json が無い")
        if count["netkeiba"] >= NK_MAX:
            raise SystemExit("netkeiba の上限に達した")
        count["netkeiba"] += 1
        t = now()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(random.uniform(3.0, 7.0))
            html = page.content()
        except Exception as e:  # noqa: BLE001
            log_rec({"kind": "result", "key": race_id, "url": url, "fetched_at": t, "error": f"{type(e).__name__}: {e}",
                     "run": "additional_20261113"})
            browser.close()
            raise SystemExit(f"取れなかった（取り直さない）: {e}")
        browser.close()
    save_page("result", race_id, url, html, t)
    result = scrape_results.parse_result_html(html, label)
    if result["problems"]:
        raise SystemExit(f"形の異常があるので保存しない: {result['problems']}")
    data["races"][label] = {"race_id": race_id, **{k: result[k] for k in ("condition", "horses", "others")},
                            "fetched_at": t, "note": "2026-11-13 追加の取得（1ページ）"}
    data["complete"] = all(l in data["races"] for l in data["race_ids"])
    data["check_against_race_results"] = rfull.check_against_race_results(date, data["races"])
    rfull.save_out(date, data)
    print(f"netkeiba: {label} を保存 → complete={data['complete']}・既存の結果との照合 {data['check_against_race_results']}")


def main():
    fetch_netkeiba()
    fetch_jra()
    log_rec({"kind": "run", "run": "additional_20261113", "finished_at": now(), "count": count})
    print("アクセス数:", count)


if __name__ == "__main__":
    main()
