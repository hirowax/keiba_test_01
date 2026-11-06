#!/usr/bin/env python3
"""
「新しく候補」を🔥・推奨・買い目に含める場合と外す場合の2つを、固定した設定で作って保存する（評価用）。
基準: docs/new_candidates_criteria_202611.md（4.1。2026-11-06 ユーザー承認: 比べるもの・このスクリプトの実装・結論を🔥に限る）

- 新しく候補: 今の定義（scraper.find_triple_top5_rows）では候補で、旧方式（コミット 6f2ed8d の同じ関数）では候補でない馬。
  旧方式は research/index_top3_compare_202611/old_scraper_6f2ed8d.py（git show 6f2ed8d:scraper.py）を呼ぶ（書き写さない）。
- 含める場合: 採点結果（pickup_scores.json の scored）をそのまま使う。
- 外す場合: scored から新しく候補を除く。各馬の点数は、ほかの候補の有無に左右されない（前走指数レース内1位の比較相手は
  出走馬全員）ので、残りの馬の点数は変えない。買い目の紐・◯もこの一覧で選ぶ（サマリーの3指数の候補は紐の C・D から
  除かれるので、外した馬は買い目に入らない）。
- 2つとも同じ固定した設定（FIXED）で作る。本番の閾値が変わっても、この評価では変えない。
- 品質の判定は data_quality.race_quality_issues を、このスクリプトの実行時に呼んで記録する（保存した quality_issues を見ない）。
- 成績（着順・払戻）は読まない。本番のファイルは書き換えない。

usage: python3 new_candidates_variants.py YYYYMMDD
出力: output/YYYYMMDD/new_candidates_variants.json
"""
import hashlib
import importlib.util
import json
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import scraper
from data_quality import load_invalidated, race_quality_issues
from index_table_check import load_index_tables
from paper_trade import build_menus, build_summary_index, menu_rule_of
from validate_trifecta_202608 import MIN_SCORE, BET_UNIT

logging.disable(logging.CRITICAL)
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
SUMMARY_DIR = BASE_DIR / "summary"
OLD_MODULE = BASE_DIR / "research" / "index_top3_compare_202611" / "old_scraper_6f2ed8d.py"
VARIANTS_VERSION = "含める場合と外す場合_固定設定_20261106"

