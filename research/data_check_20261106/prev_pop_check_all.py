"""タスク4（2026-11-06）: 前走の人気の照合を、全レースの結果の取り直し（race_results_all.json）で出し直す。
照合の方法は research/data_check_20261103/prev_pop_check.py と同じ（同じ馬ID・同じ日付のレースが結果に1つだけの場合、
同じ（馬ID, 前走日）は prev_data.json を日付順に読んで最初の1回だけ）。違いは結果の読み元だけ:
その日に race_results_all.json があればそれ（着順のある馬）、無ければ race_results.json。
人気が違う馬は、同じレースの同じ単勝オッズの馬の組の人気のどれかか、と、組の全馬に前走の人気があるかを確かめる。"""
import json, collections, sys
from pathlib import Path
import sys as _sys  # 2026-11-13〜: 結果の保存が不完全な日があれば止める
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from data_quality import stop_if_any_results_save_incomplete
stop_if_any_results_save_incomplete()
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output"
res = {}
src = collections.Counter()
for d in sorted(p.name for p in OUT.iterdir() if p.is_dir() and p.name.isdigit()):
    pa, po = OUT / d / "race_results_all.json", OUT / d / "race_results.json"
    if pa.exists():
        src["race_results_all"] += 1
        for race, r in json.load(open(pa))["races"].items():
            rows = r["horses"]
            for h in rows: res.setdefault((d, h.get("horse_id")), []).append((race, h, rows))
    elif po.exists():
        src["race_results"] += 1
        for race, rows in json.load(open(po)).items():
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
        bad.append({"前走日": pd, "レース": race, "馬ID": hid, "馬名": h.get("name"), "結果の人気": h.get("pop"),
                    "馬ページの人気": v.get("prev_pop"), "単勝オッズ": h.get("odds"), "同じオッズの組の結果の人気": pops,
                    "組の中か": inside, "読んだprev_data": pp.parent.name})
summary = dict(c, 結果の読み元の日数=dict(src))
json.dump({"summary": summary, "detail": bad}, open(Path(__file__).parent / "results/prev_pop_all.json", "w"),
          ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1))
