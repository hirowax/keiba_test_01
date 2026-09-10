#!/usr/bin/env python3
"""
穴馬発見ファクター検証スクリプト v2
3指数重複馬を除外したプールで再検証 + 芝ダート・クラス・年齢区分を追加
"""
import json
import re
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

ANABA_POP_THRESHOLD = 5


def extract_class(class_name: str) -> str:
    """class_nameからクラス区分を抽出"""
    if not class_name:
        return "不明"
    if "新馬" in class_name:
        return "新馬"
    if "未勝利" in class_name:
        return "未勝利"
    if "オープン" in class_name or "ＯＰ" in class_name or "G1" in class_name or "G2" in class_name or "G3" in class_name:
        return "OP+"
    if "３勝" in class_name or "3勝" in class_name:
        return "3勝"
    if "２勝" in class_name or "2勝" in class_name:
        return "2勝"
    if "１勝" in class_name or "1勝" in class_name:
        return "1勝"
    return "その他"


def extract_age_class(class_name: str) -> str:
    """3歳限定 vs 3歳以上"""
    if not class_name:
        return "不明"
    if "２歳" in class_name or "2歳" in class_name:
        return "2歳"
    if "３歳以上" in class_name or "3歳以上" in class_name or "４歳以上" in class_name or "4歳以上" in class_name:
        return "3歳以上"
    if "３歳" in class_name or "3歳" in class_name:
        return "3歳限定"
    return "不明"


def is_female_only(class_name: str) -> bool:
    return "牝" in class_name


def load_all():
    """全日付のrace_results+horse_db+pickup_scoresから全馬データ構築。3指数重複馬は除外フラグ付与"""
    horse_db = json.loads((OUTPUT_DIR / "horse_db.json").read_text(encoding="utf-8"))

    horses = []
    dates = sorted(d.name for d in OUTPUT_DIR.iterdir()
                   if d.is_dir() and re.match(r"^\d{8}$", d.name))

    for date in dates:
        rr_path = OUTPUT_DIR / date / "race_results.json"
        rc_path = OUTPUT_DIR / date / "race_conditions.json"
        ps_path = OUTPUT_DIR / date / "pickup_scores.json"
        if not rr_path.exists():
            continue
        rr = json.loads(rr_path.read_text(encoding="utf-8"))
        rc = json.loads(rc_path.read_text(encoding="utf-8")) if rc_path.exists() else {}
        ps = json.loads(ps_path.read_text(encoding="utf-8")) if ps_path.exists() else {"races": {}}
        ps_races = ps.get("races", {})

        # 3指数重複馬の (race_label, num) セットを構築
        triple_set = set()
        for rl, rdata in ps_races.items():
            if isinstance(rdata, dict) and "scored" in rdata:
                for h in rdata.get("scored", []):
                    triple_set.add((rl, str(h.get("馬番", ""))))

        for race_label, results_list in rr.items():
            cond = rc.get(race_label, {}) if isinstance(rc, dict) else {}
            race_dist = cond.get("distance")
            race_surface = cond.get("surface", "")
            class_name = cond.get("class_name", "")
            class_lvl = extract_class(class_name)
            age_class = extract_age_class(class_name)
            female_only = is_female_only(class_name)
            race_n = len(results_list)

            for h in results_list:
                hid = h.get("horse_id", "")
                prev = horse_db.get(hid, {}) if hid else {}

                def to_int(v, default=99):
                    try: return int(str(v).strip())
                    except: return default
                def to_float(v, default=None):
                    try: return float(str(v).strip())
                    except: return default

                rank = to_int(h.get("rank", 99))
                pop = to_int(h.get("pop", 99))
                odds = to_float(h.get("odds"), 0)
                hw_diff = to_int(h.get("horse_weight_diff", 0), 0)

                prev_pop = to_int(prev.get("prev_pop", ""), 99)
                prev_rank = to_int(prev.get("prev_rank", ""), 99)
                prev_idx = to_float(prev.get("prev_idx", ""))
                prev_dist_raw = prev.get("prev_dist", "")
                m = re.search(r"(\d{3,4})", prev_dist_raw)
                prev_dist = int(m.group(1)) if m else None

                prev_date_str = prev.get("prev_date", "")
                interval_days = None
                if prev_date_str and date:
                    try:
                        pd_dt = datetime.strptime(prev_date_str.replace("/", ""), "%Y%m%d")
                        td_dt = datetime.strptime(date, "%Y%m%d")
                        interval_days = (td_dt - pd_dt).days
                    except: pass

                num = str(h.get("num", ""))
                is_triple = (race_label, num) in triple_set

                horses.append({
                    "date": date,
                    "race_label": race_label,
                    "race_n": race_n,
                    "race_dist": race_dist,
                    "race_surface": race_surface,
                    "class_lvl": class_lvl,
                    "age_class": age_class,
                    "female_only": female_only,
                    "is_triple": is_triple,
                    "horse_id": hid,
                    "rank": rank,
                    "pop": pop,
                    "odds": odds,
                    "hw_diff": hw_diff,
                    "prev_pop": prev_pop,
                    "prev_rank": prev_rank,
                    "prev_idx": prev_idx,
                    "prev_dist": prev_dist,
                    "interval_days": interval_days,
                    "is_win": rank == 1,
                    "is_top3": rank is not None and 1 <= rank <= 3,
                })

    return horses


