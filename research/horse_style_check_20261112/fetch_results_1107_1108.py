"""脚質の設計 5章の3a（docs/horse_style_design_202611.md）: 11/7・11/8 の全レースの結果の取り直し。
位置付けは「入力の補完と取得量の参考測定」。新しい取得処理の確かめではない。

今の rescrape_results_full.py をそのまま動かす（待ち時間・連続5回の失敗で止まる条件は変えない）。
ページを取る2つの関数（レース一覧・結果）だけを包み、取得日時・URL・SHA-256 を fetch_log.jsonl に残し、
ページを pages/ に保存する（research/*/pages/ は gitignore 済み。HTML・cookies は公開しない）。
保存先は rescrape_results_full.py と同じ output/{date}/race_results_all.json（新しいファイル。既存のファイルは書き換えない）。

usage: cd ~/Desktop/netkeiba && python3 research/horse_style_check_20261112/fetch_results_1107_1108.py
"""
import hashlib
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))
import rescrape_results_full as rfull  # noqa: E402
import scrape_results  # noqa: E402
import scraper  # noqa: E402

HERE = Path(__file__).resolve().parent
PAGES = HERE / "pages"
LOG = HERE / "fetch_log.jsonl"
JST = timezone(timedelta(hours=9))
DATES = ["20261107", "20261108"]


def now():
    return datetime.now(JST).isoformat(timespec="seconds")


def save(kind, key, url, html, fetched_at, extra=None):
    PAGES.mkdir(parents=True, exist_ok=True)
    data = html.encode("utf-8")
    path = PAGES / f"{kind}_{key}.html"
    path.write_bytes(data)
    rec = {"kind": kind, "key": key, "url": url, "fetched_at": fetched_at,
           "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "file": str(path.relative_to(BASE))}
    rec.update(extra or {})
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


_orig_get_race_ids = scraper.get_race_ids


def logged_get_race_ids(page, date):
    t = now()
    races = _orig_get_race_ids(page, date)
    save("race_list", date, scraper.RACE_LIST_URL.format(date=date), page.content(), t, {"n_races": len(races)})
    return races


def logged_scrape_race_result(page, race_id, race_label):
    """scrape_results.scrape_race_result と同じ（goto → 3〜7秒待つ → 読む）。ページの保存と記録だけ足す"""
    url = scrape_results.RESULT_URL.format(race_id=race_id)
    t = now()
    try:
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(random.uniform(3.0, 7.0))
    except scrape_results.PlaywrightTimeoutError:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "result", "key": race_id, "url": url, "fetched_at": t,
                                "error": "timeout"}, ensure_ascii=False) + "\n")
        return None
    html = page.content()
    save("result", race_id, url, html, t, {"label": race_label})
    return scrape_results.parse_result_html(html, race_label)


def main():
    for d in DATES:
        if (BASE / "output" / d / rfull.OUT_NAME).exists():
            print(f"{d}: {rfull.OUT_NAME} がすでにある → 止める（上書きしない）")
            sys.exit(1)
    rfull.get_race_ids = logged_get_race_ids
    rfull.scrape_race_result = logged_scrape_race_result
    started = now()
    t0 = time.time()
    sys.argv = ["rescrape_results_full.py", *DATES]
    try:
        rfull.main()
    finally:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "run", "started_at": started, "finished_at": now(),
                                "seconds": round(time.time() - t0)}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
