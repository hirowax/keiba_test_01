#!/usr/bin/env python3
"""
穴馬フィルター最適化スクリプト
docs/anaba_filter_optimization.md の仕様に従い A〜E 軸の組合せを総当たり検証する
usage: python3 analyze_anaba_filters.py 2>&1 | tee docs/anaba_filter_results.txt
"""
import json
import re
from pathlib import Path
from datetime import datetime
from itertools import product
from collections import defaultdict

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

TOTAL_DAYS = 38  # 対象日数（N/day計算用）


# ─── ヘルパ（analyze_anaba.py から流用） ────────────────────────────
def extract_class(cn):
    if not cn: return "不明"
    if "新馬" in cn: return "新馬"
    if "未勝利" in cn: return "未勝利"
    if "オープン" in cn or "ＯＰ" in cn: return "OP+"
    if "３勝" in cn or "3勝" in cn: return "3勝"
    if "２勝" in cn or "2勝" in cn: return "2勝"
    if "１勝" in cn or "1勝" in cn: return "1勝"
    return "その他"

def extract_age_class(cn):
    if not cn: return "不明"
    if "２歳" in cn or "2歳" in cn: return "2歳"
    if "３歳以上" in cn or "3歳以上" in cn or "４歳以上" in cn or "4歳以上" in cn: return "3歳以上"
    if "３歳" in cn or "3歳" in cn: return "3歳限定"
    return "不明"

def is_female_only(cn): return "牝" in cn


# ─── v2 穴馬スコア計算 ─────────────────────────────────────────────
def calc_v2(h):
    """h: {prev_rank, prev_idx, hw_diff, race_dist, prev_dist, female_only}"""
    score = 0
    factors = []

    if h["prev_rank"] == 1:
        score += 3; factors.append("前走1着+3")
    elif h["prev_rank"] in (2, 3):
        score += 1; factors.append(f"前走{h['prev_rank']}着+1")

    if h["prev_idx"] is not None and h["prev_idx"] >= 80 and 1 <= h["prev_rank"] <= 3:
        score += 1; factors.append(f"指数{h['prev_idx']:.0f}+1-3着+1")

    if -2 <= h["hw_diff"] <= 2:
        score += 1; factors.append(f"馬体平行({h['hw_diff']:+d})+1")

    if h["hw_diff"] <= -10:
        score -= 2; factors.append(f"馬体減{h['hw_diff']}kg-2")

    dist_diff = h["race_dist"] - h["prev_dist"] if h["race_dist"] and h["prev_dist"] else 0
    if dist_diff >= 200:
        score -= 1; factors.append(f"距離延長+{dist_diff}m-1")

    if h["female_only"]:
        score -= 1; factors.append("牝馬限定-1")

    badges = []
    if h["class_lvl"] == "未勝利" and h["prev_rank"] == 1:
        badges.append("未勝利×前走1着")
    if h["age_class"] == "3歳限定" and h["prev_rank"] == 1:
        badges.append("3歳限定×前走1着")

    return score, factors, badges, dist_diff


