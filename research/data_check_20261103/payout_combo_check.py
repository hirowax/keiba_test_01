"""4-3: JRA公式の払戻（payouts_jra.json）の各券種の組み合わせが、結果（race_results.json）の1〜3着の馬番・枠番から作った組み合わせと一致するかを確かめる。
金額は見ない。3着以内に同着があるレースは外す。
複勝は「払戻に載った馬が、1〜3着の上から順の k 頭（k=払戻の頭数）と一致するか」だけを見る。k が正しいか（発売条件・払戻対象の頭数）は確かめていないので、頭数の分布だけを別に出す。
枠連の発売の有無も確かめていない（着順のある頭数との対応だけを出す）。着順のある頭数は出走頭数ではない（取消・除外・中止・失格の馬は race_results.json に載らない）。"""
import json, collections, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import OUT
c = collections.Counter(); bad = []; need = []
def key(nums, ordered=False): return "-".join(nums if ordered else sorted(nums, key=int))
for pj in sorted(OUT.glob("*/payouts_jra.json")):
    d = pj.parent.name; rp = OUT / d / "race_results.json"
    if not rp.exists(): continue
    res = json.load(open(rp))
    for race, p in json.load(open(pj)).items():
        rows = res.get(race)
        if not rows: c["結果にレースが無い・0頭"] += 1; continue
        c["レース"] += 1
        by = collections.defaultdict(list)
        for h in rows: by[h["rank"]].append(h)
        if any(len(v) > 1 for k, v in by.items() if k <= 3): c["3着以内に同着（外した）"] += 1; continue
        if not all(by[i] for i in (1, 2, 3)): bad.append({"日": d, "レース": race, "種類": "3着までがそろわない"}); continue
        c["照合したレース"] += 1
        t = [by[i][0] for i in (1, 2, 3)]; n = [h["num"] for h in t]; g = [h["gate"] for h in t]
        exp = {"tansho": {n[0]}, "umaren": {key(n[:2])}, "umatan": {key(n[:2], True)}, "trio": {key(n)}, "tierce": {key(n, True)},
               "wide": {key([n[0], n[1]]), key([n[0], n[2]]), key([n[1], n[2]])}, "wakuren": {"-".join(sorted(g[:2], key=int))}}
        for bt, e in exp.items():
            if bt not in p: c[f"{bt}: 払戻に無い"] += 1; continue
            if set(p[bt]) == e: c[f"{bt}: 一致"] += 1
            else: c[f"{bt}: 不一致"] += 1; bad.append({"日": d, "レース": race, "券種": bt, "払戻": sorted(p[bt]), "結果から": sorted(e)})
        fk = set(p.get("fukusho", {})); k = len(fk)
        if fk == set(n[:k]): c["fukusho: 上からk頭と一致"] += 1
        else: c["fukusho: 不一致"] += 1; bad.append({"日": d, "レース": race, "券種": "fukusho", "払戻": sorted(fk), "1〜3着": n})
        nf = len(rows); w = "wakuren" in p
        c[f"（未検証）複勝の払戻頭数={k}・着順のある頭数{'7以下' if nf <= 7 else '8以上'}"] += 1
        c[f"（未検証）枠連{'あり' if w else 'なし'}・着順のある頭数{'8以下' if nf <= 8 else '9以上'}"] += 1
        if (k == 3 and nf <= 7) or (k == 2 and nf >= 8) or (w and nf <= 8) or (not w and nf >= 9):
            need.append({"日": d, "レース": race, "着順のある頭数": nf, "複勝の払戻頭数": k, "枠連": w})
c["頭数の条件を追加で確かめる必要があるレース"] = len(need)
summary = dict(c)
json.dump({"summary": summary, "不一致": bad, "頭数の条件を追加で確かめる必要があるレース": need},
          open(Path(__file__).parent / "results/payout_combo.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1)); [print(x) for x in need]; [print(x) for x in bad]
