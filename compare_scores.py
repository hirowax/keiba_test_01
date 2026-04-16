#!/usr/bin/env python3
"""旧スコア vs 新スコアの前後比較"""
import json
import re
from pathlib import Path
from collections import defaultdict

OUTPUT_DIR = Path("output")


def load_scored(path):
    """pickup_scores.json → { (date, race_label, 馬番): score_dict }"""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        ps = json.load(f)
    result = {}
    date = ps.get("date", "")
    for rl, rd in ps.get("races", {}).items():
        if "error" in rd:
            continue
        for h in rd.get("scored", []):
            key = (date, rl, str(h["馬番"]))
            result[key] = h
    return result


def load_results(date):
    rr_path = OUTPUT_DIR / date / "race_results.json"
    if not rr_path.exists():
        return {}
    with open(rr_path, encoding="utf-8") as f:
        rr = json.load(f)
    out = {}
    for rl, horses in rr.items():
        for h in horses:
            out[(date, rl, str(h["num"]))] = h
    return out


def main():
    dates = sorted(d.name for d in OUTPUT_DIR.iterdir()
                   if d.is_dir() and re.match(r"^\d{8}$", d.name)
                   and (OUTPUT_DIR / d.name / "pickup_scores_before.json").exists()
                   and (OUTPUT_DIR / d.name / "race_results.json").exists())

    all_old = {}
    all_new = {}
    all_res = {}

    for date in dates:
        old = load_scored(OUTPUT_DIR / date / "pickup_scores_before.json")
        new = load_scored(OUTPUT_DIR / date / "pickup_scores.json")
        res = load_results(date)
        all_old.update(old)
        all_new.update(new)
        all_res.update(res)

    keys = sorted(set(all_old) | set(all_new))
    print(f"対象馬: {len(keys)}頭 / {len(dates)}日\n")

    # ── 全体比較 ──
    def stats(scored_map, result_map, label, threshold=0):
        horses = []
        for k in keys:
            s = scored_map.get(k)
            r = result_map.get(k)
            if not s or not r:
                continue
            if s["score"] < threshold:
                continue
            horses.append({
                "score": s["score"],
                "rank_result": r.get("rank"),
                "odds": r.get("odds", 0),
                "pop": r.get("pop", 0),
                "name": s.get("馬名", ""),
            })
        if not horses:
            return
        n = len(horses)
        wins = sum(1 for h in horses if h["rank_result"] == 1)
        top3 = sum(1 for h in horses if h["rank_result"] is not None and h["rank_result"] <= 3)
        roi = sum(h["odds"] * 100 for h in horses if h["rank_result"] == 1) / (n * 100) * 100 if n else 0
        print(f"  {label:<20s} N={n:>4} 勝率{wins/n*100:5.1f}% 3着内{top3/n*100:5.1f}% 回収率{roi:6.1f}%")

    print("=" * 80)
    print("【スコア閾値別 旧 vs 新】")
    print("=" * 80)
    for th in [0, 1, 3, 5, 7, 8, 9, 10]:
        stats(all_old, all_res, f"旧 score>={th}", th)
        stats(all_new, all_res, f"新 score>={th}", th)
        print()

    # ── 推奨馬の変動 ──
    print("=" * 80)
    print("【推奨馬の入れ替わり（閾値8pt以上）】")
    print("=" * 80)

    old_reco = {k for k, v in all_old.items() if v["score"] >= 8}
    new_reco = {k for k, v in all_new.items() if v["score"] >= 8}
    added = new_reco - old_reco
    removed = old_reco - new_reco
    kept = old_reco & new_reco

    print(f"\n  継続: {len(kept)}頭  新規追加: {len(added)}頭  脱落: {len(removed)}頭\n")

    if added:
        print("  ── 新規追加（新で8pt以上、旧で7pt以下）──")
        for k in sorted(added):
            r = all_res.get(k, {})
            old_s = all_old.get(k, {}).get("score", 0)
            new_s = all_new[k]["score"]
            rank = r.get("rank", "?")
            odds = r.get("odds", 0)
            pop = r.get("pop", 0)
            result = "◎" if rank == 1 else ("○" if rank and rank <= 3 else "×")
            print(f"    {k[0]} {k[1]:<10} {all_new[k].get('馬名',''):<12} "
                  f"旧{old_s}→新{new_s}pt  {pop}人気 {rank}着 {odds}倍 {result}")

    if removed:
        print("\n  ── 脱落（旧で8pt以上、新で7pt以下）──")
        for k in sorted(removed):
            r = all_res.get(k, {})
            old_s = all_old[k]["score"]
            new_s = all_new.get(k, {}).get("score", 0)
            rank = r.get("rank", "?")
            odds = r.get("odds", 0)
            pop = r.get("pop", 0)
            result = "◎" if rank == 1 else ("○" if rank and rank <= 3 else "×")
            print(f"    {k[0]} {k[1]:<10} {all_old[k].get('馬名',''):<12} "
                  f"旧{old_s}→新{new_s}pt  {pop}人気 {rank}着 {odds}倍 {result}")

    # ── スコア変動詳細（上がった馬 / 下がった馬）──
    print(f"\n{'='*80}")
    print("【全馬スコア変動】")
    print("=" * 80)
    up = []
    down = []
    for k in keys:
        o = all_old.get(k, {}).get("score", 0)
        n = all_new.get(k, {}).get("score", 0)
        if n > o:
            up.append((k, o, n))
        elif n < o:
            down.append((k, o, n))
    print(f"  スコア上昇: {len(up)}頭  下降: {len(down)}頭  変化なし: {len(keys)-len(up)-len(down)}頭\n")

    # 上昇馬の結果サマリー
    up_wins = sum(1 for k, o, n in up if all_res.get(k, {}).get("rank") == 1)
    up_top3 = sum(1 for k, o, n in up if all_res.get(k, {}).get("rank") is not None and all_res.get(k, {}).get("rank") <= 3)
    down_wins = sum(1 for k, o, n in down if all_res.get(k, {}).get("rank") == 1)
    down_top3 = sum(1 for k, o, n in down if all_res.get(k, {}).get("rank") is not None and all_res.get(k, {}).get("rank") <= 3)

    if up:
        up_n = len([k for k, o, n in up if all_res.get(k)])
        print(f"  上昇馬: {len(up)}頭中 勝ち{up_wins} 3着内{up_top3} "
              f"(3着内率{up_top3/up_n*100:.1f}%)" if up_n else "")
    if down:
        down_n = len([k for k, o, n in down if all_res.get(k)])
        print(f"  下降馬: {len(down)}頭中 勝ち{down_wins} 3着内{down_top3} "
              f"(3着内率{down_top3/down_n*100:.1f}%)" if down_n else "")

    # 上昇した馬の回収率
    up_with_res = [(k, o, n) for k, o, n in up if all_res.get(k)]
    if up_with_res:
        up_roi = sum(all_res[k]["odds"] * 100 for k, o, n in up_with_res if all_res[k].get("rank") == 1) / (len(up_with_res) * 100) * 100
        print(f"  上昇馬回収率: {up_roi:.1f}%")
    down_with_res = [(k, o, n) for k, o, n in down if all_res.get(k)]
    if down_with_res:
        down_roi = sum(all_res[k]["odds"] * 100 for k, o, n in down_with_res if all_res[k].get("rank") == 1) / (len(down_with_res) * 100) * 100
        print(f"  下降馬回収率: {down_roi:.1f}%")

    # ── 閾値別 回収率比較 (ev_threshold想定) ──
    print(f"\n{'='*80}")
    print("【ev_threshold (最注目馬) 閾値別比較】")
    print("=" * 80)
    for th in range(5, 13):
        for ver, smap in [("旧", all_old), ("新", all_new)]:
            hs = [(k, smap[k]) for k in keys if k in smap and smap[k]["score"] >= th and k in all_res]
            if not hs:
                continue
            n = len(hs)
            w = sum(1 for k, s in hs if all_res[k].get("rank") == 1)
            t3 = sum(1 for k, s in hs if all_res[k].get("rank") is not None and all_res[k]["rank"] <= 3)
            roi = sum(all_res[k]["odds"] * 100 for k, s in hs if all_res[k].get("rank") == 1) / (n * 100) * 100
            print(f"  {ver} {th}pt以上: N={n:>3} 勝率{w/n*100:5.1f}% 3着内{t3/n*100:5.1f}% 回収率{roi:6.1f}%")
        print()


if __name__ == "__main__":
    main()
