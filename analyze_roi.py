#!/usr/bin/env python3
"""
回収率分析スクリプト
pickup_scores.json × race_results.json を突合し、
各種条件での的中率・回収率（単勝ベース）を算出する。
"""
import json
import re
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"


def load_all_data():
    """全日付のpickup_scores + race_resultsを突合して馬リストを返す"""
    horses = []
    dates = sorted(d.name for d in OUTPUT_DIR.iterdir()
                   if d.is_dir() and re.match(r"^\d{8}$", d.name))

    for date in dates:
        ps_path = OUTPUT_DIR / date / "pickup_scores.json"
        rr_path = OUTPUT_DIR / date / "race_results.json"
        if not ps_path.exists() or not rr_path.exists():
            continue

        with open(ps_path, encoding="utf-8") as f:
            ps = json.load(f)
        with open(rr_path, encoding="utf-8") as f:
            rr = json.load(f)

        races = ps.get("races", ps)  # top-level or nested

        for race_label, rdata in races.items():
            if isinstance(rdata, dict) and "error" in rdata:
                continue
            scored = rdata.get("scored", [])
            pop_map = rdata.get("pop_map", {})
            pace = rdata.get("predicted_pace", "")
            race_dist = rdata.get("race_dist")
            results_list = rr.get(race_label, [])
            if not results_list:
                continue

            # 結果をnum→dictにマップ
            res_map = {}
            for r in results_list:
                res_map[str(r["num"])] = r

            # レース番号抽出
            m = re.search(r"(\d+)R", race_label)
            race_num = int(m.group(1)) if m else 0

            # venue抽出
            venue = re.sub(r"\d+R$", "", race_label)

            for h in scored:
                num = str(h["馬番"])
                res = res_map.get(num)
                if not res:
                    continue

                rank = res.get("rank")
                odds = res.get("odds", 0)
                pop = res.get("pop", 0)
                score = h.get("score", 0)
                breakdown = h.get("breakdown", [])
                today_pop = h.get("today_pop", "")

                # breakdown labels
                bd_labels = [b["label"] for b in breakdown]

                # 各ファクターフラグ
                factors = set()
                for b in breakdown:
                    lb = b["label"]
                    if "前走指数" in lb and "90以上" in lb:
                        factors.add("prev_idx_90")
                    elif "前走指数" in lb and "70" in lb:
                        factors.add("prev_idx_70")
                    elif "前走指数レース内1位" in lb:
                        factors.add("prev_idx_race_top")
                    elif "巻き返し" in lb:
                        factors.add("makikaeshi")
                    elif "前走好走" in lb:
                        factors.add("prev_good")
                    elif "逃げ馬" in lb:
                        factors.add("nige")
                    elif "中4週以内" in lb:
                        factors.add("interval_short")
                    elif "同距離" in lb:
                        factors.add("same_dist")
                    elif "ポジション有利" in lb:
                        factors.add("position")
                    elif "データ分析ピックアップ" in lb:
                        factors.add("data_pickup")
                    elif "各データ上位" in lb:
                        factors.add("top3_data")
                    elif "出走馬分析" in lb:
                        factors.add("analysis")

                try:
                    pop_val = int(today_pop) if today_pop else pop
                except (ValueError, TypeError):
                    pop_val = pop

                horses.append({
                    "date": date,
                    "race_label": race_label,
                    "venue": venue,
                    "race_num": race_num,
                    "num": num,
                    "name": h.get("馬名", ""),
                    "score": score,
                    "rank_result": rank,
                    "odds": odds,
                    "pop": pop_val,
                    "pace": pace,
                    "race_dist": race_dist,
                    "factors": factors,
                    "bd_labels": bd_labels,
                    "is_win": rank == 1,
                    "is_top3": rank is not None and rank <= 3,
                })

    return horses


def calc_stats(subset, label=""):
    """馬リストの統計を返す"""
    n = len(subset)
    if n == 0:
        return None
    wins = sum(1 for h in subset if h["is_win"])
    top3 = sum(1 for h in subset if h["is_top3"])
    # 単勝回収率
    roi_win = sum(h["odds"] * 100 for h in subset if h["is_win"]) / (n * 100) * 100
    # 複勝回収率（概算: 1着=odds*0.4, 2着=odds*0.3, 3着=odds*0.25）
    # 正確な複勝オッズがないので単勝ベースで推定
    return {
        "label": label,
        "n": n,
        "wins": wins,
        "win_rate": wins / n * 100,
        "top3": top3,
        "top3_rate": top3 / n * 100,
        "roi_win": roi_win,
    }


def print_stats(stats, min_n=5):
    if stats is None or stats["n"] < min_n:
        return
    s = stats
    print(f"  {s['label']:<50s}  N={s['n']:>4d}  "
          f"勝率{s['win_rate']:5.1f}%  3着内{s['top3_rate']:5.1f}%  "
          f"単勝回収率{s['roi_win']:6.1f}%")