# 評価の期間中に変えない設定（基準 4.1）。閾値は評価の期間の始め（2026-11-07 の採点）の threshold_config.json の値
FIXED = {
    "ev_threshold": 9,
    "next_range": [7, 8],          # 次点: 閾値−2〜閾値−1
    "min_score_axis": MIN_SCORE,   # 買い目の軸の最低点
    "bet_unit": BET_UNIT,
    "scoring_version": "v5.1",
    "candidate_rule_version": scraper.CANDIDATE_RULE_VERSION,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_old():
    spec = importlib.util.spec_from_file_location("old_scraper_6f2ed8d", OLD_MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def variant(scored: list, summary_race) -> dict:
    """1つの場合の🔥・次点・買い目"""
    th = FIXED["ev_threshold"]
    lo, hi = FIXED["next_range"]
    out = {"scored": [{"馬番": str(h["馬番"]), "馬名": h.get("馬名", ""), "score": h.get("score", 0)} for h in scored],
           "fire": [str(h["馬番"]) for h in scored if h.get("score", 0) >= th],
           "next": [str(h["馬番"]) for h in scored if lo <= h.get("score", 0) <= hi]}
    if not scored:
        out["bets"] = None
        out["bets_skip_reason"] = "候補が0頭"
    elif scored[0].get("score", 0) < FIXED["min_score_axis"]:
        out["bets"] = None
        out["bets_skip_reason"] = f"軸の点が{FIXED['min_score_axis']}点未満"
    elif summary_race is None:
        out["bets"] = None
        out["bets_skip_reason"] = "サマリーにレースが無い"
    else:
        axis_num, axis_name, menus = build_menus(scored, summary_race)
        out["bets"] = {"axis_num": axis_num, "axis_name": axis_name, "menu_rule": menu_rule_of(summary_race),
                       "menus": menus}
    return out


def main():
    date = sys.argv[1]
    day = OUTPUT_DIR / date
    pickup_path, xlsx_path, summary_path = day / "pickup_scores.json", OUTPUT_DIR / f"{date}.xlsx", SUMMARY_DIR / f"{date}.json"
    pickup = load_json(pickup_path)
    if pickup is None or not xlsx_path.exists():
        print(f"{date}: pickup_scores.json か指数の表（xlsx）が無い → 作らない")
        sys.exit(1)
    tables = load_index_tables(xlsx_path)
    sidx = build_summary_index(load_json(summary_path))
    invalid = load_invalidated(date, OUTPUT_DIR)
    old = load_old()

    races = {}
    for label, rdata in pickup.get("races", {}).items():
        rec = {"quality_issues_now": race_quality_issues(rdata, invalid.get(label))}
        if "error" in rdata:
            rec["skip_reason"] = "採点の記録が無い（エラー）"
            races[label] = rec
            continue
        dfs = {k: (v.copy() if v is not None else None) for k, v in (tables.get(label) or {}).items()}
        for k in scraper.MODES:
            dfs.setdefault(k, None)
        if any(v is None for v in dfs.values()):
            rec["skip_reason"] = "指数の表が無い"
            races[label] = rec
            continue
        new_def = {str(r["馬番"]) for r in scraper.find_triple_top5_rows(label, {k: v.copy() for k, v in dfs.items()})}
        old_def = {str(r["馬番"]) for r in old.find_triple_top5_rows(label, {k: v.copy() for k, v in dfs.items()})}
        scored = rdata.get("scored", [])
        scored_nums = {str(h["馬番"]) for h in scored}
        rec["candidates_new_definition"] = sorted(new_def, key=int)
        rec["candidates_old_method"] = sorted(old_def, key=int)
        # 採点の候補（取消・除外を除く）が、指数の表から今の定義で選んだ候補と合うか（合わなければ評価から外す）
        scratched = set((rdata.get("scratched_nums") or {}).keys())
        rec["candidate_check"] = "一致" if scored_nums == new_def - scratched else \
            f"不一致（採点だけ {sorted(scored_nums - new_def)}・表だけ {sorted(new_def - scratched - scored_nums)}）"
        new_only = sorted(scored_nums - old_def, key=int)
        rec["new_candidates"] = new_only
        summary_race = sidx.get(label)
        rec["include"] = variant(scored, summary_race)
        rec["exclude"] = variant([h for h in scored if str(h["馬番"]) not in new_only], summary_race)
        races[label] = rec

    inputs = {str(p.relative_to(BASE_DIR)): sha256(p) for p in (pickup_path, xlsx_path, summary_path, OLD_MODULE,
                                                                   day / "invalidated.json") if p.exists()}
    head = subprocess.run(["git", "-C", str(BASE_DIR), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(BASE_DIR), "status", "--porcelain", "--", "new_candidates_variants.py",
                            "paper_trade.py", "scraper.py", "data_quality.py", "index_table_check.py",
                            "validate_trifecta_202608.py"], capture_output=True, text=True).stdout.strip()
    out = {"date": date, "version": VARIANTS_VERSION, "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "code_head": head, "code_dirty": dirty, "fixed": FIXED, "inputs_sha256": inputs, "races": races}
    path = day / "new_candidates_variants.json"
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    tmp.replace(path)
    n_new = sum(len(r.get("new_candidates", [])) for r in races.values())
    n_skip = sum(1 for r in races.values() if "skip_reason" in r)
    n_mis = sum(1 for r in races.values() if r.get("candidate_check", "一致") != "一致")
    print(f"{date}: {len(races)}レース（外した {n_skip}・候補の不一致 {n_mis}）、新しく候補 {n_new}頭 → {path}")


if __name__ == "__main__":
    main()
