"""
タスク3: refetch_six_days.py で保存した指数の表のページを読み、結果と全頭照合する（ページは取りに行かない）。2026-11-06。

対象: 6日（2025-11-02、2026-01-10・11・17・18・24）の race_results_all.json にある全レース × 3つの指数。
ページごとの分類:
- 順位の表あり: scraper.parse_rank_html で読み、scraper._is_valid_speed_df で順位の表と判定できたもの。
- 新馬戦: 順位の表が無く、ページに「新馬戦にはタイム指数はございません」とある。
- 全馬が値なしの表: 表はあるが「N位」の行が無く、全馬の順位が「-」・値が値なし（その距離・コースの経験が全馬に無い）。全頭の照合はする。
- 表なし（ほか）: 順位の表が無く、上の2つにも当たらない。ページの本文の「タイム指数」の後ろの文を記録する。
- 未取得: ページが保存されていない。
順位の表の検査（CLAUDE.md の表の検査に合わせる）:
- 行: 2段目の見出し行（1列目が「5走前」「着度数」）は読み飛ばす。馬の行は1列目が「N位」か「-」。それ以外の形は問題。
- 結果との照合: 結果の全馬（着順のある馬と、取消・除外・中止などの馬）の馬番が表にあるか（欠け）、表の馬番が結果にあるか（余分）、
  馬番の重複、馬名の違い（空白の違いだけ許す）。
- 値: scraper.classify_index_text で分類。200以上・読めない値は問題。
- 順位の形: scraper.index_read_errors・rank_notes（判定処理と同じもの）。
- 「N位」の行の値が「-」「未経験」などの値なし、「-」の行の値が数字、は問題として数える。
古い表（output/{日}.xlsx。書き換えない）との比較: 古い表にある馬（馬番1〜3）ごとに、古い値と新しい値を並べ、
「古い値 = 1000 + 新しい値」「古い値が1000ちょうどで新しい値が値なし」「古い表で値なし（-）・新しい値も値なし」「それ以外」に分ける。

実行: python3 research/task3_index_refetch_20261106/check_six_days.py
出力: このフォルダの check_result.json・check_result.txt
"""
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "data_check_20261103"))
import scraper  # noqa: E402
from xlsx_blocks import load_day  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

HERE = Path(__file__).resolve().parent
PAGES = HERE / "pages"
DAYS = ["20251102", "20260110", "20260111", "20260117", "20260118", "20260124"]
MODES = ["average", "distance", "course"]
SUB = ("5走前", "着度数")
NEWCOMER_TEXT = "新馬戦にはタイム指数はございません"
LOG = []


def out(*a):
    LOG.append(" ".join(str(x) for x in a))
    print(*a)


def norm(s):
    return re.sub(r"\s+", "", str(s or ""))


def page_note(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "header", "footer", "nav"]):
        t.decompose()
    txt = re.sub(r"\s+", " ", soup.get_text(" "))
    i = txt.find("高精度＆個別ラップで区間毎指数を追加!")
    return txt[i:i + 120] if i >= 0 else txt[:0]


def check_table(df, truth):
    rows, problems = [], []
    for _, r in df.iterrows():
        v = [str(x) for x in r.values]
        if v[0] in SUB:
            continue
        if not (re.fullmatch(r"\d+位", v[0]) or v[0] == "-"):
            problems.append(f"順位の形が分からない行 {v[:6]}")
            continue
        cls = scraper.classify_index_text(v[5])[0]
        rows.append({"rank": v[0], "num": v[2], "name": v[4], "value": v[5], "class": cls})
    nums = [x["num"] for x in rows]
    res = {"n_rows": len(rows),
           "missing": sorted(set(truth) - set(nums), key=int),
           "extra": sorted(set(nums) - set(truth)),
           "dup": sorted({n for n in nums if nums.count(n) > 1}),
           "name_mismatch": [x["num"] for x in rows if x["num"] in truth and norm(x["name"]) != norm(truth[x["num"]])],
           "classes": dict(Counter(x["class"] for x in rows)),
           "ranked_without_value": [x["num"] for x in rows if x["rank"] != "-" and x["class"] != "ok"],
           "dash_with_value": [x["num"] for x in rows if x["rank"] == "-" and x["class"] == "ok"],
           "read_errors": scraper.index_read_errors(df), "rank_notes": scraper.rank_notes(df)}
    oks = [int(x["value"].rstrip("*")) for x in rows if x["class"] == "ok"]
    res["min"], res["max"] = (min(oks), max(oks)) if oks else (None, None)
    for k in ("missing", "extra", "dup", "name_mismatch", "ranked_without_value", "dash_with_value", "read_errors"):
        if res[k]:
            problems.append(k)
    if any(x["class"] not in ("ok", "none") for x in rows):
        problems.append("値が200以上・読めない")
    res["problems"] = problems
    res["rows"] = rows
    return res


