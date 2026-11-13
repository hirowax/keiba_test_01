"""4-1: 結果（race_results.json）の1着の馬（同着なら全員）の確定オッズ×100 と、JRA公式（payouts_jra.json）の単勝払戻を、馬番の組と金額で照合する。"""
import json, collections, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import OUT
import sys as _sys  # 2026-11-13〜: 結果の保存が不完全な日があれば止める
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from data_quality import stop_if_any_results_save_incomplete
stop_if_any_results_save_incomplete()
c = collections.Counter(); bad = []
for pj in sorted(OUT.glob("*/payouts_jra.json")):
    d = pj.parent.name; rp = OUT / d / "race_results.json"
    if not rp.exists(): c["結果ファイルが無い日"] += 1; continue
    res = json.load(open(rp))
    for race, p in json.load(open(pj)).items():
        if not isinstance(p, dict) or "tansho" not in p: c["払戻に単勝が無いレース"] += 1; continue
        rows = res.get(race)
        if not rows: c["結果にレースが無い・0頭のレース"] += 1; bad.append({"日": d, "レース": race, "種類": "結果にレースが無い・0頭"}); continue
        win = [h for h in rows if h.get("rank") == 1]
        c["照合したレース"] += 1
        jra = p["tansho"]
        nk = {h["num"]: (round(h["odds"] * 100) if h.get("odds") is not None else None) for h in win}
        if set(jra) != set(nk):
            c["1着の馬番の組が違うレース"] += 1; bad.append({"日": d, "レース": race, "種類": "1着の馬番の組が違う", "JRA": jra, "結果": nk}); continue
        diff = [n for n in jra if nk[n] != jra[n]]
        if diff:
            c["金額が違うレース"] += 1; c["金額が違う馬"] += len(diff)
            bad.append({"日": d, "レース": race, "種類": "金額が違う", "1着の頭数": len(win), "JRA": jra, "オッズx100": nk})
        c["1着の馬"] += len(jra); c["金額が一致した馬"] += len(jra) - len(diff)
summary = dict(c)
json.dump({"summary": summary, "detail": bad}, open(Path(__file__).parent / "results/tansho.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1)); [print(b) for b in bad if b["種類"] != "結果にレースが無い・0頭"]