def calc_stats(subset, label=""):
    n = len(subset)
    if n == 0: return None
    wins = sum(1 for h in subset if h["is_win"])
    top3 = sum(1 for h in subset if h["is_top3"])
    roi_win = sum(h["odds"] * 100 for h in subset if h["is_win"]) / (n * 100) * 100
    return {"label": label, "n": n, "wins": wins, "top3": top3,
            "win_rate": wins/n*100, "top3_rate": top3/n*100, "roi_win": roi_win}


def print_stats(stats, min_n=10):
    if stats is None or stats["n"] < min_n: return
    s = stats
    print(f"  {s['label']:<60s}  N={s['n']:>4d}  "
          f"勝率{s['win_rate']:5.1f}%  3着内{s['top3_rate']:5.1f}%  "
          f"単勝回収率{s['roi_win']:6.1f}%")


def main():
    horses = load_all()
    total = len(horses)

    # 3指数重複馬除外プール
    anaba_pool = [h for h in horses if h["pop"] >= ANABA_POP_THRESHOLD and not h["is_triple"]]
    triple_anaba = [h for h in horses if h["pop"] >= ANABA_POP_THRESHOLD and h["is_triple"]]

    print(f"総馬数: {total}")
    print(f"3指数重複馬(全体): {sum(1 for h in horses if h['is_triple'])}頭")
    print(f"穴馬プール（5+人気・3指数重複馬除外）: {len(anaba_pool)}頭")
    print(f"参考: 5+人気の3指数重複馬: {len(triple_anaba)}頭")

    base = calc_stats(anaba_pool, "ベースライン(3指数重複馬除外)")
    print()
    print("=" * 100)
    print("【ベースライン】穴馬プール（3指数重複馬除外）")
    print("=" * 100)
    print_stats(base, min_n=1)

    # ──────────────────────────────────────
    # F1. 前走着順
    # ──────────────────────────────────────
    print()
    print("=" * 100)
    print("【F1】前走着順 × 5+人気（3指数重複馬除外）")
    print("=" * 100)
    for lo, hi, lbl in [(1,1,"前走1着"),(2,2,"前走2着"),(3,3,"前走3着"),
                         (1,3,"前走1-3着"),(4,6,"前走4-6着"),(7,99,"前走7着以下")]:
        sub = [h for h in anaba_pool if lo <= h["prev_rank"] <= hi]
        print_stats(calc_stats(sub, lbl))

    # ──────────────────────────────────────
    # F3. 前走指数
    # ──────────────────────────────────────
    print()
    print("=" * 100)
    print("【F2】前走指数 × 5+人気（3指数重複馬除外）")
    print("=" * 100)
    for lo, hi, lbl in [(90,999,"前走指数90+"),(80,89,"前走指数80-89"),
                         (70,79,"前走指数70-79"),(0,69,"前走指数0-69")]:
        sub = [h for h in anaba_pool if h["prev_idx"] is not None and lo <= h["prev_idx"] <= hi]
        print_stats(calc_stats(sub, lbl))

    # ──────────────────────────────────────
    # F3. 主要組合せ
    # ──────────────────────────────────────
    print()
    print("=" * 100)
    print("【F3】主要組合せ × 5+人気（3指数重複馬除外）")
    print("=" * 100)
    patterns = [
        ("前走1着 × 前走指数70+", lambda h: h["prev_rank"] == 1 and h["prev_idx"] and h["prev_idx"] >= 70),
        ("前走1着 × 前走指数80+", lambda h: h["prev_rank"] == 1 and h["prev_idx"] and h["prev_idx"] >= 80),
        ("前走1着 × 前走指数90+", lambda h: h["prev_rank"] == 1 and h["prev_idx"] and h["prev_idx"] >= 90),
        ("前走1-2着 × 前走指数70+", lambda h: 1 <= h["prev_rank"] <= 2 and h["prev_idx"] and h["prev_idx"] >= 70),
        ("前走1-2着 × 前走指数80+", lambda h: 1 <= h["prev_rank"] <= 2 and h["prev_idx"] and h["prev_idx"] >= 80),
        ("前走1-3着 × 前走指数80+", lambda h: 1 <= h["prev_rank"] <= 3 and h["prev_idx"] and h["prev_idx"] >= 80),
        ("前走1-3着 × 馬体減(-2kg以下)", lambda h: 1 <= h["prev_rank"] <= 3 and h["hw_diff"] <= -2),
        ("前走1-3着 × 馬体平行(±2kg)", lambda h: 1 <= h["prev_rank"] <= 3 and -2 < h["hw_diff"] < 3),
        ("前走1-3着 × 馬体増(+3kg以上)", lambda h: 1 <= h["prev_rank"] <= 3 and h["hw_diff"] >= 3),
    ]
    for lbl, fn in patterns:
        sub = [h for h in anaba_pool if fn(h)]
        print_stats(calc_stats(sub, lbl))

    # ──────────────────────────────────────
    # F4. 芝/ダート別
    # ──────────────────────────────────────
    print()
    print("=" * 100)
    print("【F4】芝/ダート別 × 5+人気（3指数重複馬除外）")
    print("=" * 100)
    for surf in ["芝", "ダ", "障"]:
        sub = [h for h in anaba_pool if surf in h["race_surface"]]
        print_stats(calc_stats(sub, f"{surf}全体"))

    print()
    print("  ── 芝・ダート別 × 前走1着 ──")
    for surf in ["芝", "ダ"]:
        sub = [h for h in anaba_pool if surf in h["race_surface"] and h["prev_rank"] == 1]
        print_stats(calc_stats(sub, f"{surf} × 前走1着"))

    print()
    print("  ── 芝・ダート別 × 前走1-3着 ──")
    for surf in ["芝", "ダ"]:
        sub = [h for h in anaba_pool if surf in h["race_surface"] and 1 <= h["prev_rank"] <= 3]
        print_stats(calc_stats(sub, f"{surf} × 前走1-3着"))

    print()
    print("  ── 芝・ダート別 × 前走指数80+ ──")
    for surf in ["芝", "ダ"]:
        sub = [h for h in anaba_pool if surf in h["race_surface"] and h["prev_idx"] and h["prev_idx"] >= 80]
        print_stats(calc_stats(sub, f"{surf} × 前走指数80+"))

    # ──────────────────────────────────────
    # F5. クラス別
    # ──────────────────────────────────────
    print()
    print("=" * 100)
    print("【F5】クラス別 × 5+人気（3指数重複馬除外）")
    print("=" * 100)
    for cls in ["新馬", "未勝利", "1勝", "2勝", "3勝", "OP+", "その他"]:
        sub = [h for h in anaba_pool if h["class_lvl"] == cls]
        print_stats(calc_stats(sub, f"クラス={cls}"))

    print()
    print("  ── クラス別 × 前走1着 ──")
    for cls in ["未勝利", "1勝", "2勝", "3勝", "OP+"]:
        sub = [h for h in anaba_pool if h["class_lvl"] == cls and h["prev_rank"] == 1]
        print_stats(calc_stats(sub, f"{cls} × 前走1着"), min_n=5)

    print()
    print("  ── クラス別 × 前走1-3着 ──")
    for cls in ["未勝利", "1勝", "2勝", "3勝", "OP+"]:
        sub = [h for h in anaba_pool if h["class_lvl"] == cls and 1 <= h["prev_rank"] <= 3]
        print_stats(calc_stats(sub, f"{cls} × 前走1-3着"), min_n=5)

    # ──────────────────────────────────────
    # F6. 年齢区分（3歳限定 vs 3歳以上）
    # ──────────────────────────────────────
    print()
    print("=" * 100)
    print("【F6】年齢区分別 × 5+人気（3指数重複馬除外）")
    print("=" * 100)
    for ac in ["2歳", "3歳限定", "3歳以上"]:
        sub = [h for h in anaba_pool if h["age_class"] == ac]
        print_stats(calc_stats(sub, f"{ac}全体"))

    print()
    print("  ── 年齢区分 × 前走1着 ──")
    for ac in ["3歳限定", "3歳以上"]:
        sub = [h for h in anaba_pool if h["age_class"] == ac and h["prev_rank"] == 1]
        print_stats(calc_stats(sub, f"{ac} × 前走1着"), min_n=5)

    print()
    print("  ── 年齢区分 × 前走1-3着 ──")
    for ac in ["3歳限定", "3歳以上"]:
        sub = [h for h in anaba_pool if h["age_class"] == ac and 1 <= h["prev_rank"] <= 3]
        print_stats(calc_stats(sub, f"{ac} × 前走1-3着"), min_n=5)

    # ──────────────────────────────────────
    # F7. 牝馬限定戦
    # ──────────────────────────────────────
    print()
    print("=" * 100)
    print("【F7】牝馬限定戦 vs 混合 × 5+人気（3指数重複馬除外）")
    print("=" * 100)
    sub_f = [h for h in anaba_pool if h["female_only"]]
    sub_m = [h for h in anaba_pool if not h["female_only"]]
    print_stats(calc_stats(sub_f, "牝馬限定戦"))
    print_stats(calc_stats(sub_m, "混合戦"))

    print()
    print("  ── 牝馬限定 × 前走1-3着 ──")
    sub = [h for h in anaba_pool if h["female_only"] and 1 <= h["prev_rank"] <= 3]
    print_stats(calc_stats(sub, "牝馬限定 × 前走1-3着"), min_n=5)
    sub = [h for h in anaba_pool if not h["female_only"] and 1 <= h["prev_rank"] <= 3]
    print_stats(calc_stats(sub, "混合 × 前走1-3着"), min_n=5)

    # ──────────────────────────────────────
    # F8. 距離変更
    # ──────────────────────────────────────
    print()
    print("=" * 100)
    print("【F8】距離変更 × 5+人気（3指数重複馬除外）")
    print("=" * 100)
    def dc(h):
        if h["prev_dist"] is None or h["race_dist"] is None: return None
        return h["race_dist"] - h["prev_dist"]
    for label, fn in [
        ("距離短縮(-200m以上)", lambda h: dc(h) is not None and dc(h) <= -200),
        ("同距離(±50m)", lambda h: dc(h) is not None and abs(dc(h)) < 50),
        ("距離延長(+200m以上)", lambda h: dc(h) is not None and dc(h) >= 200),
    ]:
        sub = [h for h in anaba_pool if fn(h)]
        print_stats(calc_stats(sub, label))

    # ──────────────────────────────────────
    # F9. 馬体重増減
    # ──────────────────────────────────────
    print()
    print("=" * 100)
    print("【F9】馬体重増減 × 5+人気（3指数重複馬除外）")
    print("=" * 100)
    for lo, hi, lbl in [(-99,-15,"-15kg以上減"),(-14,-9,"-14〜-9kg"),
                         (-8,-3,"-8〜-3kg"),(-2,2,"±2kg(平行)"),
                         (3,8,"+3〜+8kg"),(9,14,"+9〜+14kg"),
                         (15,99,"+15kg以上増")]:
        sub = [h for h in anaba_pool if lo <= h["hw_diff"] <= hi]
        print_stats(calc_stats(sub, lbl))

    # ──────────────────────────────────────
    # F10. 高ROIパターン探索
    # ──────────────────────────────────────
    print()
    print("=" * 100)
    print("【F10】高ROIパターン探索（5+人気・3指数重複馬除外・N>=15・ROI≥150%）")
    print("=" * 100)
    found = []
    factors = {
        "prev_rank_1": lambda h: h["prev_rank"] == 1,
        "prev_rank_top3": lambda h: 1 <= h["prev_rank"] <= 3,
        "prev_idx_70+": lambda h: h["prev_idx"] and h["prev_idx"] >= 70,
        "prev_idx_80+": lambda h: h["prev_idx"] and h["prev_idx"] >= 80,
        "prev_idx_90+": lambda h: h["prev_idx"] and h["prev_idx"] >= 90,
        "hw_minus5": lambda h: h["hw_diff"] <= -5,
        "hw_plus5": lambda h: h["hw_diff"] >= 5,
        "hw_flat": lambda h: -2 <= h["hw_diff"] <= 2,
        "interval_56_180": lambda h: h["interval_days"] is not None and 56 <= h["interval_days"] <= 180,
        "shiba": lambda h: "芝" in h["race_surface"],
        "dirt": lambda h: "ダ" in h["race_surface"],
        "age_3only": lambda h: h["age_class"] == "3歳限定",
        "age_3plus": lambda h: h["age_class"] == "3歳以上",
        "female_only": lambda h: h["female_only"],
        "class_misho": lambda h: h["class_lvl"] == "未勝利",
        "class_1sho": lambda h: h["class_lvl"] == "1勝",
        "class_2sho": lambda h: h["class_lvl"] == "2勝",
        "class_3sho_op": lambda h: h["class_lvl"] in ("3勝", "OP+"),
        "dist_extend_200+": lambda h: dc(h) is not None and dc(h) >= 200,
        "dist_short_200+": lambda h: dc(h) is not None and dc(h) <= -200,
    }
    keys = list(factors.keys())
    for k, fn in factors.items():
        sub = [h for h in anaba_pool if fn(h)]
        s = calc_stats(sub, k)
        if s and s["n"] >= 15 and s["roi_win"] >= 150:
            found.append(s)
    for i in range(len(keys)):
        for j in range(i+1, len(keys)):
            ki, kj = keys[i], keys[j]
            sub = [h for h in anaba_pool if factors[ki](h) and factors[kj](h)]
            s = calc_stats(sub, f"{ki} AND {kj}")
            if s and s["n"] >= 15 and s["roi_win"] >= 150:
                found.append(s)
    found.sort(key=lambda x: x["roi_win"], reverse=True)
    for s in found[:40]:
        print(f"  {s['label']:<60s}  N={s['n']:>4d}  "
              f"勝率{s['win_rate']:5.1f}%  3着内{s['top3_rate']:5.1f}%  "
              f"単勝回収率{s['roi_win']:6.1f}%")
    print(f"\n  ※ 該当パターン: {len(found)}件")


if __name__ == "__main__":
    main()
