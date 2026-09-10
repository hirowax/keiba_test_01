#!/usr/bin/env python3
"""
穴馬ファクターのアウトオブサンプル検証
38日を前半19日（探索）/後半18日（検証）に分割し、
両期間で安定するファクターのみを「再現性あり」として残す
"""
import json
import re
from pathlib import Path
from datetime import datetime
from analyze_anaba_filters import calc_v2, calc_stats, extract_class, extract_age_class, is_female_only

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"


def load_all_with_prev():
    """load_allを再実装してprev_*フィールドも保持"""
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
                prev_pop  = ti(prev.get("prev_pop", ""))
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
                    "prev_rank": prev_rank,
                    "prev_idx": prev_idx,
                    "prev_pop": prev_pop,
                    "race_surface": cond.get("surface", ""),
                    "is_win": rank == 1,
                    "is_top3": 1 <= rank <= 3,
                })
    return horses


def split_train_test(horses):
    """日付で前後に2分割"""
    dates = sorted(set(h["date"] for h in horses))
    mid = len(dates) // 2
    train_dates = set(dates[:mid])
    test_dates = set(dates[mid:])
    train = [h for h in horses if h["date"] in train_dates]
    test = [h for h in horses if h["date"] in test_dates]
    return train, test, sorted(train_dates), sorted(test_dates)


def stats_for(cands):
    """簡易統計"""
    if not cands:
        return {"n": 0, "wins": 0, "top3": 0, "win_rate": 0, "top3_rate": 0, "roi_win": 0}
    n = len(cands)
    wins = sum(1 for h in cands if h["is_win"])
    top3 = sum(1 for h in cands if h["is_top3"])
    return {
        "n": n, "wins": wins, "top3": top3,
        "win_rate": wins/n*100, "top3_rate": top3/n*100,
        "roi_win": sum(h["odds"]*100 for h in cands if h["is_win"]) / (n*100) * 100,
    }


def evaluate(label, train, test, factor_fn, min_n_train=15, min_n_test=10):
    """ファクター関数を train/test 双方で評価。再現性判定付き"""
    t_cands = [h for h in train if factor_fn(h)]
    v_cands = [h for h in test if factor_fn(h)]
    ts = stats_for(t_cands)
    vs = stats_for(v_cands)

    # 再現性判定:
    #   P1のROI>=150% かつ P2のROI>=100% かつ 両方N十分 = ◎
    #   P1のROI>=100% かつ P2のROI>=80% かつ 両方N十分 = ◯
    #   P1で良いがP2で崩壊（ROI<P1の半分以下）= ✗ オーバーフィット
    #   両方N不足 = -
    if ts["n"] < min_n_train or vs["n"] < min_n_test:
        verdict = "- N不足"
    elif ts["roi_win"] >= 150 and vs["roi_win"] >= 100:
        verdict = "◎ 再現◎"
    elif ts["roi_win"] >= 100 and vs["roi_win"] >= 80:
        verdict = "◯ 再現○"
    elif vs["roi_win"] < ts["roi_win"] * 0.5 and ts["roi_win"] >= 150:
        verdict = "✗ 過剰適合"
    elif vs["roi_win"] < 50:
        verdict = "✗ 検証で破綻"
    else:
        verdict = "△ 微妙"

    return label, ts, vs, verdict


def print_eval(rows):
    print(f"  {'ファクター':<48s} {'N(P1)':<6s} {'ROI(P1)':<8s} {'top3%(P1)':<9s}  | "
          f"{'N(P2)':<6s} {'ROI(P2)':<8s} {'top3%(P2)':<9s}  判定")
    print(f"  {'-'*125}")
    # 判定で並び替え
    order = {"◎": 0, "◯": 1, "△": 2, "-": 3, "✗": 4}
    rows.sort(key=lambda r: order.get(r[3][0], 9))
    for label, ts, vs, verdict in rows:
        print(f"  {label:<48s} "
              f"{ts['n']:<6d} {ts['roi_win']:<7.1f}% {ts['top3_rate']:<7.1f}%   | "
              f"{vs['n']:<6d} {vs['roi_win']:<7.1f}% {vs['top3_rate']:<7.1f}%   {verdict}")


