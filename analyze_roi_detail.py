#!/usr/bin/env python3
"""
高回収率パターンの再現性を検証する
各パターンの個別馬リスト・外れ値影響・日別分布を出力
"""
import json
import re
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"


def load_all_data():
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
        races = ps.get("races", ps)
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
            res_map = {str(r["num"]): r for r in results_list}
            m = re.search(r"(\d+)R", race_label)
            race_num = int(m.group(1)) if m else 0
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
                jockey = res.get("jockey", "")
                trainer = res.get("trainer", "")

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
                    "is_win": rank == 1,
                    "is_top3": rank is not None and rank <= 3,
                    "jockey": jockey,
                    "trainer": trainer,
                })
    return horses


def detail_analysis(horses, condition_fn, label):
    """条件に合う馬の全リスト＋外れ値除去分析"""
    sub = [h for h in horses if condition_fn(h)]
    if not sub:
        print(f"\n{label}: 該当なし")
        return

    n = len(sub)
    wins = [h for h in sub if h["is_win"]]
    top3 = [h for h in sub if h["is_top3"]]
    total_bet = n * 100
    total_return = sum(h["odds"] * 100 for h in wins)
    roi = total_return / total_bet * 100

    print(f"\n{'='*90}")
    print(f"【{label}】  N={n}  勝率{len(wins)/n*100:.1f}%  3着内{len(top3)/n*100:.1f}%  単勝回収率{roi:.1f}%")
    print(f"{'='*90}")

    # 全馬リスト
    print(f"\n  {'日付':<10} {'レース':<10} {'馬名':<12} {'人気':>4} {'着順':>4} {'オッズ':>8} {'スコア':>6} {'騎手':<8} {'結果':>6}")
    print(f"  {'-'*80}")
    sub_sorted = sorted(sub, key=lambda h: (h["date"], h["race_label"]))
    for h in sub_sorted:
        result_mark = ""
        if h["is_win"]:
            result_mark = f"◎+{h['odds']*100:.0f}円"
        elif h["is_top3"]:
            result_mark = "○3着内"
        else:
            result_mark = "×"
        print(f"  {h['date']:<10} {h['race_label']:<10} {h['name']:<12} "
              f"{h['pop']:>4} {h['rank_result'] or '?':>4} {h['odds']:>8.1f} "
              f"{h['score']:>6} {h['jockey']:<8} {result_mark}")

    # 日別分布
    print(f"\n  ── 日別分布 ──")
    by_date = defaultdict(list)
    for h in sub:
        by_date[h["date"]].append(h)
    for d in sorted(by_date):
        dh = by_date[d]
        d_wins = sum(1 for h in dh if h["is_win"])
        d_top3 = sum(1 for h in dh if h["is_top3"])
        d_ret = sum(h["odds"] * 100 for h in dh if h["is_win"])
        d_bet = len(dh) * 100
        d_roi = d_ret / d_bet * 100 if d_bet else 0
        print(f"  {d}: {len(dh)}頭 勝{d_wins} 3着内{d_top3} 回収率{d_roi:.0f}%")

    # 外れ値除去（最大オッズの勝ち馬を除外）
    if wins:
        max_odds_win = max(wins, key=lambda h: h["odds"])
        sub_excl = [h for h in sub if h != max_odds_win]
        wins_excl = [h for h in sub_excl if h["is_win"]]
        if sub_excl:
            total_bet_excl = len(sub_excl) * 100
            total_return_excl = sum(h["odds"] * 100 for h in wins_excl)
            roi_excl = total_return_excl / total_bet_excl * 100
            print(f"\n  ── 最大配当除外（{max_odds_win['name']} {max_odds_win['odds']}倍を除外）──")
            print(f"  N={len(sub_excl)} 勝率{len(wins_excl)/len(sub_excl)*100:.1f}% 回収率{roi_excl:.1f}%")

    # 人気帯別内訳
    print(f"\n  ── 人気帯別内訳 ──")
    for lo, hi in [(1,3),(4,6),(7,9),(10,99)]:
        ph = [h for h in sub if lo <= h["pop"] <= hi]
        if not ph:
            continue
        pw = sum(1 for h in ph if h["is_win"])
        pt = sum(1 for h in ph if h["is_top3"])
        pr = sum(h["odds"] * 100 for h in ph if h["is_win"]) / (len(ph) * 100) * 100
        print(f"  {lo}-{hi}番人気: {len(ph)}頭 勝{pw} 3着内{pt} 回収率{pr:.0f}%")


