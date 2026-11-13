"""4-2 追加: 人気が食い違ったレースの「同じ単勝オッズの馬の組」ごとに、組の全馬について結果の人気の集まりと馬ページの前走人気の集まりが同じか（重複・欠けが無いか）を確かめる。
馬ページの値は、prev_data.json のうち（前走日, 馬ID）が一致するものすべてを集める（取得日が違うと複数あり得る）。
あわせて「前走好走」（前走1〜6番人気かつ1〜3着。race_pickup.py の加点）の判定が、結果ページの人気と馬ページの人気で変わるかを数える。"""
import json, collections, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import OUT
import sys as _sys  # 2026-11-13〜: 結果の保存が不完全な日があれば止める
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from data_quality import stop_if_any_results_save_incomplete
stop_if_any_results_save_incomplete()
prev = collections.defaultdict(set)
for pp in sorted(OUT.glob("*/prev_data.json")):
    for hid, v in json.load(open(pp)).items():
        pd = (v.get("prev_date") or "").replace("/", "")
        if pd: prev[(pd, hid)].add((str(v.get("prev_pop")), str(v.get("prev_rank"))))
c = collections.Counter(); out = []; groups = set()
for (pd, hid), vals in prev.items():
    p = OUT / pd / "race_results.json"
    if not p.exists(): continue
    for race, rows in json.load(open(p)).items():
        me = [h for h in rows if h["horse_id"] == hid]
        if not me: continue
        me = me[0]
        if len(vals) > 1: c["同じ馬・同じ前走日で値が複数"] += 1
        if any(pp != str(me["pop"]) for pp, _ in vals): groups.add((pd, race, me["odds"]))
        for pp, rk in vals:
            a = me["rank"] <= 3 and me["pop"] <= 6
            b = rk.isdigit() and pp.isdigit() and int(rk) <= 3 and int(pp) <= 6
            c["前走好走の判定: 同じ" if a == b else "前走好走の判定: 変わる"] += 1
            if a != b: out.append({"種類": "前走好走の判定が変わる", "前走日": pd, "レース": race, "馬ID": hid})
c["人気が食い違った組"] = len(groups)
for pd, race, odds in sorted(groups):
    rows = json.load(open(OUT / pd / "race_results.json"))[race]
    g = [h for h in rows if h["odds"] == odds]
    page = [sorted(int(x) for x, _ in prev.get((pd, h["horse_id"]), ()) if x.isdigit()) for h in g]
    row = {"前走日": pd, "レース": race, "単勝オッズ": odds, "馬番と結果の人気": [[h["num"], h["pop"]] for h in g], "馬ページの人気": page}
    if not all(len(v) == 1 for v in page):
        c["組の一部の馬に前走人気が無い（確かめられない）"] += 1; row["種類"] = "前走人気がそろわない"
    elif sorted(v[0] for v in page) == sorted(h["pop"] for h in g):
        c["組の人気の集まりが同じ（入れ替わりだけ）"] += 1; row["種類"] = "入れ替わりだけ"
    else:
        c["組の人気の集まりが違う"] += 1; row["種類"] = "集まりが違う"
    out.append(row)
summary = dict(c)
json.dump({"summary": summary, "detail": out}, open(Path(__file__).parent / "results/prev_pop_groups.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1))
