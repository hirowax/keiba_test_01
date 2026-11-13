"""11/8 の JRA 公式の結果の一覧を取る（2026-11-13。残りのタスク4）。
取るのは、キャッシュ（output/jra_cname_cache.json）にある 11/8 の開催場・日のページ3つだけ（福島・東京・京都。
netkeiba の 11/8 の一覧と同じ開催場）。月ページは取らない。上限 3。netkeiba にはアクセスしない。
取得日時・URL（cname）・SHA-256 を fetch_log.jsonl に、ページを pages/ に残す（pages/ は git に入れない）。

usage: cd ~/Desktop/netkeiba && python3 research/horse_style_check_20261112/fetch_jra_20261108.py
"""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[1]
sys.path.insert(0, str(BASE))
import scrape_jra_payouts as J  # noqa: E402

JST = timezone(timedelta(hours=9))
DATE = "20261108"
EXPECTED = {"03", "05", "08"}
MAX = 3
LOG = HERE / "fetch_log.jsonl"


def main():
    cache = J.load_cache()
    srl = {k.split("_")[1]: v["cname"] for k, v in cache["srl"].items() if k.startswith(f"{DATE}_")}
    if set(srl) != EXPECTED:
        raise SystemExit(f"キャッシュの 11/8 の開催場が想定と違う {sorted(srl)} → 取らない")
    n = 0
    for vv, cname in sorted(srl.items()):
        if n >= MAX:
            raise SystemExit("上限に達した")
        n += 1
        t = datetime.now(JST).isoformat(timespec="seconds")
        html = J.fetch_post(cname)
        b = html.encode("utf-8")
        (HERE / "pages").mkdir(exist_ok=True)
        path = HERE / "pages" / f"jra_srl_{DATE}_{vv}.html"
        path.write_bytes(b)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "jra_srl", "key": f"{DATE}_{vv}", "url": f"{J.BASE_URL} cname={cname}",
                                "fetched_at": t, "sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b),
                                "file": str(path.relative_to(BASE)), "run": "jra_20261108"}, ensure_ascii=False) + "\n")
        if J.is_param_error_page(html):
            raise SystemExit(f"パラメータエラー: {cname}")
        for sde in J.extract_links(html, J.SDE_PATTERN):
            race_id, ymd, v2, rr = J.parse_sde_components(sde)
            cache["sde"].setdefault(race_id, {"cname": sde, "date": ymd})
    J.save_cache(cache)
    print(f"アクセス {n}・{DATE} の結果のリンク {sum(1 for v in cache['sde'].values() if v['date'] == DATE)}件")


if __name__ == "__main__":
    main()