def main():
    horses = load_all_data()
    print(f"総データ: {len(horses)}頭 / {len(set(h['date'] for h in horses))}日")
    print()

    # ──────────────────────────────────────
    # 1. スコア別
    # ──────────────────────────────────────
    print("=" * 100)
    print("【1】スコア別")
    print("=" * 100)
    for threshold in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
        sub = [h for h in horses if h["score"] >= threshold]
        print_stats(calc_stats(sub, f"スコア{threshold}pt以上"))

    print()

    # ──────────────────────────────────────
    # 2. スコア帯別（ちょうどN点）
    # ──────────────────────────────────────
    print("=" * 100)
    print("【2】スコア帯別（ちょうどN点）")
    print("=" * 100)
    for s in range(0, 15):
        sub = [h for h in horses if h["score"] == s]
        print_stats(calc_stats(sub, f"スコア={s}pt"))

    print()

    # ──────────────────────────────────────
    # 3. 人気別
    # ──────────────────────────────────────
    print("=" * 100)
    print("【3】人気別（3指数重複馬のみ）")
    print("=" * 100)
    for lo, hi in [(1,1),(2,2),(3,3),(1,3),(4,6),(7,9),(10,18),(1,6),(4,9),(5,10)]:
        sub = [h for h in horses if lo <= h["pop"] <= hi]
        label = f"{lo}番人気" if lo == hi else f"{lo}-{hi}番人気"
        print_stats(calc_stats(sub, label))

    print()

    # ──────────────────────────────────────
    # 4. スコア×人気 クロス分析
    # ──────────────────────────────────────
    print("=" * 100)
    print("【4】スコア×人気 クロス分析")
    print("=" * 100)
    for score_min in [1, 3, 5, 7]:
        for lo, hi in [(1,3),(4,6),(7,9),(10,18),(4,9),(5,10)]:
            sub = [h for h in horses if h["score"] >= score_min and lo <= h["pop"] <= hi]
            label = f"スコア{score_min}+pt × {lo}-{hi}番人気"
            print_stats(calc_stats(sub, label))

    print()

    # ──────────────────────────────────────
    # 5. 個別ファクター別
    # ──────────────────────────────────────
    print("=" * 100)
    print("【5】個別ファクター別")
    print("=" * 100)
    factor_names = {
        "prev_idx_90": "前走指数90以上",
        "prev_idx_70": "前走指数70-89",
        "prev_idx_race_top": "前走指数レース内1位",
        "makikaeshi": "巻き返し馬",
        "prev_good": "前走好走",
        "nige": "逃げ馬",
        "interval_short": "中4週以内",
        "same_dist": "同距離前走",
        "position": "ポジション有利",
        "data_pickup": "データ分析ピックアップ",
        "top3_data": "各データ上位3頭",
        "analysis": "出走馬分析",
    }
    for fkey, fname in factor_names.items():
        sub = [h for h in horses if fkey in h["factors"]]
        print_stats(calc_stats(sub, fname))

    print()

    # ──────────────────────────────────────
    # 6. ファクター組合せ（2因子AND）
    # ──────────────────────────────────────
    print("=" * 100)
    print("【6】ファクター2因子AND")
    print("=" * 100)
    keys = list(factor_names.keys())
    for i in range(len(keys)):
        for j in range(i+1, len(keys)):
            sub = [h for h in horses if keys[i] in h["factors"] and keys[j] in h["factors"]]
            label = f"{factor_names[keys[i]]} + {factor_names[keys[j]]}"
            print_stats(calc_stats(sub, label), min_n=3)

    print()

    # ──────────────────────────────────────
    # 7. ペース別
    # ──────────────────────────────────────
    print("=" * 100)
    print("【7】予想ペース別")
    print("=" * 100)
    for pace in ["H", "M", "S"]:
        sub = [h for h in horses if h["pace"] == pace]
        print_stats(calc_stats(sub, f"ペース={pace}"))

    # ペース×ファクター
    print()
    for pace in ["H", "M", "S"]:
        for fkey in ["nige", "position", "prev_idx_90", "makikaeshi"]:
            sub = [h for h in horses if h["pace"] == pace and fkey in h["factors"]]
            label = f"ペース={pace} × {factor_names[fkey]}"
            print_stats(calc_stats(sub, label), min_n=3)

    print()

    # ──────────────────────────────────────
    # 8. 距離別
    # ──────────────────────────────────────
    print("=" * 100)
    print("【8】距離帯別")
    print("=" * 100)
    for dlo, dhi, dlabel in [(0,1200,"短距離(~1200m)"), (1201,1600,"マイル(1201-1600m)"),
                              (1601,2000,"中距離(1601-2000m)"), (2001,9999,"長距離(2001m~)")]:
        sub = [h for h in horses if h["race_dist"] and dlo <= h["race_dist"] <= dhi]
        print_stats(calc_stats(sub, dlabel))

    print()

    # ──────────────────────────────────────
    # 9. レース番号別
    # ──────────────────────────────────────
    print("=" * 100)
    print("【9】レース番号帯別")
    print("=" * 100)
    for rlo, rhi, rlabel in [(1,3,"1-3R(未勝利)"), (4,6,"4-6R"), (7,9,"7-9R"),
                              (10,12,"10-12R(メイン)")]:
        sub = [h for h in horses if rlo <= h["race_num"] <= rhi]
        print_stats(calc_stats(sub, rlabel))

    print()

    # ──────────────────────────────────────
    # 10. 高回収率パターン探索
    # ──────────────────────────────────────
    print("=" * 100)
    print("【10】高回収率パターン探索（回収率80%以上 & N>=5）")
    print("=" * 100)
    patterns = []

    # スコア×人気×ファクター
    for score_min in [0, 1, 3, 5]:
        for lo, hi in [(1,3),(4,6),(7,9),(10,18),(1,6),(4,9),(5,10),(1,18)]:
            for fkey, fname in factor_names.items():
                sub = [h for h in horses if h["score"] >= score_min
                       and lo <= h["pop"] <= hi and fkey in h["factors"]]
                s = calc_stats(sub, f"score{score_min}+ × {lo}-{hi}人気 × {fname}")
                if s and s["n"] >= 5 and s["roi_win"] >= 80:
                    patterns.append(s)

    # スコア×ペース
    for score_min in [0, 1, 3, 5]:
        for pace in ["H", "M", "S"]:
            sub = [h for h in horses if h["score"] >= score_min and h["pace"] == pace]
            s = calc_stats(sub, f"score{score_min}+ × ペース{pace}")
            if s and s["n"] >= 5 and s["roi_win"] >= 80:
                patterns.append(s)

    # スコア×距離
    for score_min in [0, 1, 3, 5]:
        for dlo, dhi, dlabel in [(0,1200,"短距離"), (1201,1600,"マイル"),
                                  (1601,2000,"中距離"), (2001,9999,"長距離")]:
            sub = [h for h in horses if h["score"] >= score_min
                   and h["race_dist"] and dlo <= h["race_dist"] <= dhi]
            s = calc_stats(sub, f"score{score_min}+ × {dlabel}")
            if s and s["n"] >= 5 and s["roi_win"] >= 80:
                patterns.append(s)

    # スコア×人気×ペース
    for score_min in [0, 1, 3, 5]:
        for lo, hi in [(1,3),(4,6),(7,9),(4,9),(5,10)]:
            for pace in ["H", "M", "S"]:
                sub = [h for h in horses if h["score"] >= score_min
                       and lo <= h["pop"] <= hi and h["pace"] == pace]
                s = calc_stats(sub, f"score{score_min}+ × {lo}-{hi}人気 × ペース{pace}")
                if s and s["n"] >= 5 and s["roi_win"] >= 80:
                    patterns.append(s)

    # スコア×人気×距離
    for score_min in [0, 1, 3, 5]:
        for lo, hi in [(1,3),(4,6),(7,9),(4,9),(5,10)]:
            for dlo, dhi, dlabel in [(0,1200,"短距離"), (1201,1600,"マイル"),
                                      (1601,2000,"中距離"), (2001,9999,"長距離")]:
                sub = [h for h in horses if h["score"] >= score_min
                       and lo <= h["pop"] <= hi
                       and h["race_dist"] and dlo <= h["race_dist"] <= dhi]
                s = calc_stats(sub, f"score{score_min}+ × {lo}-{hi}人気 × {dlabel}")
                if s and s["n"] >= 5 and s["roi_win"] >= 80:
                    patterns.append(s)

    # ファクター×人気
    for fkey, fname in factor_names.items():
        for lo, hi in [(1,3),(4,6),(7,9),(10,18),(4,9),(5,10)]:
            sub = [h for h in horses if fkey in h["factors"] and lo <= h["pop"] <= hi]
            s = calc_stats(sub, f"{fname} × {lo}-{hi}人気")
            if s and s["n"] >= 5 and s["roi_win"] >= 80:
                patterns.append(s)

    # レース番号×スコア×人気
    for rlo, rhi, rlabel in [(1,3,"1-3R"), (4,6,"4-6R"), (7,9,"7-9R"), (10,12,"10-12R")]:
        for score_min in [0, 1, 3, 5]:
            for lo, hi in [(1,3),(4,6),(7,9),(1,6),(4,9)]:
                sub = [h for h in horses if rlo <= h["race_num"] <= rhi
                       and h["score"] >= score_min and lo <= h["pop"] <= hi]
                s = calc_stats(sub, f"{rlabel} × score{score_min}+ × {lo}-{hi}人気")
                if s and s["n"] >= 5 and s["roi_win"] >= 80:
                    patterns.append(s)

    # 重複除去してソート
    seen = set()
    unique = []
    for p in patterns:
        if p["label"] not in seen:
            seen.add(p["label"])
            unique.append(p)
    unique.sort(key=lambda x: x["roi_win"], reverse=True)

    for s in unique[:50]:
        print(f"  {s['label']:<55s}  N={s['n']:>4d}  "
              f"勝率{s['win_rate']:5.1f}%  3着内{s['top3_rate']:5.1f}%  "
              f"単勝回収率{s['roi_win']:6.1f}%")

    print()
    print(f"  ※ 回収率80%以上パターン: {len(unique)}件")


if __name__ == "__main__":
    main()
