"""脚質の設計 5章の4（docs/horse_style_design_202611.md）: 結果ページの馬の行の通過順（td.PassageRate）の先頭の値が、
同じ地点（そのレースのコーナーごとの通過順位の表の、最初のコーナー）の順位かを、保存したページで確かめる。
ページは fetch_results_1107_1108.py が 2026-11-12 に取った 11/7・11/8 の結果（pages/。git に入れない。取得日時・URL・SHA-256 は fetch_log.jsonl）。
netkeiba にはアクセスしない。

照らすこと（レースごと・馬ごと）:
  A. 馬の行の値の数 ＝ 表のコーナーのうち、その馬の馬番が載っているコーナーの数
  B. 馬の行の k 番目の値 ＝ 表の k 番目に載っているコーナーでの順位（下の読み方で計算）
     表の読み方: 1つのコーナーの並び「(4,*13)(5,8)-7-3,12-1」で、かっこの組は同じ順位（組の最初の位置）、
     かっこの外は1頭ずつ。「-」「=」は間の差で、順位には関係しない。「*」付きの馬は、その組の中で先に置く
     （*の馬が組の最初の位置、ほかの馬は組の最初の位置＋1）。この読み方は、照らした結果で確かめるもので、決め打ちではない。
  C. 着順の無い馬（中止）について、A・B が成り立つか、先頭の値が表の最初のコーナーの順位か

結果は check_first_corner.json に保存する（件数と、合わなかった馬の一覧）。
usage: cd ~/Desktop/netkeiba && python3 research/horse_style_check_20261112/check_first_corner.py
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[1]
sys.path.insert(0, str(BASE))
from scrape_results import parse_result_html  # noqa: E402


def corner_positions(seq: str) -> dict:
    """1つのコーナーの並び → {馬番: 順位}"""
    pos = {}
    i = 0
    n = 0   # これまでに置いた頭数
    tokens = re.findall(r"\([^)]*\)|\*?\d+", seq)
    for tok in tokens:
        if tok.startswith("("):
            nums = re.findall(r"\*?\d+", tok)
            start = n + 1
            starred = [x for x in nums if x.startswith("*")]
            for x in nums:
                num = x.lstrip("*")
                pos[num] = start if (not starred or x.startswith("*")) else start + 1
            n += len(nums)
        else:
            n += 1
            pos[tok.lstrip("*")] = n
        i += 1
    return pos


def check_page(path: Path) -> dict:
    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    t = soup.find("table", class_="Corner_Num")
    corners = []
    if t:
        for tr in t.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) == 2 and cells[1]:
                corners.append((cells[0], corner_positions(cells[1])))
    parsed = parse_result_html(html, path.stem)
    rows = [("完走", h) for h in parsed["horses"]] + [(o["status"], o) for o in parsed["others"]
                                                     if o["status"] in ("中止", "失格")]
    out = {"race_id": path.stem.replace("result_", ""), "corner_labels": [c[0] for c in corners],
           "horses": 0, "A_ok": 0, "B_ok": 0, "first_ok": 0, "bad": [], "nonfinishers": []}
    for kind, h in rows:
        num = str(h["num"])
        vals = h.get("corners") or []
        listed = [(label, p[num]) for label, p in corners if num in p]
        a = len(vals) == len(listed)
        b = a and all(v == p for v, (_, p) in zip(vals, listed))
        first = bool(vals) and bool(corners) and num in corners[0][1] and vals[0] == corners[0][1][num]
        out["horses"] += 1
        out["A_ok"] += a
        out["B_ok"] += b
        out["first_ok"] += first
        rec = {"num": num, "kind": kind, "row": h.get("corners_text"), "table": listed}
        if kind != "完走":
            out["nonfinishers"].append({**rec, "A": a, "B": b, "first": first})
        if not (a and b and first):
            out["bad"].append(rec)
    return out


def main():
    pages = sorted((HERE / "pages").glob("result_*.html"))
    results = [check_page(p) for p in pages]
    total = Counter()
    for r in results:
        for k in ("horses", "A_ok", "B_ok", "first_ok"):
            total[k] += r[k]
    summary = {
        "pages": len(pages),
        "races_without_corner_table": [r["race_id"] for r in results if not r["corner_labels"]],
        "first_corner_label": dict(Counter(r["corner_labels"][0] for r in results if r["corner_labels"])),
        "totals": dict(total),
        "bad": [{"race_id": r["race_id"], **b} for r in results for b in r["bad"]],
        "nonfinishers": [{"race_id": r["race_id"], **n} for r in results for n in r["nonfinishers"]],
    }
    (HERE / "check_first_corner.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "bad"}, ensure_ascii=False, indent=1))
    print("合わなかった馬:", len(summary["bad"]))
    for b in summary["bad"][:15]:
        print(" ", b)


if __name__ == "__main__":
    main()