# ─── データロード ─────────────────────────────────────────────────
def load_all():
    horse_db = json.loads((OUTPUT_DIR / "horse_db.json").read_text())
    horses = []
    dates = sorted(d.name for d in OUTPUT_DIR.iterdir()
                   if d.is_dir() and re.match(r"^\d{8}$", d.name))

    for date in dates:
        rr_path = OUTPUT_DIR / date / "race_results.json"
        rc_path = OUTPUT_DIR / date / "race_conditions.json"
        ps_path = OUTPUT_DIR / date / "pickup_scores.json"
        if not rr_path.exists(): continue

        rr = json.loads(rr_path.read_text())
        rc = json.loads(rc_path.read_text()) if rc_path.exists() else {}
        ps = json.loads(ps_path.read_text()) if ps_path.exists() else {"races": {}}

        triple_set = set()
        for rl, rd in ps.get("races", {}).items():
            if isinstance(rd, dict):
                for h in rd.get("scored", []):
                    triple_set.add((rl, str(h.get("馬番", ""))))

        for race_label, results in rr.items():
            cond = rc.get(race_label, {}) if isinstance(rc, dict) else {}
            race_dist = cond.get("distance")
            class_name = cond.get("class_name", "")
            cls = extract_class(class_name)
            age = extract_age_class(class_name)
            fem = is_female_only(class_name)

            for h in results:
                hid = h.get("horse_id", "")
                try: pop = int(h.get("pop", 99))
                except: pop = 99
                if pop < 5: continue
                if (race_label, str(h.get("num", ""))) in triple_set: continue

                prev = horse_db.get(hid, {}) if hid else {}
                def ti(v, d=99):
                    try: return int(str(v).strip())
                    except: return d
                def tf(v):
                    try: return float(str(v).strip()) if v else None
                    except: return None

                prev_rank = ti(prev.get("prev_rank", ""))
                prev_idx  = tf(prev.get("prev_idx", ""))
                hw_diff   = ti(h.get("horse_weight_diff", 0), 0)
                prev_dist_raw = prev.get("prev_dist", "")
                m = re.search(r"(\d{3,4})", prev_dist_raw)
                prev_dist = int(m.group(1)) if m else None
                odds = tf(h.get("odds")) or 0
                place_odds = tf(h.get("place_odds")) or 0

                try: rank = int(h.get("rank", 99))
                except: rank = 99

                hd = {
                    "prev_rank": prev_rank, "prev_idx": prev_idx,
                    "hw_diff": hw_diff, "race_dist": race_dist,
                    "prev_dist": prev_dist, "female_only": fem,
                    "class_lvl": cls, "age_class": age,
                }
                score, factors, badges, dist_diff = calc_v2(hd)

                horses.append({
                    "date": date,
                    "race_label": race_label,
                    "name": h.get("name", ""),
                    "num": str(h.get("num", "")),
                    "pop": pop,
                    "odds": odds,
                    "place_odds": place_odds,
                    "rank": rank,
                    "score": score,
                    "factors": factors,
                    "badges": badges,
                    "class_lvl": cls,
                    "age_class": age,
                    "female_only": fem,
                    "dist_diff": dist_diff,
                    "hw_diff": hw_diff,
                    "is_win":  rank == 1,
                    "is_top3": 1 <= rank <= 3,
                })

    return horses


# ─── フィルター適用 ────────────────────────────────────────────────
def apply_filters(horses, score_min, odds_lo, odds_hi, per_race_max,
                  d_pattern, excl_female, excl_dist, excl_hwloss):
    """
    score_min    : int (スコア閾値)
    odds_lo/hi   : float|None (オッズ範囲, Noneで無制限)
    per_race_max : int|None (1レース最大頭数, Noneで無制限)
    d_pattern    : 0=全候補 1=未勝利OR3歳限定×前走1着 2=未勝利×前走1着のみ 3=D1+牝馬除外
    excl_female  : bool (牝馬限定戦を完全除外)
    excl_dist    : bool (距離延長+200m以上を完全除外)
    excl_hwloss  : bool (馬体-10kg以下を完全除外)
    """
    cands = []
    for h in horses:
        if h["score"] < score_min: continue
        if odds_lo is not None and h["odds"] < odds_lo: continue
        if odds_hi is not None and h["odds"] > odds_hi: continue
        if excl_female and h["female_only"]: continue
        if excl_dist and h["dist_diff"] >= 200: continue
        if excl_hwloss and h["hw_diff"] <= -10: continue
        if d_pattern == 1:
            if not h["badges"]: continue
        elif d_pattern == 2:
            if "未勝利×前走1着" not in h["badges"]: continue
        elif d_pattern == 3:
            if not h["badges"] or h["female_only"]: continue
        cands.append(h)

    if per_race_max is None:
        return cands

    # per_race_max 適用: 同一日・同一レースで上位N頭
    from collections import defaultdict
    race_buckets = defaultdict(list)
    for h in cands:
        race_buckets[(h["date"], h["race_label"])].append(h)
    result = []
    for key, group in race_buckets.items():
        group.sort(key=lambda x: -x["score"])
        result.extend(group[:per_race_max])
    return result


def calc_stats(cands, label=""):
    n = len(cands)
    if n == 0:
        return {"label": label, "n": 0, "n_day": 0, "wins": 0, "top3": 0,
                "win_rate": 0, "top3_rate": 0, "roi_win": 0, "roi_place": 0}
    wins  = sum(1 for h in cands if h["is_win"])
    top3  = sum(1 for h in cands if h["is_top3"])
    roi_win   = sum(h["odds"] * 100 for h in cands if h["is_win"]) / (n * 100) * 100
    roi_place = sum(h["place_odds"] * 100 for h in cands if h["is_top3"]) / (n * 100) * 100 if any(h["place_odds"] for h in cands) else None
    return {"label": label, "n": n, "n_day": n / TOTAL_DAYS,
            "wins": wins, "top3": top3,
            "win_rate": wins/n*100, "top3_rate": top3/n*100,
            "roi_win": roi_win, "roi_place": roi_place}


