"""4-2: 馬ページから列の位置で読んだ前走の値（output/*/prev_data.json の prev_date・prev_rank・prev_pop）を、自前の結果（race_results.json）と照合する。
対応付け: 同じ馬ID・同じ日付のレースが自前の結果に1つだけある場合に限る。同じ（馬ID, 前走日）の組は、prev_data.json を日付順に読んで最初の1回だけ数える。
人気が違う馬について、同じレースに同じ単勝オッズの馬がいて、前走の人気がその馬たちの結果の人気のどれかか、を確かめる。"""
import json, collections, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import OUT
res = {}
for d in sorted(p.name for p in OUT.iterdir() if p.is_dir() and p.name.isdigit()):
    p = OUT / d / "race_results.json"
    if p.exists():
        for race, rows in json.load(open(p)).items():
            for h in rows: res.setdefault((d, h.get("horse_id")), []).append((race, h, rows))
c = collections.Counter(); bad = []; seen = set()
for pp in sorted(OUT.glob("*/prev_data.json")):
    for hid, v in json.load(open(pp)).items():
        pd = (v.get("prev_date") or "").replace("/", "")
        if not pd or (hid, pd) in seen: continue
        seen.add((hid, pd))
        m = res.get((pd, hid))
        if not m: c["前走が自前の結果に無い"] += 1; continue
        if len(m) > 1: c["同じ日に同じ馬IDが複数"] += 1; continue
        race, h, rows = m[0]; c["照合した馬"] += 1
        c["着順が一致"] += str(h.get("rank")) == str(v.get("prev_rank"))
        if str(h.get("pop")) == str(v.get("prev_pop")): c["人気が一致"] += 1; continue
        same = [x for x in rows if x.get("odds") == h.get("odds")]
        pops = sorted(x["pop"] for x in same)
        inside = len(same) > 1 and str(v.get("prev_pop")).isdigit() and int(v["prev_pop"]) in pops
        c["人気が違う: 同じオッズの組の人気のどれか" if inside else "人気が違う: 同じオッズの組で説明できない"] += 1
        bad.append({"前走日": pd, "レース": race, "馬ID": hid, "馬名": h.get("name"), "結果の人気": h.get("pop"), "馬ページの人気": v.get("prev_pop"),
                    "単勝オッズ": h.get("odds"), "同じオッズの組の結果の人気": pops, "組の中か": inside, "読んだprev_data": pp.parent.name})
summary = dict(c)
json.dump({"summary": summary, "detail": bad}, open(Path(__file__).parent / "results/prev_pop.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1))
