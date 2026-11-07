"""
タスク1: 馬ページから取った前走のタイム指数（prev_inputs の記録）を、別の出どころである指数の表（前日の speed.html、近走平均の表）の
「前走」の列と、採点したレースの全頭で照合する（2026-11-07。docs/prev_cache_fix_plan_202611.md の案H「次の開催日の実行で…全頭で照合する」）。
ページは取りに行かない。成績は見ない。一致率は列の意味の証明にしない（同じ計画）。

入力（対象日 D）:
- output/D/pickup_scores.json: 採点したレース、馬番→馬ID（horse_id_map）、馬番→正式名（horse_name_full）、取消（scratched_nums）、
  そのレースが使った前走データの記録の実行ID（prev_inputs_run）
- output/D/prev_inputs/{実行ID}.json: 馬ごとの状態と選んだ行（values.idx）
- output/D.xlsx: 近走平均の表。「前走」の列は、見出しの「近5走の指数」から5列（5走前・4走前・3走前・2走前・前走）の最後。
  表が11列で、7列目の見出しが「近5走の指数」、2段目の見出し行の先頭5つが「5走前」〜「前走」のときだけ読む
  （research/prev_data_check_20261104 と同じ確かめ方に、見出しの確認を足した）。
照合: 馬番と馬名（空白を除いて同じ）の両方が合う行だけを同じ馬とする。
分け方（馬ごと）: 一致／不一致／表の「前走」が値なし（「-」など）で記録は数字／記録が数字でない（状態ごと）／照合できない。

実行: python3 research/prev_index_check_20261107/check_prev_vs_table.py 20261107
出力: このフォルダの result_{D}.json・result_{D}.txt
"""
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import scraper  # noqa: E402
from index_table_check import load_index_tables  # noqa: E402

HERE = Path(__file__).resolve().parent
SP = re.compile(r"[\s　]+")
SUB5 = ["5走前", "4走前", "3走前", "2走前", "前走"]
LOG = []


def out(*a):
    LOG.append(" ".join(str(x) for x in a))
    print(*a)


def table_prev(df):
    """馬番 → (馬名, 前走の文字)。形が違えば None"""
    cols = [str(c) for c in df.columns]
    if len(cols) != 11 or "近5走の指数" not in cols or cols.index("近5走の指数") != 6:
        return None
    first = [str(v).strip() for v in df.iloc[0][:5]]
    if first != SUB5:
        return None
    res = {}
    for _, x in df.iloc[1:].iterrows():
        n = str(x["馬番"]).strip()
        if n in res:
            return None
        res[n] = (SP.sub("", str(x["馬名"])), str(x[df.columns[10]]).strip())
    return res


def main():
    d = sys.argv[1]
    pk = json.load(open(ROOT / "output" / d / "pickup_scores.json", encoding="utf-8"))
    tables = load_index_tables(ROOT / "output" / f"{d}.xlsx")
    runs = {}
    kinds, rows, race_skip = Counter(), [], Counter()
    for lab, r in sorted(pk["races"].items()):
        run_id = r.get("prev_inputs_run")
        if not run_id:
            race_skip["前走データの記録が無いレース"] += 1
            continue
        if run_id not in runs:
            runs[run_id] = json.load(open(ROOT / "output" / d / "prev_inputs" / f"{run_id}.json", encoding="utf-8"))
        rec = runs[run_id]["races"].get(lab)
        df = (tables.get(lab) or {}).get("average")
        tp = table_prev(df) if df is not None else None
        if rec is None:
            race_skip["記録にレースが無い"] += 1
            continue
        if tp is None:
            race_skip["近走平均の表が無い・形が違う"] += 1
            continue
        scratched = {str(k) for k in (r.get("scratched_nums") or {})}
        for num, hid in sorted(r["horse_id_map"].items(), key=lambda kv: int(kv[0])):
            if num in scratched:
                kinds["取消・除外（出馬表）"] += 1
                continue
            name = SP.sub("", r["horse_name_full"].get(num, ""))
            h = rec["horses"].get(hid)
            row = {"race": lab, "num": num, "name": name, "horse_id": hid}
            t = tp.get(num)
            if t is None or t[0] != name:
                k = "照合できない（表に馬番が無い・馬名が違う）"
            elif h is None:
                k = "記録に馬が無い"
            else:
                row.update(status=h["status"], prev=h.get("values", {}), table_prev=t[1])
                tk, _ = scraper.classify_index_text(t[1])
                idx = (h.get("values") or {}).get("idx")
                if h["status"] != "取れた" or not isinstance(idx, int):
                    k = f"記録が数字でない（{h['status']}）・表は{'数字' if tk == 'ok' else '値なし'}"
                elif tk != "ok":
                    k = f"記録は数字・表は値なし（{t[1]}）"
                elif int(t[1].rstrip("*")) == idx:
                    k = "一致"
                else:
                    k = "不一致"
            row["kind"] = k
            kinds[k] += 1
            rows.append(row)
    out("対象日", d, "レース", len(pk["races"]), "外したレース", dict(race_skip))
    out("馬ごと", dict(kinds))
    for x in rows:
        if x["kind"] != "一致":
            out("  ", x["race"], x["num"], x["name"], x["kind"], "記録:", x.get("prev"), "表:", x.get("table_prev"))
    files = [ROOT / "output" / d / "pickup_scores.json", ROOT / "output" / f"{d}.xlsx"] + \
        [ROOT / "output" / d / "prev_inputs" / f"{k}.json" for k in runs]
    res = {"date": d, "race_skip": dict(race_skip), "kinds": dict(kinds), "rows": rows,
           "inputs_sha256": {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
           "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (HERE / f"result_{d}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / f"result_{d}.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
