#!/usr/bin/env python3
"""
期待値🔥閾値キャリブレーション
過去のpickup_scores.json + race_results.jsonを照合し、
最適なEV閾値を計算してoutput/threshold_config.jsonに保存する。
"""

import json
import logging
from datetime import date
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

# 閾値候補（降順に評価）
THRESHOLDS = [9, 8, 7, 6, 5, 4, 3]
# 3着内率の目標（これ以上であれば有効とみなす）
TARGET_RATE = 0.55
# 最低サンプル数
MIN_COUNT = 5


def load_pairs():
    """pickup_scores.json と race_results.json が両方ある日付のデータを返す"""
    pairs = []
    for date_dir in sorted(OUTPUT_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        pickup_path = date_dir / "pickup_scores.json"
        results_path = date_dir / "race_results.json"
        if not pickup_path.exists() or not results_path.exists():
            continue
        with open(pickup_path, encoding="utf-8") as f:
            pickup = json.load(f)
        with open(results_path, encoding="utf-8") as f:
            results = json.load(f)
        pairs.append((date_dir.name, pickup, results))
    return pairs


def cross_reference(pairs):
    """全日付のpickup馬に着順を紐付けたリストを返す"""
    all_horses = []
    dates_used = []
    for date_str, pickup, results in pairs:
        dates_used.append(date_str)
        for race, rdata in pickup.get("races", {}).items():
            for h in rdata.get("scored", []):
                finish = None
                num = str(h.get("馬番", ""))
                name = h.get("馬名", "")
                if race in results:
                    for r in results[race]:
                        if str(r.get("num", "")) == num or r.get("name", "") == name:
                            finish = r.get("rank")
                            break
                if finish is None:
                    continue  # 結果不明はスキップ
                all_horses.append({
                    "date": date_str,
                    "race": race,
                    "name": name,
                    "score": h["score"],
                    "finish": finish,
                })
    return all_horses, dates_used


def calc_stats(horses):
    """閾値ごとの統計を計算する"""
    stats = {}
    for t in THRESHOLDS:
        subset = [h for h in horses if h["score"] >= t]
        hit3 = [h for h in subset if h["finish"] <= 3]
        count = len(subset)
        stats[str(t)] = {
            "count": count,
            "hit3": len(hit3),
            "rate": round(len(hit3) / count, 3) if count > 0 else 0.0,
        }
    return stats


def pick_threshold(stats):
    """
    最適閾値を選ぶ:
    高い閾値から順に「count >= MIN_COUNT かつ rate >= TARGET_RATE」を満たす最低閾値を採用。
    条件を満たすものがなければサンプル数が十分な中で最高率のものを採用。
    """
    candidates = []
    for t in sorted(THRESHOLDS):
        s = stats[str(t)]
        if s["count"] >= MIN_COUNT:
            candidates.append((t, s["rate"], s["count"]))

    if not candidates:
        log.warning("サンプル不足。デフォルト5ptを使用")
        return 5

    # TARGET_RATE以上で最も低いptを採用（馬数を多くしたい）
    valid = [(t, r, c) for t, r, c in candidates if r >= TARGET_RATE]
    if valid:
        chosen = min(valid, key=lambda x: x[0])
        log.info(f"閾値 {chosen[0]}pt 採用: 3着内率{chosen[1]*100:.0f}% ({chosen[2]}頭)")
        return chosen[0]

    # TARGET_RATEを満たすものがなければ最高率のptを採用
    best = max(candidates, key=lambda x: x[1])
    log.info(f"TARGET未達。最高率 {best[0]}pt 採用: 3着内率{best[1]*100:.0f}% ({best[2]}頭)")
    return best[0]


def main():
    pairs = load_pairs()
    if not pairs:
        log.warning("照合できる過去データがありません")
        return

    log.info(f"{len(pairs)}日分のデータを照合: {[p[0] for p in pairs]}")
    horses, dates_used = cross_reference(pairs)
    log.info(f"照合馬数: {len(horses)}頭")

    stats = calc_stats(horses)
    optimal = pick_threshold(stats)

    for t in sorted(THRESHOLDS):
        s = stats[str(t)]
        log.info(f"  {t}pt以上: {s['count']}頭 → 3着内{s['hit3']}頭 ({s['rate']*100:.0f}%)")

    config = {
        "ev_threshold": optimal,
        "calibrated_at": str(date.today()),
        "dates_used": dates_used,
        "stats": stats,
    }

    out_path = OUTPUT_DIR / "threshold_config.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    log.info(f"保存: {out_path} (ev_threshold={optimal})")


if __name__ == "__main__":
    main()