def print_row(s, min_n=10):
    if s["n"] < min_n:
        print(f"  {s['label']:<52s}  N={s['n']:>4d}({s['n_day']:.1f}/日)  (サンプル不足)")
        return
    rp = f"{s['roi_place']:6.1f}%" if s["roi_place"] is not None else "  -   "
    print(f"  {s['label']:<52s}  N={s['n']:>4d}({s['n_day']:.1f}/日)  "
          f"勝率{s['win_rate']:5.1f}%  3着内{s['top3_rate']:5.1f}%  "
          f"単勝ROI{s['roi_win']:6.1f}%  複勝ROI{rp}")


def section(title):
    print()
    print("=" * 100)
    print(f"  {title}")
    print("=" * 100)


def main():
    print("データ読み込み中...")
    horses = load_all()
    anaba = [h for h in horses if h["score"] >= 3]
    print(f"  総馬数: {len(horses)}  score≥3の候補: {len(anaba)}")

    # ─── ステップ1: 軸別単独テスト ──────────────────────────────────

    section("【ベースライン】score≥3 / フィルターなし")
    base = apply_filters(anaba, 3, None, None, None, 0, False, False, False)
    print_row(calc_stats(base, "全候補(score≥3)"), min_n=1)

    # 軸A
    section("【ステップ1-A】1レースあたりの最大頭数")
    for mx, lbl in [(None,"A3:制限なし"), (2,"A2:max2"), (1,"A1:max1")]:
        s = apply_filters(anaba, 3, None, None, mx, 0, False, False, False)
        print_row(calc_stats(s, lbl))

    # 軸B
    section("【ステップ1-B】スコア閾値")
    for thr, lbl in [(3,"B3:score≥3"), (4,"B4:score≥4"), (5,"B5:score≥5")]:
        s = apply_filters(anaba, thr, None, None, None, 0, False, False, False)
        print_row(calc_stats(s, lbl))

    # 軸C
    section("【ステップ1-C】オッズ範囲")
    for lo, hi, lbl in [
        (None, None, "C0:制限なし"),
        (5, 15,  "C1:5〜15倍"),
        (5, 30,  "C2:5〜30倍"),
        (5, 50,  "C3:5〜50倍"),
        (10, 30, "C4:10〜30倍"),
    ]:
        s = apply_filters(anaba, 3, lo, hi, None, 0, False, False, False)
        print_row(calc_stats(s, lbl))

    # 軸D
    section("【ステップ1-D】特注パターン限定")
    for dp, lbl in [
        (0, "D0:全候補"),
        (1, "D1:未勝利OR3歳限定×前走1着"),
        (2, "D2:未勝利×前走1着のみ"),
        (3, "D3:D1+牝馬限定除外"),
    ]:
        s = apply_filters(anaba, 3, None, None, None, dp, False, False, False)
        print_row(calc_stats(s, lbl), min_n=5)

    # 軸E
    section("【ステップ1-E】危険シグナル除外")
    for ef, ed, ew, lbl in [
        (False, False, False, "E0:除外なし"),
        (True,  False, False, "E1:牝馬限定除外"),
        (False, True,  False, "E2:距離延長+200m除外"),
        (False, False, True,  "E3:馬体-10kg除外"),
        (True,  True,  True,  "E4:全危険除外"),
    ]:
        s = apply_filters(anaba, 3, None, None, None, 0, ef, ed, ew)
        print_row(calc_stats(s, lbl))

    # ─── ステップ2: 2軸組合せ ────────────────────────────────────────
    section("【ステップ2】2軸組合せ（主要パターン）")

    combos2 = [
        # A × B
        dict(score_min=3, odds_lo=None, odds_hi=None, per_race_max=1, d=0, ef=False, ed=False, ew=False, lbl="A1×B3"),
        dict(score_min=4, odds_lo=None, odds_hi=None, per_race_max=1, d=0, ef=False, ed=False, ew=False, lbl="A1×B4"),
        dict(score_min=5, odds_lo=None, odds_hi=None, per_race_max=1, d=0, ef=False, ed=False, ew=False, lbl="A1×B5"),
        dict(score_min=3, odds_lo=None, odds_hi=None, per_race_max=2, d=0, ef=False, ed=False, ew=False, lbl="A2×B3"),
        dict(score_min=4, odds_lo=None, odds_hi=None, per_race_max=2, d=0, ef=False, ed=False, ew=False, lbl="A2×B4"),
        # A × C
        dict(score_min=3, odds_lo=5,  odds_hi=30, per_race_max=1, d=0, ef=False, ed=False, ew=False, lbl="A1×C2(5-30倍)"),
        dict(score_min=3, odds_lo=5,  odds_hi=50, per_race_max=1, d=0, ef=False, ed=False, ew=False, lbl="A1×C3(5-50倍)"),
        dict(score_min=3, odds_lo=10, odds_hi=30, per_race_max=1, d=0, ef=False, ed=False, ew=False, lbl="A1×C4(10-30倍)"),
        # A × E
        dict(score_min=3, odds_lo=None, odds_hi=None, per_race_max=1, d=0, ef=True,  ed=False, ew=False, lbl="A1×E1(牝馬除外)"),
        dict(score_min=3, odds_lo=None, odds_hi=None, per_race_max=1, d=0, ef=True,  ed=True,  ew=True,  lbl="A1×E4(全危険除外)"),
        # B × D
        dict(score_min=4, odds_lo=None, odds_hi=None, per_race_max=None, d=1, ef=False, ed=False, ew=False, lbl="B4×D1"),
        dict(score_min=3, odds_lo=None, odds_hi=None, per_race_max=None, d=1, ef=False, ed=False, ew=False, lbl="B3×D1"),
        # B × E
        dict(score_min=4, odds_lo=None, odds_hi=None, per_race_max=None, d=0, ef=True,  ed=True,  ew=True,  lbl="B4×E4"),
        dict(score_min=3, odds_lo=None, odds_hi=None, per_race_max=None, d=0, ef=True,  ed=True,  ew=True,  lbl="B3×E4"),
    ]
    for c in combos2:
        s = apply_filters(anaba, c["score_min"], c["odds_lo"], c["odds_hi"],
                          c["per_race_max"], c["d"], c["ef"], c["ed"], c["ew"])
        print_row(calc_stats(s, c["lbl"]))

    # ─── ステップ3: 3軸以上 ──────────────────────────────────────────
    section("【ステップ3】3軸以上の組合せ")

    combos3 = [
        dict(score_min=3, odds_lo=5,  odds_hi=30, per_race_max=1, d=0, ef=True,  ed=True,  ew=True,  lbl="A1×B3×C2×E4"),
        dict(score_min=4, odds_lo=5,  odds_hi=30, per_race_max=1, d=0, ef=True,  ed=True,  ew=True,  lbl="A1×B4×C2×E4"),
        dict(score_min=4, odds_lo=5,  odds_hi=50, per_race_max=1, d=0, ef=True,  ed=False, ew=True,  lbl="A1×B4×C3×E1+E3"),
        dict(score_min=3, odds_lo=None, odds_hi=None, per_race_max=1, d=1, ef=True, ed=True, ew=True, lbl="A1×D1×E4"),
        dict(score_min=4, odds_lo=5,  odds_hi=30, per_race_max=1, d=1, ef=False, ed=False, ew=False, lbl="A1×B4×C2×D1"),
        dict(score_min=3, odds_lo=5,  odds_hi=50, per_race_max=2, d=0, ef=True,  ed=True,  ew=True,  lbl="A2×B3×C3×E4"),
        dict(score_min=4, odds_lo=None, odds_hi=None, per_race_max=1, d=3, ef=True, ed=True, ew=True, lbl="A1×B4×D3×E4"),
        dict(score_min=3, odds_lo=10, odds_hi=30, per_race_max=1, d=0, ef=True,  ed=True,  ew=True,  lbl="A1×C4×E4"),
        dict(score_min=3, odds_lo=5,  odds_hi=30, per_race_max=1, d=1, ef=True,  ed=False, ew=False, lbl="A1×C2×D1×E1"),
    ]
    for c in combos3:
        s = apply_filters(anaba, c["score_min"], c["odds_lo"], c["odds_hi"],
                          c["per_race_max"], c["d"], c["ef"], c["ed"], c["ew"])
        print_row(calc_stats(s, c["lbl"]))

    # ─── ステップ4: 全パターンからROI上位 ───────────────────────────
    section("【ステップ4-grid】全パターングリッドサーチ（推奨基準: N≥30, 3着内≥30%, ROI≥200%, N/日≤3）")

    all_results = []
    for score_min in (3, 4, 5):
        for odds_lo, odds_hi in [(None,None),(5,15),(5,30),(5,50),(10,30),(10,50)]:
            for per_race_max in (None, 1, 2):
                for dp in (0, 1, 2, 3):
                    for ef in (False, True):
                        for ed in (False, True):
                            for ew in (False, True):
                                cands = apply_filters(anaba, score_min, odds_lo, odds_hi,
                                                      per_race_max, dp, ef, ed, ew)
                                s = calc_stats(cands,
                                               f"B{score_min}/C{odds_lo}-{odds_hi}/A{per_race_max}/D{dp}/E{int(ef)}{int(ed)}{int(ew)}")
                                if (s["n"] >= 30 and s["top3_rate"] >= 30
                                        and s["roi_win"] >= 200 and s["n_day"] <= 3):
                                    all_results.append((s, cands))

    all_results.sort(key=lambda x: -x[0]["roi_win"])

    # 重複排除（N と 勝ち馬 idが同一のものは同一パターン）
    seen = set()
    unique = []
    for s, cands in all_results:
        key = (s["n"], s["wins"], s["top3"])
        if key not in seen:
            seen.add(key)
            unique.append((s, cands))

    print(f"  推奨基準を満たすパターン（重複除去後）: {len(unique)}件")
    print()
    for s, _ in unique[:30]:
        print_row(s, min_n=1)

    # ─── ステップ4: 推奨セット ──────────────────────────────────────
    section("【ステップ4-推奨セット】")

    if not unique:
        print("  推奨基準を満たすパターンなし。閾値を緩めて上位を表示:")
        for s, _ in all_results[:5]:
            print_row(s, min_n=1)
    else:
        # ROI最大
        best_roi = unique[0][0]
        # 3着内最大
        best_top3 = sorted(unique, key=lambda x: -x[0]["top3_rate"])[0][0]
        # バランス型（ROI×3着内率 最大、N>=50）
        balanced = sorted([x for x in unique if x[0]["n"] >= 50],
                          key=lambda x: -(x[0]["roi_win"] * x[0]["top3_rate"]))
        best_bal = balanced[0][0] if balanced else unique[min(2, len(unique)-1)][0]

        print(f"  🥇 ROI最大:     {best_roi['label']}")
        print_row(best_roi, min_n=1)
        print(f"\n  🥈 3着内最大:   {best_top3['label']}")
        print_row(best_top3, min_n=1)
        print(f"\n  🥉 バランス型:  {best_bal['label']}")
        print_row(best_bal, min_n=1)

    # ─── 勝ち馬リスト（外れ値確認）─────────────────────────────────
    section("【付録】全パターンの勝ち馬一覧（外れ値確認用: ROI最大パターン）")
    if unique:
        winners = [h for h in unique[0][1] if h["is_win"]]
        winners.sort(key=lambda x: -x["odds"])
        print(f"  対象: {unique[0][0]['label']}  勝利馬 {len(winners)}頭")
        print(f"  {'日付':<10} {'レース':<10} {'馬名':<14} {'人気':<3} {'odds':<7} {'score':<5}  badges")
        print(f"  {'-'*80}")
        for w in winners:
            print(f"  {w['date']:<10} {w['race_label']:<10} {w['name']:<14} {w['pop']:<3d} "
                  f"{w['odds']:<7.1f} {w['score']:<5d}  {' '.join(w['badges'])}")

        print()
        top10_odds = sorted(winners, key=lambda x: -x["odds"])[:5]
        if top10_odds:
            top10_contrib = sum(w["odds"]*100 for w in top10_odds) / (unique[0][0]["n"] * 100) * 100
            all_contrib   = sum(w["odds"]*100 for w in winners)    / (unique[0][0]["n"] * 100) * 100
            print(f"  ⚠ 上位5頭の単勝ROI寄与: {top10_contrib:.1f}% / 全体{all_contrib:.1f}%  "
                  f"（{'⚠ 外れ値依存大' if top10_contrib / all_contrib > 0.6 else '◯ 分散している'}）")


if __name__ == "__main__":
    main()