def main():
    horses = load_all_data()
    print(f"総データ: {len(horses)}頭")

    # パターン1: 前走指数70-89 + 中4週以内
    detail_analysis(horses,
        lambda h: "prev_idx_70" in h["factors"] and "interval_short" in h["factors"],
        "前走指数70-89 + 中4週以内")

    # パターン2: 中4週以内 + 同距離前走
    detail_analysis(horses,
        lambda h: "interval_short" in h["factors"] and "same_dist" in h["factors"],
        "中4週以内 + 同距離前走")

    # パターン3: 中4週以内（単体）
    detail_analysis(horses,
        lambda h: "interval_short" in h["factors"],
        "中4週以内（単体）")

    # パターン4: 前走指数70-89 + 同距離前走
    detail_analysis(horses,
        lambda h: "prev_idx_70" in h["factors"] and "same_dist" in h["factors"],
        "前走指数70-89 + 同距離前走")

    # パターン5: 前走好走 + データ分析ピックアップ
    detail_analysis(horses,
        lambda h: "prev_good" in h["factors"] and "data_pickup" in h["factors"],
        "前走好走 + データ分析ピックアップ")

    # パターン6: 4-6番人気
    detail_analysis(horses,
        lambda h: 4 <= h["pop"] <= 6,
        "4-6番人気（全体）")

    # パターン7: スコア3+ × 4-6番人気
    detail_analysis(horses,
        lambda h: h["score"] >= 3 and 4 <= h["pop"] <= 6,
        "スコア3pt以上 × 4-6番人気")

    # パターン8: 前走指数70-89 + 前走指数レース内1位
    detail_analysis(horses,
        lambda h: "prev_idx_70" in h["factors"] and "prev_idx_race_top" in h["factors"],
        "前走指数70-89 + レース内1位")

    # パターン9: 前走好走 + 中4週以内
    detail_analysis(horses,
        lambda h: "prev_good" in h["factors"] and "interval_short" in h["factors"],
        "前走好走 + 中4週以内")

    # パターン10: 7-9R帯
    detail_analysis(horses,
        lambda h: 7 <= h["race_num"] <= 9,
        "7-9R帯（全体）")

    # 騎手別集計（勝ち馬がいる騎手のみ）
    print(f"\n{'='*90}")
    print("【騎手別回収率】（3指数重複馬の騎乗時、勝利あり & N>=3）")
    print(f"{'='*90}")
    by_jockey = defaultdict(list)
    for h in horses:
        if h["jockey"]:
            by_jockey[h["jockey"]].append(h)
    jockey_stats = []
    for jname, jh in by_jockey.items():
        if len(jh) < 3:
            continue
        jw = sum(1 for h in jh if h["is_win"])
        if jw == 0:
            continue
        jt = sum(1 for h in jh if h["is_top3"])
        jr = sum(h["odds"] * 100 for h in jh if h["is_win"]) / (len(jh) * 100) * 100
        jockey_stats.append((jname, len(jh), jw, jt, jr))
    jockey_stats.sort(key=lambda x: x[4], reverse=True)
    for jname, jn, jw, jt, jr in jockey_stats[:30]:
        print(f"  {jname:<10} N={jn:>3} 勝{jw:>2} 3着内{jt:>3} 回収率{jr:>7.1f}%")

    # 調教師別
    print(f"\n{'='*90}")
    print("【調教師別回収率】（3指数重複馬の管理時、勝利あり & N>=3）")
    print(f"{'='*90}")
    by_trainer = defaultdict(list)
    for h in horses:
        if h["trainer"]:
            by_trainer[h["trainer"]].append(h)
    trainer_stats = []
    for tname, th in by_trainer.items():
        if len(th) < 3:
            continue
        tw = sum(1 for h in th if h["is_win"])
        if tw == 0:
            continue
        tt = sum(1 for h in th if h["is_top3"])
        tr = sum(h["odds"] * 100 for h in th if h["is_win"]) / (len(th) * 100) * 100
        trainer_stats.append((tname, len(th), tw, tt, tr))
    trainer_stats.sort(key=lambda x: x[4], reverse=True)
    for tname, tn, tw, tt, tr in trainer_stats[:30]:
        print(f"  {tname:<14} N={tn:>3} 勝{tw:>2} 3着内{tt:>3} 回収率{tr:>7.1f}%")


if __name__ == "__main__":
    main()