def compare_old(old_df, new_rows):
    """古い表（馬番1〜3）の値と新しい値を並べる"""
    new = {x["num"]: x for x in new_rows}
    out_rows = []
    for _, r in old_df.iterrows():
        num, oldv = str(r["馬番"]).strip(), str(r["指数"]).strip()
        nx = new.get(num)
        if nx is None:
            kind = "新しい表に無い"
        else:
            nv = nx["value"]
            nok = nx["class"] == "ok"
            if re.fullmatch(r"\d+", oldv) and nok and int(oldv) == 1000 + int(nv.rstrip("*")):
                kind = "古い値＝1000＋新しい値"
            elif oldv == "1000" and not nok:
                kind = "古い値1000・新しい値なし"
            elif not re.fullmatch(r"\d+", oldv) and not nok:
                kind = "どちらも値なし"
            else:
                kind = "それ以外"
        out_rows.append({"num": num, "old": oldv, "new": nx["value"] if nx else None, "kind": kind})
    return out_rows


def main():
    result = {"races": {}, "summary": {}}
    page_kinds, problem_races, old_kinds = Counter(), [], Counter()
    unknown_notes = Counter()
    for d in DAYS:
        res_all = json.load(open(ROOT / "output" / d / "race_results_all.json", encoding="utf-8"))["races"]
        old = load_day(ROOT / "output" / f"{d}.xlsx")
        old_by_label = {lab: v for (_sh, lab), v in old.items()}
        for label, r in sorted(res_all.items(), key=lambda kv: kv[1]["race_id"]):
            rid = r["race_id"]
            truth = {str(h["num"]): h["name"] for h in r.get("horses", []) + r.get("others", [])}
            cls_name = (r.get("condition") or {}).get("class_name", "")
            rec = {"race_id": rid, "class_name": cls_name, "steeple": "障害" in cls_name,
                   "result_horses": len(truth), "modes": {}}
            for m in MODES:
                f = PAGES / d / f"{rid}_{m}.html.gz"
                if not f.exists():
                    rec["modes"][m] = {"kind": "未取得"}
                    page_kinds["未取得"] += 1
                    continue
                html = gzip.open(f, "rt", encoding="utf-8").read()
                df = scraper.parse_rank_html(html)
                if df is not None and scraper._is_valid_speed_df(df):
                    t = check_table(df, truth)
                    t["kind"] = "順位の表あり"
                    if label in old_by_label and m in old_by_label[label]:
                        t["old_compare"] = compare_old(old_by_label[label][m], t["rows"])
                        old_kinds.update(x["kind"] for x in t["old_compare"])
                    rec["modes"][m] = t
                    if t["problems"]:
                        problem_races.append((d, label, m, t["problems"]))
                elif df is not None and (t := check_table(df, truth))["n_rows"] > 0 and \
                        all(x["rank"] == "-" and x["class"] == "none" for x in t["rows"]):
                    # 表はあるが「N位」の行が無く、全馬が値なし（その距離・コースの経験が全馬に無い。2026-11-06 に4ページで確認）
                    t["kind"] = "全馬が値なしの表"
                    rec["modes"][m] = t
                    if t["problems"]:
                        problem_races.append((d, label, m, t["problems"]))
                elif NEWCOMER_TEXT in html:
                    rec["modes"][m] = {"kind": "新馬戦"}
                else:
                    note = page_note(html)
                    rec["modes"][m] = {"kind": "表なし（ほか）", "note": note}
                    unknown_notes[(note[:60], "障害" in cls_name)] += 1
                page_kinds[rec["modes"][m]["kind"]] += 1
            result["races"][f"{d} {label}"] = rec
    n_races = len(result["races"])
    full = [k for k, v in result["races"].items()
            if all(x.get("kind") in ("順位の表あり", "全馬が値なしの表") and not x["problems"] for x in v["modes"].values())]
    out("対象", n_races, "レース・", n_races * 3, "ページ")
    out("ページの分類", dict(page_kinds))
    out("3つの表とも表があり（全馬が値なしの表を含む）全頭一致・問題なし", len(full), "レース")
    out("問題のある表", len(problem_races))
    for p in problem_races:
        out("  ", p)
    out("表なし（ほか）の本文の例（障害戦か）", {f"{k[0]}…（障害:{k[1]}）": v for k, v in unknown_notes.items()})
    kinds = Counter()
    for v in result["races"].values():
        ks = tuple(sorted({x.get("kind") for x in v["modes"].values()}))
        kinds[(ks, v["steeple"], "新馬" in v["class_name"])] += 1
    out("レースの分類（ページの種類・障害戦・新馬戦）", {str(k): c for k, c in kinds.items()})
    vals = [x for v in result["races"].values() for x in v["modes"].values() if x.get("kind") == "順位の表あり"]
    out("値の範囲", min(x["min"] for x in vals if x["min"] is not None), "〜", max(x["max"] for x in vals if x["max"] is not None))
    out("古い表との比較（馬ごと）", dict(old_kinds))
    others = [(k, m, x) for k, v in result["races"].items() for m, t in v["modes"].items()
              for x in t.get("old_compare", []) if x["kind"] in ("それ以外", "新しい表に無い")]
    for o in others[:30]:
        out("  ", o)
    result["summary"] = {"page_kinds": dict(page_kinds), "full_races": len(full), "problem_tables": problem_races,
                         "old_compare": dict(old_kinds)}
    import hashlib
    import subprocess
    files = sorted(PAGES.glob("*/*.html.gz")) + [ROOT / "output" / d / "race_results_all.json" for d in DAYS] + \
        [ROOT / "output" / f"{d}.xlsx" for d in DAYS]
    result["manifest"] = {"commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip(),
                          "files": {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}
    out("読んだファイル", len(files), "（sha256 は check_result.json の manifest）")
    (HERE / "check_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "check_result.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
