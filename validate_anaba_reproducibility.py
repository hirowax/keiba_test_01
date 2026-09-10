#!/usr/bin/env python3
"""
穴馬戦略の再現性検証スクリプト
過去38日分のデータに対して以下の検証を行う:
  1. 時系列3分割（早期/中期/後期）でROIが安定しているか
  2. 外れ値除去（上位N頭の勝ちを除いた場合のROI）
  3. 日別累積ROI（時系列で右肩上がりか・特定日に集中していないか）
  4. ブートストラップ（ランダム抽出）でROIの分散を推定
  5. 直近週の結果（先週末＝5/2-5/3）との乖離
"""
import json
import re
import random
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from analyze_anaba_filters import load_all, calc_v2, apply_filters, calc_stats, TOTAL_DAYS

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"


def split_by_date_thirds(horses):
    """日付で3等分"""
    dates = sorted(set(h["date"] for h in horses))
    n = len(dates)
    early_end = dates[n//3]
    mid_end = dates[2*n//3]

    early = [h for h in horses if h["date"] <= early_end]
    middle = [h for h in horses if early_end < h["date"] <= mid_end]
    late = [h for h in horses if h["date"] > mid_end]
    return {
        "早期": (early, dates[0], early_end),
        "中期": (middle, dates[n//3+1] if n//3+1 < n else early_end, mid_end),
        "後期": (late, dates[2*n//3+1] if 2*n//3+1 < n else mid_end, dates[-1]),
    }


def remove_top_winners(cands, n_remove):
    """単勝オッズ上位n_remove頭の勝ちを除外して再計算"""
    winners = sorted([h for h in cands if h["is_win"]], key=lambda x: -x["odds"])
    excluded_keys = set(id(w) for w in winners[:n_remove])
    return [h for h in cands if id(h) not in excluded_keys]


def cumulative_roi_by_date(cands):
    """日別の単勝ROI推移"""
    daily = defaultdict(lambda: {"n": 0, "wins": 0, "payout": 0})
    for h in cands:
        d = daily[h["date"]]
        d["n"] += 1
        if h["is_win"]:
            d["wins"] += 1
            d["payout"] += h["odds"] * 100
    dates = sorted(daily.keys())
    cum_n = 0
    cum_payout = 0
    rows = []
    for d in dates:
        cum_n += daily[d]["n"]
        cum_payout += daily[d]["payout"]
        cum_roi = cum_payout / (cum_n * 100) * 100 if cum_n > 0 else 0
        rows.append({
            "date": d, "n": daily[d]["n"], "wins": daily[d]["wins"],
            "payout": daily[d]["payout"],
            "day_roi": daily[d]["payout"] / (daily[d]["n"] * 100) * 100 if daily[d]["n"] > 0 else 0,
            "cum_n": cum_n, "cum_roi": cum_roi,
        })
    return rows


def bootstrap_roi(cands, n_iter=1000, sample_ratio=0.7):
    """ブートストラップ: ランダムにサンプリングしてROIの分布を推定"""
    if not cands: return None
    rois = []
    top3_rates = []
    sample_size = max(int(len(cands) * sample_ratio), 1)
    for _ in range(n_iter):
        sample = random.sample(cands, sample_size)
        s = calc_stats(sample)
        rois.append(s["roi_win"])
        top3_rates.append(s["top3_rate"])
    rois.sort()
    top3_rates.sort()
    return {
        "roi_p05": rois[int(n_iter*0.05)],
        "roi_p50": rois[int(n_iter*0.50)],
        "roi_p95": rois[int(n_iter*0.95)],
        "top3_p05": top3_rates[int(n_iter*0.05)],
        "top3_p50": top3_rates[int(n_iter*0.50)],
        "top3_p95": top3_rates[int(n_iter*0.95)],
    }


# ─── 検証対象の3戦略 ───────────────────────────────────────────────
STRATEGIES = [
    {
        "name": "🥇 ROI最大 (B4×D1)",
        "params": dict(score_min=4, odds_lo=None, odds_hi=None,
                       per_race_max=None, d_pattern=1,
                       excl_female=False, excl_dist=False, excl_hwloss=False),
    },
    {
        "name": "🥈 3着内最大 (B3×C1×E1)",
        "params": dict(score_min=3, odds_lo=5, odds_hi=15,
                       per_race_max=None, d_pattern=0,
                       excl_female=True, excl_dist=False, excl_hwloss=False),
    },
    {
        "name": "🥉 バランス型 (B4×A1×E1)",
        "params": dict(score_min=4, odds_lo=None, odds_hi=None,
                       per_race_max=1, d_pattern=0,
                       excl_female=True, excl_dist=False, excl_hwloss=False),
    },
]


def section(title):
    print()
    print("=" * 100)
    print(f"  {title}")
    print("=" * 100)


def print_strategy_stats(stats, label):
    print(f"  {label:<26s}  N={stats['n']:>3d}  "
          f"勝率{stats['win_rate']:5.1f}%  3着内{stats['top3_rate']:5.1f}%  "
          f"単勝ROI{stats['roi_win']:7.1f}%")


def main():
    random.seed(42)
    print("データ読み込み中...")
    horses = load_all()
    anaba = [h for h in horses if h["score"] >= 3]
    print(f"  総馬数: {len(horses)}  score≥3 候補: {len(anaba)}")
    dates = sorted(set(h["date"] for h in horses))
    print(f"  対象期間: {dates[0]} 〜 {dates[-1]} ({len(dates)}日)")

    folds = split_by_date_thirds(anaba)

    # ─── 検証1: 時系列3分割 ──────────────────────────────────────────
    section("【検証1】時系列3分割（早期/中期/後期）でROIが安定しているか")
    print()
    for strat in STRATEGIES:
        print(f"\n  {strat['name']}")
        print(f"  {'-'*90}")
        # 全期間
        full = apply_filters(anaba, **strat["params"])
        full_stats = calc_stats(full)
        print_strategy_stats(full_stats, "全期間")
        # 各fold
        for fold_name, (fold_data, d_start, d_end) in folds.items():
            sub = apply_filters(fold_data, **strat["params"])
            s = calc_stats(sub)
            print_strategy_stats(s, f"{fold_name}({d_start}-{d_end})")

    # ─── 検証2: 外れ値除去 ──────────────────────────────────────────
    section("【検証2】外れ値除去（オッズ上位N頭の勝ちを除外）")
    print()
    for strat in STRATEGIES:
        print(f"\n  {strat['name']}")
        cands = apply_filters(anaba, **strat["params"])
        full = calc_stats(cands)
        print(f"  全勝ち馬: {full['wins']}頭  単勝ROI {full['roi_win']:.1f}%")
        for n_rm in [1, 3, 5, 10]:
            if n_rm > full["wins"]: continue
            reduced = remove_top_winners(cands, n_rm)
            s = calc_stats(reduced)
            print(f"  上位{n_rm}頭の勝ちを除外 → N={s['n']}  単勝ROI {s['roi_win']:.1f}%  3着内{s['top3_rate']:.1f}%")

    # ─── 検証3: 日別累積ROI ──────────────────────────────────────────
    section("【検証3】日別累積ROI（特定日への集中度）")
    for strat in STRATEGIES:
        print(f"\n  {strat['name']}")
        print(f"  {'日付':<10} {'当日N':<5} {'当日勝':<5} {'当日ROI':<9} {'累計N':<5} {'累計ROI':<9}")
        print(f"  {'-'*60}")
        cands = apply_filters(anaba, **strat["params"])
        rows = cumulative_roi_by_date(cands)
        for r in rows:
            mark = "🔥" if r["day_roi"] >= 500 else ("✓" if r["day_roi"] > 0 else "✗")
            print(f"  {r['date']} {r['n']:<5d} {r['wins']:<5d} {r['day_roi']:>7.1f}%  "
                  f"{r['cum_n']:<5d} {r['cum_roi']:>7.1f}%  {mark}")

    # ─── 検証4: ブートストラップ ────────────────────────────────────
    section("【検証4】ブートストラップ（ランダム70%抽出 × 1000回）でROI分布")
    print(f"\n  {'戦略':<26s} {'N':<4s} {'p05':<10s} {'p50(中央値)':<13s} {'p95':<10s}  分散判定")
    print(f"  {'-'*100}")
    for strat in STRATEGIES:
        cands = apply_filters(anaba, **strat["params"])
        bs = bootstrap_roi(cands, n_iter=1000, sample_ratio=0.7)
        if bs is None: continue
        spread = bs["roi_p95"] - bs["roi_p05"]
        verdict = "⚠ 高変動" if spread > 1000 else ("△ 中変動" if spread > 500 else "◯ 低変動")
        print(f"  {strat['name']:<26s} {len(cands):<4d} "
              f"ROI p05={bs['roi_p05']:>6.1f}%  p50={bs['roi_p50']:>6.1f}%  "
              f"p95={bs['roi_p95']:>6.1f}%  {verdict}")
        print(f"  {'  └ 3着内率':<26s}      "
              f"3着内 p05={bs['top3_p05']:>5.1f}%  p50={bs['top3_p50']:>5.1f}%  "
              f"p95={bs['top3_p95']:>5.1f}%")

    # ─── 検証5: 直近週との乖離 ──────────────────────────────────────
    section("【検証5】直近2週間（4/25, 4/26, 5/2, 5/3）の結果")
    recent_dates = ["20260425", "20260426", "20260502", "20260503"]
    for strat in STRATEGIES:
        print(f"\n  {strat['name']}")
        full = apply_filters(anaba, **strat["params"])
        full_stats = calc_stats(full)
        print(f"  全期間: N={full_stats['n']} ROI {full_stats['roi_win']:.1f}% 3着内{full_stats['top3_rate']:.1f}%")
        for d in recent_dates:
            sub = [h for h in full if h["date"] == d]
            if not sub:
                print(f"  {d}: 候補なし")
                continue
            s = calc_stats(sub)
            print(f"  {d}: N={s['n']} 勝{s['wins']}頭 ROI {s['roi_win']:.1f}% 3着内{s['top3_rate']:.1f}%  "
                  f"{('✓' if s['wins']>0 else '✗')}")

    # ─── 検証6: 全期間 vs 後期1ヶ月 vs 直近2週 ───────────────────────
    section("【検証6】期間別比較（汎化性能の最重要指標）")
    last_month = sorted(dates)[-8:]  # 直近約1ヶ月（土日8日分）
    last_2weeks = sorted(dates)[-4:]  # 直近2週

    print(f"\n  期間定義:")
    print(f"    全期間:   {dates[0]}-{dates[-1]} ({len(dates)}日)")
    print(f"    直近1ヶ月: {last_month[0]}-{last_month[-1]} ({len(last_month)}日)")
    print(f"    直近2週:  {last_2weeks[0]}-{last_2weeks[-1]} ({len(last_2weeks)}日)")
    print()
    print(f"  {'戦略':<28s} {'期間':<10s} {'N':<4s} {'勝率':<7s} {'3着内':<7s} {'単勝ROI':<8s}")
    print(f"  {'-'*90}")
    for strat in STRATEGIES:
        cands = apply_filters(anaba, **strat["params"])
        for span_name, span_dates in [("全期間", dates), ("直近1ヶ月", last_month), ("直近2週", last_2weeks)]:
            sub = [h for h in cands if h["date"] in span_dates]
            if not sub:
                print(f"  {strat['name']:<28s} {span_name:<10s} N=0  (候補なし)")
                continue
            s = calc_stats(sub)
            verdict = ""
            if span_name != "全期間":
                full_roi = calc_stats(cands)["roi_win"]
                if s["roi_win"] < full_roi * 0.3:
                    verdict = "  ⚠ 大幅悪化"
                elif s["roi_win"] < full_roi * 0.7:
                    verdict = "  △ 悪化傾向"
            print(f"  {strat['name']:<28s} {span_name:<10s} N={s['n']:<3d} "
                  f"{s['win_rate']:>5.1f}%  {s['top3_rate']:>5.1f}%  "
                  f"{s['roi_win']:>7.1f}%{verdict}")


if __name__ == "__main__":
    main()
