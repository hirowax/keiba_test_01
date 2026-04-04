#!/usr/bin/env python3
"""
仮説検証スクリプト
H1: 前走タイム指数が高い馬は馬券内(1-3着)に来やすいか
H2: 前走上位人気(1-3番人気)で凡走(4着以下) + 今回人気落ちの馬は馬券内に来やすいか
"""

import json
from pathlib import Path
import statistics

BASE_DIR = Path(__file__).parent
DATE = "20260328"

def main():
    # データ読み込み
    with open(BASE_DIR / "output" / DATE / "race_results.json", encoding="utf-8") as f:
        race_results = json.load(f)

    with open(BASE_DIR / "output" / DATE / "prev_data.json", encoding="utf-8") as f:
        prev_data = json.load(f)

    with open(BASE_DIR / "output" / DATE / "pickup_scores.json", encoding="utf-8") as f:
        pickup_scores = json.load(f)

    # 全馬のデータを結合
    all_horses = []  # {name, num, rank, pop, horse_id, race_label, prev_pop, prev_rank, prev_idx}

    for race_label, horses in race_results.items():
        for h in horses:
            hid = h.get("horse_id", "")
            prev = prev_data.get(hid, {})

            # 人気・着順を数値に変換
            def to_int(v, default=99):
                try:
                    return int(str(v).strip())
                except:
                    return default

            rank = to_int(h.get("rank", 99))
            pop  = to_int(h.get("pop", 99))
            prev_pop_raw  = prev.get("prev_pop", "")
            prev_rank_raw = prev.get("prev_rank", "")
            prev_idx_raw  = prev.get("prev_idx", "")

            prev_pop  = to_int(prev_pop_raw)
            prev_rank = to_int(prev_rank_raw)

            try:
                prev_idx = float(prev_idx_raw)
            except:
                prev_idx = None

            all_horses.append({
                "race_label": race_label,
                "name": h.get("name", ""),
                "num": h.get("num", ""),
                "rank": rank,
                "pop": pop,
                "horse_id": hid,
                "prev_pop": prev_pop,
                "prev_rank": prev_rank,
                "prev_idx": prev_idx,
            })

    print(f"総馬数: {len(all_horses)}")
    placed = [h for h in all_horses if h["rank"] <= 3]
    not_placed = [h for h in all_horses if h["rank"] > 3]
    print(f"馬券内(1-3着): {len(placed)}頭 / 圏外: {len(not_placed)}頭")
    print()

    # ──────────────────────────────────────────────────
    # H1: 前走タイム指数 × 馬券内
    # ──────────────────────────────────────────────────
    print("=" * 60)
    print("【H1】前走タイム指数が高い馬は馬券内に来やすいか")
    print("=" * 60)

    h1_placed     = [h["prev_idx"] for h in placed     if h["prev_idx"] is not None]
    h1_not_placed = [h["prev_idx"] for h in not_placed if h["prev_idx"] is not None]

    if h1_placed and h1_not_placed:
        print(f"馬券内の前走指数:  平均{statistics.mean(h1_placed):.1f}  中央値{statistics.median(h1_placed):.1f}  n={len(h1_placed)}")
        print(f"圏外の前走指数:    平均{statistics.mean(h1_not_placed):.1f}  中央値{statistics.median(h1_not_placed):.1f}  n={len(h1_not_placed)}")

    # 指数レンジ別の馬券率
    print()
    print("前走指数レンジ別 馬券内率:")
    print(f"{'指数範囲':<12} {'馬券内':>6} {'全馬':>6} {'馬券率':>8}")
    ranges = [(100, 999), (90, 99), (80, 89), (70, 79), (0, 69), (None, None)]
    for lo, hi in ranges:
        if lo is None:
            subset = [h for h in all_horses if h["prev_idx"] is None]
            label = "データなし"
        else:
            subset = [h for h in all_horses if h["prev_idx"] is not None and lo <= h["prev_idx"] <= hi]
            label = f"{lo}〜{hi}"
        total = len(subset)
        win = len([h for h in subset if h["rank"] <= 3])
        rate = win / total * 100 if total > 0 else 0
        print(f"{label:<12} {win:>6} {total:>6} {rate:>7.1f}%")

    # 上位・下位での的中率
    horses_with_idx = [h for h in all_horses if h["prev_idx"] is not None]
    if horses_with_idx:
        sorted_by_idx = sorted(horses_with_idx, key=lambda x: -x["prev_idx"])
        top_third = sorted_by_idx[:len(sorted_by_idx)//3]
        bottom_third = sorted_by_idx[-(len(sorted_by_idx)//3):]
        top_rate = len([h for h in top_third if h["rank"] <= 3]) / len(top_third) * 100
        bot_rate = len([h for h in bottom_third if h["rank"] <= 3]) / len(bottom_third) * 100
        print(f"\n上位1/3(高指数): 馬券率 {top_rate:.1f}%  (n={len(top_third)})")
        print(f"下位1/3(低指数): 馬券率 {bot_rate:.1f}%  (n={len(bottom_third)})")

    print()

    # ──────────────────────────────────────────────────
    # H2: 前走上位人気凡走 + 今回人気落ち × 馬券内
    # ──────────────────────────────────────────────────
    print("=" * 60)
    print("【H2】前走上位人気(1-3番人気)で凡走(4着以下) + 今回人気落ちの馬")
    print("=" * 60)

    # 条件: 前走1-3番人気 AND 前走4着以下 AND 今回人気 > 前走人気
    h2_horses = [
        h for h in all_horses
        if h["prev_pop"] <= 3
        and h["prev_rank"] >= 4
        and h["pop"] > h["prev_pop"]
        and h["prev_pop"] < 90
        and h["prev_rank"] < 90
        and h["pop"] < 90
    ]

    print(f"該当馬数: {len(h2_horses)}頭")
    if h2_horses:
        h2_placed_count = len([h for h in h2_horses if h["rank"] <= 3])
        h2_rate = h2_placed_count / len(h2_horses) * 100
        print(f"馬券内: {h2_placed_count}頭 / 馬券率: {h2_rate:.1f}%")
        print()
        print("該当馬一覧:")
        print(f"{'レース':<12} {'馬名':<12} {'着順':>4} {'人気':>4} {'前走人気':>6} {'前走着順':>6} {'前走指数':>6}")
        for h in sorted(h2_horses, key=lambda x: x["rank"]):
            idx_str = f"{h['prev_idx']:.0f}" if h["prev_idx"] is not None else "-"
            marker = "★" if h["rank"] <= 3 else ""
            print(f"{h['race_label']:<12} {h['name']:<12} {h['rank']:>4} {h['pop']:>4} {h['prev_pop']:>6} {h['prev_rank']:>6} {idx_str:>6} {marker}")

    # 比較: 全体の馬券率
    all_with_data = [h for h in all_horses if h["pop"] < 90]
    overall_rate = len([h for h in all_with_data if h["rank"] <= 3]) / len(all_with_data) * 100
    print(f"\n全体馬券率（参考）: {overall_rate:.1f}%  (n={len(all_with_data)})")

    print()
    print("=" * 60)
    print("【補足】今回人気別の馬券内率（全馬）")
    print("=" * 60)
    print(f"{'人気':>4} {'馬券内':>6} {'全馬':>6} {'馬券率':>8}")
    for pop_val in range(1, 10):
        subset = [h for h in all_horses if h["pop"] == pop_val]
        win = len([h for h in subset if h["rank"] <= 3])
        rate = win / len(subset) * 100 if subset else 0
        print(f"{pop_val:>4} {win:>6} {len(subset):>6} {rate:>7.1f}%")

    print()
    print("=" * 60)
    print("【補足2】前走着順別 馬券内率")
    print("=" * 60)
    print(f"{'前走着順':>8} {'馬券内':>6} {'全馬':>6} {'馬券率':>8}")
    for prev_r in [1, 2, 3, 4, 5, 6, 7, 8]:
        subset = [h for h in all_horses if h["prev_rank"] == prev_r]
        win = len([h for h in subset if h["rank"] <= 3])
        rate = win / len(subset) * 100 if subset else 0
        print(f"{prev_r:>8} {win:>6} {len(subset):>6} {rate:>7.1f}%")

    # 9着以上まとめて
    subset = [h for h in all_horses if 9 <= h["prev_rank"] <= 20]
    win = len([h for h in subset if h["rank"] <= 3])
    rate = win / len(subset) * 100 if subset else 0
    print(f"{'9着以上':>8} {win:>6} {len(subset):>6} {rate:>7.1f}%")

    print()
    print("=" * 60)
    print("【3指数重複馬】の馬券内率（参考）")
    print("=" * 60)

    # pickup_scores から3指数重複馬の馬番を取得
    triple_hits = 0
    triple_total = 0
    for race_label_pu, rdata in pickup_scores.get("races", {}).items():
        scored = rdata.get("scored", [])
        # race_label_pu は "東京11R" 形式
        # race_results のキーも同じ形式のはず
        result_horses = race_results.get(race_label_pu, [])
        result_by_num = {str(h["num"]): h for h in result_horses}

        for horse in scored:
            num = str(horse.get("馬番", "")).strip()
            h_result = result_by_num.get(num)
            if h_result:
                triple_total += 1
                if h_result["rank"] <= 3:
                    triple_hits += 1

    if triple_total > 0:
        print(f"3指数重複馬: {triple_hits}/{triple_total}頭 馬券率 {triple_hits/triple_total*100:.1f}%")

    # スコア別内訳
    print()
    print("スコア別 馬券内率:")
    print(f"{'スコア':>6} {'馬券内':>6} {'全馬':>6} {'馬券率':>8}")
    score_buckets = {}
    for race_label_pu, rdata in pickup_scores.get("races", {}).items():
        scored = rdata.get("scored", [])
        result_horses = race_results.get(race_label_pu, [])
        result_by_num = {str(h["num"]): h for h in result_horses}
        for horse in scored:
            num = str(horse.get("馬番", "")).strip()
            score = horse.get("score", 0)
            h_result = result_by_num.get(num)
            if h_result:
                bucket = score
                if bucket not in score_buckets:
                    score_buckets[bucket] = {"win": 0, "total": 0}
                score_buckets[bucket]["total"] += 1
                if h_result["rank"] <= 3:
                    score_buckets[bucket]["win"] += 1

    for sc in sorted(score_buckets.keys(), reverse=True):
        b = score_buckets[sc]
        rate = b["win"] / b["total"] * 100 if b["total"] > 0 else 0
        print(f"{sc:>6} {b['win']:>6} {b['total']:>6} {rate:>7.1f}%")


if __name__ == "__main__":
    main()
