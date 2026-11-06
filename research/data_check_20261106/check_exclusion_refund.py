"""
タスク4の残り: 除外のあった2レースで、除外の馬が発売開始の後に除外されたか（JRA の払戻の「返還」に載るか）を確かめる（2026-11-06）。
既存の payouts_jra.json は、返還の欄を読む処理（2026-11-02）より前に取ったので "refund" が無い。
ここでは JRA 公式のページを取り直して保存し、scrape_jra_payouts.parse_payouts で読む。既存のファイルと CNAME のキャッシュは書き換えない。
実行: python3 research/data_check_20261106/check_exclusion_refund.py
"""
import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import scrape_jra_payouts as sjp  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

OUT = Path(__file__).resolve().parent / "jra_pages_exclusion"
TARGETS = [("20260118", "京都12R"), ("20261018", "京都9R")]


def main():
    cache = copy.deepcopy(sjp.load_cache())   # 書き戻さない
    summary = {}
    for date, label in TARGETS:
        allr = json.load(open(ROOT / "output" / date / "race_results_all.json", encoding="utf-8"))["races"][label]
        race_id = allr["race_id"]
        cname = sjp.ensure_sde_cname(date, race_id[4:6], race_id, cache)
        html = sjp.fetch_post(cname)
        if sjp.is_param_error_page(html):
            raise SystemExit(f"{date} {label}: パラメータエラーのページ")
        (OUT / f"{date}_{label}.html").write_text(html, encoding="utf-8")
        parsed = sjp.parse_payouts(html)
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        notes = sorted(set(m.group(0) for m in re.finditer(r"[^。]{0,80}(除外|取消|返還)[^。]{0,80}", text)))
        excluded = [o for o in allr.get("others", []) if o.get("status") == "除外"]
        summary[f"{date} {label}"] = {
            "race_id": race_id, "cname": cname,
            "excluded_in_results": [(o["num"], o["name"]) for o in excluded],
            "refund": parsed.get("refund"),
            "fukusho": parsed.get("fukusho"), "wakuren": parsed.get("wakuren"),
            "same_as_saved_except_refund": {k: v for k, v in parsed.items() if k != "refund"} ==
                json.load(open(ROOT / "output" / date / "payouts_jra.json", encoding="utf-8"))[label],
            "page_notes": notes,
        }
    (OUT.parent / "results" / "exclusion_refund_20261106.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
