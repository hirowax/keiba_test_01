#!/usr/bin/env python3
"""
期待値🔥閾値キャリブレーション
過去のpickup_scores.json + race_results.jsonを照合し、
最適なEV閾値を計算してoutput/threshold_config.jsonに保存する。
"""

import json
import re
import logging
from datetime import date
from collections import Counter
from pathlib import Path

from data_quality import load_invalidated, race_quality_issues, results_save_issue
from race_pickup import FIRE_RULE_SCORING_VERSIONS, SCORING_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

# 閾値候補（降順に評価）
THRESHOLDS = [9, 8, 7, 6, 5, 4, 3]
# 3着内率の目標（これ以上であれば有効とみなす）
TARGET_RATE = 0.70
# 最低サンプル数
MIN_COUNT = 5
# これより前は前走系ファクター汚染のため除外（docs/factor_audit_202606.md）
CLEAN_START = "20260328"
# 品質の確認できたレースだけを使う（data_quality.race_quality_issues）: 馬番の取り方が修正後、
# 指数の読み方が記録されている（2026-10-30 夜の修正後）、無効レースでない。
# 修正前は枠番を馬番として読んでおり前走成績による加点が別馬の値（docs/incident_umaban_20261030.md）。
# 修正後の照合馬数がこれ未満なら threshold_config.json を更新せず現行値（9pt・暫定）を維持する。
MIN_TRUSTED_HORSES = 200


def load_pairs():
    """pickup_scores.json と race_results.json が両方ある日付のデータを返す"""
    pairs = []
    for date_dir in sorted(OUTPUT_DIR.iterdir()):
        if not date_dir.is_dir():
            continue
        if date_dir.name < CLEAN_START:
            continue
        pickup_path = date_dir / "pickup_scores.json"
        results_path = date_dir / "race_results.json"
        if not pickup_path.exists() or not results_path.exists():
            continue
        issue = results_save_issue(date_dir)
        if issue:   # ファイルが新旧で混ざっている可能性がある（2026-11-13〜）
            print(f"  {date_dir.name}: {issue} → 使わない")
            continue
        with open(pickup_path, encoding="utf-8") as f:
            pickup = json.load(f)
        with open(results_path, encoding="utf-8") as f:
            results = json.load(f)
        pairs.append((date_dir.name, pickup, results))
    return pairs


SPACES = re.compile(r"[\s\u3000]+")
# 中止・失格は出走しているので3着以内ではないとして数える（着順の代わりの値）
NOT_TOP3 = 99


def _load(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def match_result(rows, others: dict, num: str, name: str):
    """結果と照合して着順（int）か、集計しない理由（str）を返す。
    馬番と馬名の両方が一致したときだけ同じ馬とする（馬名で許すのは空白の違いだけ）。2026-11-02 まで
    「馬番か馬名のどちらかが一致」で照合していた"""
    if rows is None:
        return "結果が無い"
    same = lambda a, b: SPACES.sub("", str(a)) == SPACES.sub("", str(b))
    for r in rows:
        if str(r.get("num", "")) == num:
            return int(r["rank"]) if same(r.get("name", ""), name) else "馬番は合うが馬名が違う"
    o = others.get(num)
    if o is None:
        return "馬番が結果に無い（取消・除外・中止・失格の区別がつかない日を含む）"
    if not same(o.get("name", ""), name):
        return "馬番は合うが馬名が違う"
    return NOT_TOP3 if o["status"] in ("中止", "失格") else f"{o['status']}"


def cross_reference(pairs):
    """全日付のpickup馬に着順を紐付けたリストを返す"""
    all_horses = []
    dates_used = []
    excluded = Counter()  # 使わなかった理由 -> レース数
    unmatched = Counter()  # 集計しなかった馬の理由 -> 頭数
    for date_str, pickup, results in pairs:
        dates_used.append(date_str)
        nonfinishers = _load(OUTPUT_DIR / date_str / "race_nonfinishers.json") or {}
        invalid = load_invalidated(date_str, OUTPUT_DIR)
        for race, rdata in pickup.get("races", {}).items():
            issues = race_quality_issues(rdata, invalid.get(race))
            if issues:
                excluded[issues[0].split(":")[0]] += 1
                continue
            others = {str(o["num"]): o for o in nonfinishers.get(race, [])}
            for h in rdata.get("scored", []):
                num = str(h.get("馬番", ""))
                name = (rdata.get("horse_name_full") or {}).get(num) or h.get("馬名", "")
                status = match_result(results.get(race), others, num, name)
                unmatched[status] += 0 if isinstance(status, int) else 1
                if not isinstance(status, int):
                    continue  # 照合できない・取消・除外は集計しない（件数はログに出す）
                all_horses.append({
                    "date": date_str,
                    "race": race,
                    "name": name,
                    "score": h["score"],
                    "finish": status,
                })
    if excluded:
        log.info("使わなかったレース: " + "、".join(f"{k} {v}" for k, v in excluded.most_common()))
    unmatched = {k: v for k, v in unmatched.items() if v}
    if unmatched:
        log.info("集計しなかった馬: " + "、".join(f"{k} {v}" for k, v in unmatched.items()))
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
    if SCORING_VERSION not in FIRE_RULE_SCORING_VERSIONS:
        # v5.2〜 の🔥の基準は、タスク10の手順（docs/fire_threshold_plan_202611.md）で決める。この選び方では計算しない
        log.warning(f"点数の版 {SCORING_VERSION} の🔥の基準はまだ決めていないため、閾値は計算・更新しない"
                    f"（docs/data_top_reader_fix_plan_202611.md 3.5）")
        return
    pairs = load_pairs()
    if not pairs:
        log.warning("照合できる過去データがありません")
        return

    log.info(f"{len(pairs)}日分のデータを照合: {[p[0] for p in pairs]}")
    horses, dates_used = cross_reference(pairs)
    log.info(f"照合馬数（品質の確認できたレースのみ）: {len(horses)}頭")
    if len(horses) < MIN_TRUSTED_HORSES:
        log.warning(f"品質の確認できたデータが {MIN_TRUSTED_HORSES}頭未満のため閾値は更新しない"
                    f"（threshold_config.json の現行値を暫定維持。有効性は未確認）")
        return

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
        "collector_version_only": True,  # index.html はこれが無い実績を表示しない
    }

    out_path = OUTPUT_DIR / "threshold_config.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    log.info(f"保存: {out_path} (ev_threshold={optimal})")


if __name__ == "__main__":
    main()
