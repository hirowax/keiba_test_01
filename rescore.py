#!/usr/bin/env python3
"""
既存の pickup_scores.json を新スコアリング(H1/H2)で再計算する
horse_id_map は race_results.json から補完
usage: python3 rescore.py [YYYYMMDD]
"""

import sys
import json
from datetime import datetime
from pathlib import Path

from race_pickup import score_horses

BASE_DIR = Path(__file__).parent


def main():
    date = sys.argv[1] if len(sys.argv) >= 2 else datetime.now().strftime("%Y%m%d")

    pickup_path  = BASE_DIR / "output" / date / "pickup_scores.json"
    results_path = BASE_DIR / "output" / date / "race_results.json"
    horse_db_path = BASE_DIR / "output" / "horse_db.json"

    if not pickup_path.exists():
        print(f"pickup_scores.json が見つかりません: {pickup_path}"); return
    if not horse_db_path.exists():
        print(f"horse_db.json が見つかりません: {horse_db_path}"); return

    with open(pickup_path, encoding="utf-8") as f:
        pickup_data = json.load(f)
    with open(horse_db_path, encoding="utf-8") as f:
        horse_db = json.load(f)

    horse_style_path = BASE_DIR / "output" / "horse_style.json"
    horse_style_db = {}
    if horse_style_path.exists():
        with open(horse_style_path, encoding="utf-8") as f:
            horse_style_db = json.load(f)

    # race_conditions.json から距離を取得
    conditions_path = BASE_DIR / "output" / date / "race_conditions.json"
    race_conditions = {}
    if conditions_path.exists():
        with open(conditions_path, encoding="utf-8") as f:
            race_conditions = json.load(f)

    # race_results.json から 馬番→horse_id マップを構築（任意）
    horse_id_by_race = {}
    if results_path.exists():
        with open(results_path, encoding="utf-8") as f:
            race_results = json.load(f)
        for race_label, horses in race_results.items():
            horse_id_by_race[race_label] = {
                str(h["num"]): h.get("horse_id", "") for h in horses
            }

    updated = 0
    for race_label, rdata in pickup_data["races"].items():
        if "error" in rdata:
            continue

        # horse_id_map: pickup_scores.json に保存済みなら優先使用、なければ race_results から補完
        horse_id_map = rdata.get("horse_id_map") or horse_id_by_race.get(race_label, {})

        # shutuba_data / data_top_data を既存データから復元
        shutuba_data = {
            "position_nums": set(rdata.get("position_nums", [])),
            "top3_hits":     rdata.get("top3_hits", {}),
            "horse_id_map":  horse_id_map,
            "pop_map":       rdata.get("pop_map", {}),
        }
        data_top_data = {
            "pickup_nums":   set(rdata.get("pickup_nums", [])),
            "analysis_hits": rdata.get("analysis_hits", {}),
        }

        triple_horses = rdata.get("scored", [])
        if not triple_horses:
            continue

        # レース内の前走指数最大値を計算（出走全馬対象）
        race_max_prev_idx = None
        prev_idxs = []
        for hid in horse_id_map.values():
            if hid in horse_db:
                try:
                    prev_idxs.append(float(horse_db[hid].get("prev_idx", "")))
                except (ValueError, TypeError):
                    pass
        if prev_idxs:
            race_max_prev_idx = max(prev_idxs)

        race_dist = race_conditions.get(race_label, {}).get("distance")

        # 再スコアリング
        new_scored = score_horses(triple_horses, shutuba_data, data_top_data,
                                  prev_db=horse_db, race_max_prev_idx=race_max_prev_idx,
                                  race_date=date, horse_style_db=horse_style_db,
                                  race_dist=race_dist)
        rdata["scored"] = new_scored

        max_score = max((h["score"] for h in new_scored), default=0)
        top_horse = next((h["馬名"] for h in new_scored if h["score"] == max_score), "")
        print(f"{race_label}: 最高{max_score}pt ({top_horse})")
        updated += 1

    with open(pickup_path, "w", encoding="utf-8") as f:
        json.dump(pickup_data, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n再スコアリング完了: {updated}レース → {pickup_path}")


if __name__ == "__main__":
    main()
