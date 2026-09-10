#!/usr/bin/env python3
"""
直近日の穴馬候補を抽出して結果と照合
usage: python3 check_anaba_recent.py 20260502 20260503
"""
import json
import re
import sys
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"


def extract_class(class_name: str) -> str:
    if not class_name: return "不明"
    if "新馬" in class_name: return "新馬"
    if "未勝利" in class_name: return "未勝利"
    if "オープン" in class_name or "ＯＰ" in class_name: return "OP+"
    if "３勝" in class_name or "3勝" in class_name: return "3勝"
    if "２勝" in class_name or "2勝" in class_name: return "2勝"
    if "１勝" in class_name or "1勝" in class_name: return "1勝"
    return "その他"


def extract_age_class(class_name: str) -> str:
    if "２歳" in class_name or "2歳" in class_name: return "2歳"
    if "３歳以上" in class_name or "3歳以上" in class_name or "４歳以上" in class_name: return "3歳以上"
    if "３歳" in class_name or "3歳" in class_name: return "3歳限定"
    return "不明"


def is_female_only(class_name: str) -> bool:
    return "牝" in class_name


def calc_anaba_score(h, race_dist, prev_dist, class_name):
    """v2 穴馬スコア計算（5+人気のみ対象）"""
    score = 0
    factors = []
    cls = extract_class(class_name)
    age = extract_age_class(class_name)

    # 加点
    if h["prev_rank"] == 1:
        score += 3
        factors.append("前走1着+3")
    elif h["prev_rank"] in (2, 3):
        score += 1
        factors.append(f"前走{h['prev_rank']}着+1")

    if h["prev_idx"] is not None and h["prev_idx"] >= 80 and 1 <= h["prev_rank"] <= 3:
        score += 1
        factors.append(f"前走指数{h['prev_idx']:.0f}+1-3着+1")

    if -2 <= h["hw_diff"] <= 2:
        score += 1
        factors.append(f"馬体平行({h['hw_diff']:+d}kg)+1")

    # 減点
    if h["hw_diff"] <= -10:
        score -= 2
        factors.append(f"馬体減{h['hw_diff']}kg-2")
    if race_dist and prev_dist and (race_dist - prev_dist) >= 200:
        score -= 1
        factors.append(f"距離延長(+{race_dist-prev_dist}m)-1")
    if is_female_only(class_name):
        score -= 1
        factors.append("牝馬限定-1")

    # 強調バッジ判定
    badges = []
    if cls == "未勝利" and h["prev_rank"] == 1:
        badges.append("🌟未勝利×前走1着")
    if age == "3歳限定" and h["prev_rank"] == 1:
        badges.append("⭐3歳限定×前走1着")

    return score, factors, badges, cls, age


def main():
    if len(sys.argv) < 2:
        # default to last 2 dates with race_results
        dates = []
        for d in sorted(OUTPUT_DIR.iterdir(), reverse=True):
            if d.is_dir() and re.match(r"^\d{8}$", d.name):
                if (d / "race_results.json").exists():
                    dates.append(d.name)
                    if len(dates) >= 2: break
        dates = sorted(dates)
    else:
        dates = sys.argv[1:]

    horse_db = json.loads((OUTPUT_DIR / "horse_db.json").read_text(encoding="utf-8"))

    for date in dates:
        rr_path = OUTPUT_DIR / date / "race_results.json"
        rc_path = OUTPUT_DIR / date / "race_conditions.json"
        if not rr_path.exists():
            print(f"[{date}] race_results.json なし → スキップ")
            continue
        rr = json.loads(rr_path.read_text(encoding="utf-8"))
        rc = json.loads(rc_path.read_text(encoding="utf-8")) if rc_path.exists() else {}

        # collect anaba candidates
        candidates = []
        for race_label, results in rr.items():
            cond = rc.get(race_label, {}) if isinstance(rc, dict) else {}
            race_dist = cond.get("distance")
            class_name = cond.get("class_name", "")

            for h in results:
                hid = h.get("horse_id", "")
                if not hid: continue
                try: pop = int(h.get("pop", 99))
                except: pop = 99
                if pop < 5: continue

                prev = horse_db.get(hid, {})
                try: prev_rank = int(prev.get("prev_rank", "") or 99)
                except: prev_rank = 99
                try: prev_idx = float(prev.get("prev_idx", "") or 0) if prev.get("prev_idx") else None
                except: prev_idx = None
                try: hw_diff = int(h.get("horse_weight_diff", 0))
                except: hw_diff = 0
                prev_dist_raw = prev.get("prev_dist", "")
                m = re.search(r"(\d{3,4})", prev_dist_raw)
                prev_dist = int(m.group(1)) if m else None

                hd = {
                    "name": h.get("name"),
                    "num": h.get("num"),
                    "pop": pop,
                    "rank": h.get("rank"),
                    "odds": h.get("odds"),
                    "prev_rank": prev_rank,
                    "prev_idx": prev_idx,
                    "hw_diff": hw_diff,
                }
                score, factors, badges, cls, age = calc_anaba_score(hd, race_dist, prev_dist, class_name)
                if score >= 3:
                    candidates.append({
                        "race": race_label,
                        "name": h.get("name"),
                        "num": h.get("num"),
                        "pop": pop,
                        "rank": h.get("rank"),
                        "odds": h.get("odds"),
                        "score": score,
                        "factors": factors,
                        "badges": badges,
                        "class": cls,
                        "age": age,
                    })

        # show summary
        print(f"\n{'='*100}")
        print(f"  {date}  穴馬候補（anaba_score≥3）  全{len(candidates)}頭")
        print(f"{'='*100}")
        # group by score
        candidates.sort(key=lambda x: (-x["score"], x["race"]))

        hits = [c for c in candidates if c["rank"] is not None and c["rank"] <= 3]
        wins = [c for c in candidates if c["rank"] == 1]
        print(f"  → 3着内的中: {len(hits)}/{len(candidates)} ({len(hits)/len(candidates)*100 if candidates else 0:.1f}%)")
        print(f"  → 勝利: {len(wins)}/{len(candidates)} ({len(wins)/len(candidates)*100 if candidates else 0:.1f}%)")
        if wins:
            roi = sum(c["odds"] for c in wins) * 100 / (len(candidates) * 100) * 100
            print(f"  → 単勝回収率: {roi:.1f}%")

        print()
        print(f"  {'レース':<10s} {'馬番':<3s} {'馬名':<14s} {'人気':<3s} {'着':<3s} {'odds':<6s} {'score':<5s} {'cls':<8s} {'age':<8s}  factors / badges")
        print(f"  {'-'*120}")
        for c in candidates:
            rank = c["rank"] if c["rank"] is not None else "?"
            mark = "★" if isinstance(rank, int) and rank <= 3 else "  "
            print(f"  {mark}{c['race']:<8s} {str(c['num']):<3s} {c['name']:<14s} {c['pop']:<3d} {str(rank):<3s} "
                  f"{c['odds']:<6} {c['score']:<5d} {c['class']:<8s} {c['age']:<8s}  "
                  f"{', '.join(c['factors'])}  {' '.join(c['badges'])}")


if __name__ == "__main__":
    main()
