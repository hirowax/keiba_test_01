#!/usr/bin/env python3
"""
既存の pickup_scores.json を新スコアリングで再計算する
horse_id_map は race_results.json から補完
usage: python3 rescore.py [YYYYMMDD] [--allow-global-db]

前走データは per-date の output/{date}/prev_data.json を使う（採点当日のスナップショット）。
prev_data.json がない日付はグローバル horse_db.json だと「未来の前走」で汚染されるため停止する。
どうしても強行する場合のみ --allow-global-db を付ける（クリーン境界 20260328 以降の直近日付のみ推奨。
その場合も prev_date >= 対象日 のエントリは除外される）。
"""

import sys
import json
from datetime import datetime
from pathlib import Path

from race_pickup import score_horses, SCORING_VERSION

BASE_DIR = Path(__file__).parent


def _prev_is_valid(entry: dict, date: str) -> bool:
    """prev_date が対象日より前か（未来の前走=汚染データを弾く）"""
    pd_str = (entry.get("prev_date") or "").replace("/", "")
    return len(pd_str) == 8 and pd_str.isdigit() and pd_str < date


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    allow_global_db = "--allow-global-db" in sys.argv
    date = args[0] if args else datetime.now().strftime("%Y%m%d")

    pickup_path  = BASE_DIR / "output" / date / "pickup_scores.json"
    results_path = BASE_DIR / "output" / date / "race_results.json"
    prev_data_path = BASE_DIR / "output" / date / "prev_data.json"
    horse_db_path = BASE_DIR / "output" / "horse_db.json"

    if not pickup_path.exists():
        print(f"pickup_scores.json が見つかりません: {pickup_path}"); return

    with open(pickup_path, encoding="utf-8") as f:
        pickup_data = json.load(f)

    # 前走データソースの選択（per-date スナップショット優先）
    if prev_data_path.exists():
        with open(prev_data_path, encoding="utf-8") as f:
            horse_db = json.load(f)
        n_snap = len(horse_db)
        # スナップショットの欠損馬（スクレイプ失敗等で空）はグローバルDBから
        # prev_date < 対象日 のエントリのみ補完する
        n_fill = 0
        if horse_db_path.exists():
            with open(horse_db_path, encoding="utf-8") as f:
                global_db = json.load(f)
            for hid, e in global_db.items():
                if not horse_db.get(hid, {}).get("prev_date") and _prev_is_valid(e, date):
                    horse_db[hid] = e
                    n_fill += 1
        print(f"prev_data.json 使用: {n_snap}頭（採点当日スナップショット）+ グローバルDB補完{n_fill}頭")
    elif allow_global_db:
        if not horse_db_path.exists():
            print(f"horse_db.json が見つかりません: {horse_db_path}"); return
        with open(horse_db_path, encoding="utf-8") as f:
            horse_db = json.load(f)
        before = len(horse_db)
        horse_db = {hid: e for hid, e in horse_db.items() if _prev_is_valid(e, date)}
        print(f"⚠️  グローバル horse_db 使用（--allow-global-db）: "
              f"{before}頭中 prev_date>={date} の{before - len(horse_db)}頭を除外 → {len(horse_db)}頭")
    else:
        print(f"❌ {date} に prev_data.json がありません。")
        print(f"   グローバル horse_db での再スコアは前走データ汚染を起こすため中止します。")
        print(f"   （強行する場合: --allow-global-db。クリーン境界 20260328 以降の直近日付のみ推奨）")
        return

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
            "odds_map":      rdata.get("odds_map", {}),
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
                                  race_dist=race_dist,
                                  predicted_pace=rdata.get("predicted_pace"))
        rdata["scored"] = new_scored

        max_score = max((h["score"] for h in new_scored), default=0)
        top_horse = next((h["馬名"] for h in new_scored if h["score"] == max_score), "")
        print(f"{race_label}: 最高{max_score}pt ({top_horse})")
        updated += 1

    pickup_data["scoring_version"] = SCORING_VERSION
    pickup_data["rescored_at"] = datetime.now().isoformat()
    with open(pickup_path, "w", encoding="utf-8") as f:
        json.dump(pickup_data, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n再スコアリング完了: {updated}レース ({SCORING_VERSION}) → {pickup_path}")


if __name__ == "__main__":
    main()