def section(title):
    print()
    print("=" * 130)
    print(f"  {title}")
    print("=" * 130)


def main():
    print("データ読み込み中...")
    horses = load_all_with_prev()
    train, test, td, vd = split_train_test(horses)

    print(f"  探索期間 P1: {td[0]} 〜 {td[-1]} ({len(td)}日 / {len(train)}頭)")
    print(f"  検証期間 P2: {vd[0]} 〜 {vd[-1]} ({len(vd)}日 / {len(test)}頭)")

    # ─── 単独ファクター ────────────────────────────────────────────
    section("【単独ファクター】P1で発見、P2で検証")
    rows = []
    rows.append(evaluate("前走1着", train, test, lambda h: h["prev_rank"] == 1))
    rows.append(evaluate("前走2着", train, test, lambda h: h["prev_rank"] == 2))
    rows.append(evaluate("前走3着", train, test, lambda h: h["prev_rank"] == 3))
    rows.append(evaluate("前走1-3着", train, test, lambda h: 1 <= h["prev_rank"] <= 3))
    rows.append(evaluate("前走指数90+", train, test,
                         lambda h: h["prev_idx"] is not None and h["prev_idx"] >= 90))
    rows.append(evaluate("前走指数80+", train, test,
                         lambda h: h["prev_idx"] is not None and h["prev_idx"] >= 80))
    rows.append(evaluate("前走指数70+", train, test,
                         lambda h: h["prev_idx"] is not None and h["prev_idx"] >= 70))
    rows.append(evaluate("馬体平行(±2kg)", train, test,
                         lambda h: -2 <= h["hw_diff"] <= 2))
    rows.append(evaluate("馬体減(-3〜-9kg)", train, test,
                         lambda h: -9 <= h["hw_diff"] <= -3))
    rows.append(evaluate("馬体増(+3〜+9kg)", train, test,
                         lambda h: 3 <= h["hw_diff"] <= 9))
    rows.append(evaluate("馬体大幅減(-10kg以下)", train, test,
                         lambda h: h["hw_diff"] <= -10))
    rows.append(evaluate("距離延長+200m以上", train, test,
                         lambda h: h["dist_diff"] >= 200))
    rows.append(evaluate("距離短縮-200m以上", train, test,
                         lambda h: h["dist_diff"] <= -200))
    rows.append(evaluate("芝", train, test, lambda h: "芝" not in h.get("race_surface","") or True))  # surfaceフィールドを使わずスキップ
    rows.append(evaluate("クラス=未勝利", train, test, lambda h: h["class_lvl"] == "未勝利"))
    rows.append(evaluate("クラス=1勝", train, test, lambda h: h["class_lvl"] == "1勝"))
    rows.append(evaluate("クラス=2勝", train, test, lambda h: h["class_lvl"] == "2勝"))
    rows.append(evaluate("クラス=3勝", train, test, lambda h: h["class_lvl"] == "3勝"))
    rows.append(evaluate("クラス=OP+", train, test, lambda h: h["class_lvl"] == "OP+"))
    rows.append(evaluate("3歳限定戦", train, test, lambda h: h["age_class"] == "3歳限定"))
    rows.append(evaluate("3歳以上戦", train, test, lambda h: h["age_class"] == "3歳以上"))
    rows.append(evaluate("牝馬限定戦", train, test, lambda h: h["female_only"]))
    rows.append(evaluate("混合戦", train, test, lambda h: not h["female_only"]))
    # 「芝」「ダ」フィルタを正しく追加
    rows = [r for r in rows if r[0] != "芝"]
    print_eval(rows)

    # ─── 2因子AND ──────────────────────────────────────────────────
    section("【2因子AND】両期間で安定するか")
    rows = []
    rows.append(evaluate("前走1着 × 前走指数80+", train, test,
                         lambda h: h["prev_rank"] == 1 and h["prev_idx"] and h["prev_idx"] >= 80))
    rows.append(evaluate("前走1着 × 前走指数70+", train, test,
                         lambda h: h["prev_rank"] == 1 and h["prev_idx"] and h["prev_idx"] >= 70))
    rows.append(evaluate("前走1着 × 馬体平行", train, test,
                         lambda h: h["prev_rank"] == 1 and -2 <= h["hw_diff"] <= 2))
    rows.append(evaluate("前走1着 × 未勝利戦", train, test,
                         lambda h: h["prev_rank"] == 1 and h["class_lvl"] == "未勝利"))
    rows.append(evaluate("前走1着 × 3歳限定戦", train, test,
                         lambda h: h["prev_rank"] == 1 and h["age_class"] == "3歳限定"))
    rows.append(evaluate("前走1-3着 × 前走指数80+", train, test,
                         lambda h: 1 <= h["prev_rank"] <= 3 and h["prev_idx"] and h["prev_idx"] >= 80))
    rows.append(evaluate("前走1-3着 × 馬体平行", train, test,
                         lambda h: 1 <= h["prev_rank"] <= 3 and -2 <= h["hw_diff"] <= 2))
    rows.append(evaluate("前走1-3着 × 馬体増(+3〜+9kg)", train, test,
                         lambda h: 1 <= h["prev_rank"] <= 3 and 3 <= h["hw_diff"] <= 9))
    rows.append(evaluate("前走1-3着 × 馬体減(-3〜-9kg)", train, test,
                         lambda h: 1 <= h["prev_rank"] <= 3 and -9 <= h["hw_diff"] <= -3))
    rows.append(evaluate("前走1-3着 × 未勝利", train, test,
                         lambda h: 1 <= h["prev_rank"] <= 3 and h["class_lvl"] == "未勝利"))
    rows.append(evaluate("前走1-3着 × 1勝", train, test,
                         lambda h: 1 <= h["prev_rank"] <= 3 and h["class_lvl"] == "1勝"))
    rows.append(evaluate("前走指数80+ × 馬体平行", train, test,
                         lambda h: h["prev_idx"] and h["prev_idx"] >= 80 and -2 <= h["hw_diff"] <= 2))
    print_eval(rows)

    # ─── 既存3戦略の再現性 ─────────────────────────────────────────
    section("【既存3戦略】train/testで再現するか")
    rows = []
    rows.append(evaluate("score≥3 全候補", train, test, lambda h: h["score"] >= 3))
    rows.append(evaluate("score≥4 全候補 (B4)", train, test, lambda h: h["score"] >= 4))
    rows.append(evaluate("score≥5 全候補 (B5)", train, test, lambda h: h["score"] >= 5))
    rows.append(evaluate("score≥3 + 牝馬除外 + 1レース1頭(無理)", train, test,
                         lambda h: h["score"] >= 3 and not h["female_only"]))
    rows.append(evaluate("score≥4 + 牝馬除外", train, test,
                         lambda h: h["score"] >= 4 and not h["female_only"]))
    rows.append(evaluate("score≥3 + オッズ5-15倍 + 牝馬除外 (3着内最大型)", train, test,
                         lambda h: h["score"] >= 3 and 5 <= h["odds"] <= 15 and not h["female_only"]))
    rows.append(evaluate("特注: 未勝利×前走1着", train, test,
                         lambda h: "未勝利×前走1着" in h["badges"]))
    rows.append(evaluate("特注: 3歳限定×前走1着", train, test,
                         lambda h: "3歳限定×前走1着" in h["badges"]))
    rows.append(evaluate("特注: 未勝利OR3歳限定×前走1着 (D1)", train, test,
                         lambda h: bool(h["badges"])))
    print_eval(rows)

    # ─── 結論サマリ ─────────────────────────────────────────────────
    section("【結論サマリ】両期間でROI>=100%を維持できたファクター")
    print()
    survivors = []
    candidates = [
        ("前走1着", lambda h: h["prev_rank"] == 1),
        ("前走1-3着", lambda h: 1 <= h["prev_rank"] <= 3),
        ("前走指数90+", lambda h: h["prev_idx"] and h["prev_idx"] >= 90),
        ("前走指数80+", lambda h: h["prev_idx"] and h["prev_idx"] >= 80),
        ("馬体平行", lambda h: -2 <= h["hw_diff"] <= 2),
        ("前走1着×指数80+", lambda h: h["prev_rank"] == 1 and h["prev_idx"] and h["prev_idx"] >= 80),
        ("前走1着×指数70+", lambda h: h["prev_rank"] == 1 and h["prev_idx"] and h["prev_idx"] >= 70),
        ("前走1着×馬体平行", lambda h: h["prev_rank"] == 1 and -2 <= h["hw_diff"] <= 2),
        ("前走1着×未勝利", lambda h: h["prev_rank"] == 1 and h["class_lvl"] == "未勝利"),
        ("前走1着×3歳限定", lambda h: h["prev_rank"] == 1 and h["age_class"] == "3歳限定"),
        ("前走1-3着×指数80+", lambda h: 1 <= h["prev_rank"] <= 3 and h["prev_idx"] and h["prev_idx"] >= 80),
        ("score≥3", lambda h: h["score"] >= 3),
        ("score≥4", lambda h: h["score"] >= 4),
        ("score≥5", lambda h: h["score"] >= 5),
    ]
    for label, fn in candidates:
        t = stats_for([h for h in train if fn(h)])
        v = stats_for([h for h in test if fn(h)])
        if t["n"] < 15 or v["n"] < 10: continue
        if t["roi_win"] >= 100 and v["roi_win"] >= 100:
            survivors.append((label, t, v))

    if survivors:
        print(f"  両期間でROI≥100%を維持したファクター: {len(survivors)}件")
        print()
        print(f"  {'ファクター':<32s} {'N(P1)':<6s} {'ROI(P1)':<9s} {'N(P2)':<6s} {'ROI(P2)':<9s} {'top3%(P2)':<9s}")
        print(f"  {'-'*100}")
        survivors.sort(key=lambda x: -min(x[1]["roi_win"], x[2]["roi_win"]))
        for label, t, v in survivors:
            print(f"  {label:<32s} "
                  f"{t['n']:<6d} {t['roi_win']:<8.1f}% "
                  f"{v['n']:<6d} {v['roi_win']:<8.1f}% {v['top3_rate']:<7.1f}%")
    else:
        print("  ⚠ 両期間でROI≥100%を維持したファクターは存在しなかった")
        print("  → 現在の38日では再現性のあるファクターを抽出できない")

    # ─── 反対のチェック: P1で強いがP2で破綻したファクター ──────────
    section("【失敗例】P1では魅力的だがP2で崩壊したファクター（過剰適合）")
    print()
    failures = []
    for label, fn in candidates:
        t = stats_for([h for h in train if fn(h)])
        v = stats_for([h for h in test if fn(h)])
        if t["n"] < 15 or v["n"] < 10: continue
        if t["roi_win"] >= 200 and v["roi_win"] < 100:
            failures.append((label, t, v))
    if failures:
        for label, t, v in failures:
            print(f"  {label:<32s} P1: ROI {t['roi_win']:.1f}% (N={t['n']}) → P2: ROI {v['roi_win']:.1f}% (N={v['n']}) 💀")
    else:
        print("  該当なし")

    # ─── 最終提言 ──────────────────────────────────────────────────
    section("【最終提言】")
    print()
    print("  検証結果に基づく実装方針:")
    print()
    if survivors:
        best = survivors[0]
        print(f"  🟢 最も再現性のあるファクター: 「{best[0]}」")
        print(f"     P1: ROI {best[1]['roi_win']:.1f}% (N={best[1]['n']})")
        print(f"     P2: ROI {best[2]['roi_win']:.1f}% (N={best[2]['n']}) 3着内{best[2]['top3_rate']:.1f}%")
        print()
        print(f"  ただし、これは19日 vs 18日という短期同士の比較。")
        print(f"  本番に組み込む前に、さらに別期間でも検証する必要がある。")
    else:
        print(f"  🔴 現データでは再現性のあるファクターが見つからない")
        print(f"  → 実装は完全保留すべき")
    print()
    print(f"  推奨アクション:")
    print(f"  1. 穴馬スコア実装は保留（既存「anaba」リストの強化はしない）")
    print(f"  2. データ蓄積を続け、少なくとも90日分（あと2-3ヶ月）になってから再検証")
    print(f"  3. 「前走1着 × 5+人気」のような単純ファクターを「参考表示」として追加するのは可")
    print(f"     ただし「期待ROI」のような数値は表示しない")
    print(f"  4. 馬券の自動推奨ラベルとしては絶対に使わない")


if __name__ == "__main__":
    main()
