#!/usr/bin/env python3
"""
「新しく候補」を🔥・推奨・買い目に含める場合と外す場合の2つを、固定した設定で作って保存する（評価用）。
基準: docs/new_candidates_criteria_202611.md（4.1。2026-11-06 ユーザー承認）

- 新しく候補: 今の定義（scraper.find_triple_top5_rows）では候補で、旧方式（コミット 6f2ed8d の同じ関数）では候補でない馬。
  旧方式は research/index_top3_compare_202611/old_scraper_6f2ed8d.py（git show 6f2ed8d:scraper.py）を呼ぶ（書き写さない）。
- 含める場合: 採点結果（pickup_scores.json の scored）をそのまま使う。
- 外す場合: scored から新しく候補を除く。推奨・購入の対象から**完全に**外す（軸にしないだけではない）。
  各馬の点数は、ほかの候補の有無に左右されない（前走指数レース内1位の比較相手は出走馬全員）ので、残りの馬の点数は変えない。
  紐・◯もこの一覧で選ぶ。サマリーの3指数の候補（triple）は紐の C・D から除かれるので、外した馬は買い目に入らない。
  作った後に、外した馬が全券種の組み合わせ・軸・◯・紐に入っていないことを検査する（入っていれば止める）。

固定（2026-11-06 外部レビュー指摘で強めた）:
- 設定（FIXED）はこのファイルに書いた値を使う（実行時のモジュールの定数を使わない）。
- 頼っている関数の中身（FROZEN_CODE の sha256）と、旧方式のファイル・開始時の閾値設定の sha256 が違えば、何も書かずに止める。
- 入力の pickup_scores.json の採点の版（scoring_version）が FIXED と違えば止める。レースの候補の版が違えば、そのレースは評価の対象外。

評価の対象（レースごとに1つの判定 eligible と理由 reasons）:
- 採点の記録が無い（pickup_scores.json に無い・エラー）、品質の問題（実行時の race_quality_issues）、指数の表が無い、
  候補の版が違う、採点の候補が指数の表から今の定義で選んだ候補と合わない → 対象外。対象外のレースには🔥・買い目を作らない。
- 採点の記録が無いレースは、指数の表から今の定義で候補がいるのに pickup_scores.json に無いレースも数える。

保存: output/YYYYMMDD/new_candidates_variants/{実行ID}.json。入力・設定・コードのハッシュが同じ記録が既にあれば作らない
（前の記録を置き換えない）。違えば別の実行として足す。
成績（着順・払戻）は読まない。本番のファイルは書き換えない。

usage: python3 new_candidates_variants.py YYYYMMDD
"""
import hashlib
import importlib.util
import inspect
import json
import logging
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

import paper_trade
import scraper
import validate_trifecta_202608
from data_quality import load_invalidated, race_quality_issues
from index_table_check import load_index_tables

logging.disable(logging.CRITICAL)
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
SUMMARY_DIR = BASE_DIR / "summary"
EVAL_DIR = BASE_DIR / "research" / "new_candidates_eval_202611"
OLD_MODULE = BASE_DIR / "research" / "index_top3_compare_202611" / "old_scraper_6f2ed8d.py"
THRESHOLD_START = EVAL_DIR / "threshold_config_start.json"   # 評価の期間の始め（2026-11-07 の採点）の閾値設定の写し
VARIANTS_VERSION = "含める場合と外す場合_固定設定_20261106b"

FIXED = {
    "ev_threshold": 9,
    "next_range": [7, 8],          # 次点: 閾値−2〜閾値−1
    "min_score_axis": 8,           # 買い目の軸の最低点（validate_trifecta_202608.MIN_SCORE の 2026-11-06 の値）
    "bet_unit": 100,               # 1点の金額（validate_trifecta_202608.BET_UNIT の 2026-11-06 の値）
    "scoring_version": "v5.1",
    "candidate_rule_version": "ページの順位で3位以内_同じ順位は全員_20261102",
}
FIXED_FILES_SHA256 = {
    "old_module": "4c8ea46e26a0899ec6762a09318858e7f907e6b766fb38ff00c0e031718fc5d9",
    "threshold_start": "f136d9e646dc8244949807a648a1c5d7d1a7f9f8b5e7d65b7f8ff14d8dd4ccdf",
}
# 頼っている関数の中身（inspect.getsource の sha256 の先頭16文字。2026-11-06 の値）
FROZEN_CODE = {
    "paper_trade.build_menus": "3dd1038fc58cc54d",
    "paper_trade.maru_num_page_rank": "10e249153c5c56b1",
    "paper_trade.himo_set_page_rank": "f6cf3893cef7400b",
    "paper_trade._page_rank_lists": "17d8f9d4ba9c6a8b",
    "paper_trade.menu_rule_of": "ae6c0cc3b9caff10",
    "paper_trade.build_summary_index": "41207373ac301a68",
    "validate_trifecta_202608.maru_num": "340b6e4536e23e9a",
    "validate_trifecta_202608.himo_set": "13dadc08ce72c62d",
    "validate_trifecta_202608.combo_key": "9b29a85c761dda28",
    "scraper.find_triple_top5_rows": "93fbbaacdc715bbc",
    "scraper.ranked_horses": "11b1eeb4000af71f",
    "scraper.top_rank_horses": "dff209149b83071d",
    "scraper._table_columns": "72a44ba7b052a0ea",
    "scraper.classify_index_text": "bbb3579734b07702",
}
FROZEN_CONSTANTS = {   # 頼っているモジュールの定数（関数の中身のハッシュに入らないもの）
    "paper_trade.BET_UNIT": 100,
    "paper_trade.MIN_SCORE": 8,
    "scraper.CANDIDATE_RULE_VERSION": FIXED["candidate_rule_version"],
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_hashes() -> dict:
    mods = {"paper_trade": paper_trade, "validate_trifecta_202608": validate_trifecta_202608, "scraper": scraper}
    out = {}
    for key in FROZEN_CODE:
        mod, name = key.split(".", 1)
        out[key] = hashlib.sha256(inspect.getsource(getattr(mods[mod], name)).encode()).hexdigest()[:16]
    return out


def check_frozen() -> list:
    """固定したものが変わっていないか。変わっていれば理由のリスト"""
    problems = []
    now = code_hashes()
    for k, v in FROZEN_CODE.items():
        if now[k] != v:
            problems.append(f"関数の中身が変わった: {k}（固定 {v}・今 {now[k]}）")
    mods = {"paper_trade": paper_trade, "scraper": scraper}
    for k, v in FROZEN_CONSTANTS.items():
        mod, name = k.split(".", 1)
        if getattr(mods[mod], name) != v:
            problems.append(f"定数が変わった: {k}（固定 {v!r}・今 {getattr(mods[mod], name)!r}）")
    for name, path in (("old_module", OLD_MODULE), ("threshold_start", THRESHOLD_START)):
        if not path.exists() or sha256_file(path) != FIXED_FILES_SHA256[name]:
            problems.append(f"固定したファイルが無いか変わった: {path}")
    if THRESHOLD_START.exists() and (load_json(THRESHOLD_START) or {}).get("ev_threshold") != FIXED["ev_threshold"]:
        problems.append("開始時の閾値設定と FIXED の閾値が違う")
    return problems


def rel_name(p: Path) -> str:
    """記録に残すファイル名（リポジトリの中ならリポジトリからの相対、外なら絶対）"""
    try:
        return str(p.resolve().relative_to(BASE_DIR.resolve()))
    except ValueError:
        return str(p)


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
    """1つの場合の🔥・次点・買い目（FIXED の値で判定する）"""
    th = FIXED["ev_threshold"]
    lo, hi = FIXED["next_range"]
    out = {"scored": [{"馬番": str(h["馬番"]), "馬名": h.get("馬名", ""), "score": h.get("score", 0)} for h in scored],
           "fire": [str(h["馬番"]) for h in scored if h.get("score", 0) >= th],
           "next": [str(h["馬番"]) for h in scored if lo <= h.get("score", 0) <= hi]}
    if not scored:
        out["bets"], out["bets_skip_reason"] = None, "候補が0頭"
    elif scored[0].get("score", 0) < FIXED["min_score_axis"]:
        out["bets"], out["bets_skip_reason"] = None, f"軸の点が{FIXED['min_score_axis']}点未満"
    elif summary_race is None:
        out["bets"], out["bets_skip_reason"] = None, "サマリーにレースが無い"
    else:
        axis_num, axis_name, menus = paper_trade.build_menus(scored, summary_race)
        out["bets"] = {"axis_num": axis_num, "axis_name": axis_name, "menu_rule": paper_trade.menu_rule_of(summary_race),
                       "menus": menus}
    return out


def horses_in_bets(bets) -> set:
    """買い目に出てくる馬番（軸・◯・紐・全券種の組み合わせ）"""
    if not bets:
        return set()
    nums = {str(bets["axis_num"])}
    for m in bets["menus"].values():
        if m.get("o_num") is not None:
            nums.add(str(m["o_num"]))
        nums |= {str(x) for x in m.get("himo", [])}
        for key in m.get("bets", {}):
            nums |= {x for x in str(key).split("-") if x}
    return nums


def candidates_from_tables(dfs, fn) -> set:
    return {str(r["馬番"]) for r in fn("", {k: (v.copy() if v is not None else None) for k, v in dfs.items()})}


def main():
    date = sys.argv[1]
    problems = check_frozen()
    if problems:
        print("固定したものが変わったので作らない:\n  " + "\n  ".join(problems))
        sys.exit(2)
    day = OUTPUT_DIR / date
    pickup_path, xlsx_path, summary_path = day / "pickup_scores.json", OUTPUT_DIR / f"{date}.xlsx", SUMMARY_DIR / f"{date}.json"
    pickup = load_json(pickup_path)
    if pickup is None or not xlsx_path.exists():
        print(f"{date}: pickup_scores.json か指数の表（xlsx）が無い → 作らない")
        sys.exit(1)
    if pickup.get("scoring_version") != FIXED["scoring_version"]:
        print(f"{date}: 採点の版が {pickup.get('scoring_version')!r}（固定は {FIXED['scoring_version']!r}）→ 作らない")
        sys.exit(2)

    tables = load_index_tables(xlsx_path)
    sidx = paper_trade.build_summary_index(load_json(summary_path))
    invalid = load_invalidated(date, OUTPUT_DIR)
    old = load_old()

    races = {}
    labels = sorted(set(pickup.get("races", {})) | set(tables))
    for label in labels:
        rdata = pickup.get("races", {}).get(label)
        dfs = dict(tables.get(label) or {})
        for k in scraper.MODES:
            dfs.setdefault(k, None)
        has_tables = all(v is not None for v in dfs.values())
        new_def = candidates_from_tables(dfs, lambda _l, d: scraper.find_triple_top5_rows(label, d)) if has_tables else set()
        if rdata is None:
            if new_def:   # 今の定義で候補がいるのに採点の記録が無い
                races[label] = {"eligible": False, "reasons": ["採点の記録が無い（pickup_scores.json に無い）"],
                                "candidates_new_definition": sorted(new_def, key=int)}
            continue
        reasons = []
        if "error" in rdata:
            reasons.append(f"採点の記録が無い（エラー: {rdata['error']}）")
        q_now = race_quality_issues(rdata, invalid.get(label))
        reasons += [f"品質: {x}" for x in q_now]
        if not has_tables:
            reasons.append("指数の表が無い")
        rule = (rdata.get("quality") or {}).get("candidate_rule")
        if rule != FIXED["candidate_rule_version"]:
            reasons.append(f"候補の版が違う（{rule!r}）")
        rec = {"quality_issues_now": q_now}
        if has_tables and "error" not in rdata:
            old_def = candidates_from_tables(dfs, lambda _l, d: old.find_triple_top5_rows(label, d))
            scored = rdata.get("scored", [])
            scored_nums = {str(h["馬番"]) for h in scored}
            scratched = set((rdata.get("scratched_nums") or {}).keys())
            rec["candidates_new_definition"] = sorted(new_def, key=int)
            rec["candidates_old_method"] = sorted(old_def, key=int)
            if scored_nums != new_def - scratched:
                reasons.append(f"採点の候補が指数の表の候補と合わない（採点だけ {sorted(scored_nums - new_def)}・"
                               f"表だけ {sorted(new_def - scratched - scored_nums)}）")
            rec["new_candidates"] = sorted(scored_nums - old_def, key=int)
        rec["eligible"] = not reasons
        rec["reasons"] = reasons
        if rec["eligible"]:
            new_only = set(rec["new_candidates"])
            summary_race = sidx.get(label)
            rec["include"] = variant(scored, summary_race)
            rec["exclude"] = variant([h for h in scored if str(h["馬番"]) not in new_only], summary_race)
            leaked = horses_in_bets(rec["exclude"]["bets"]) & new_only
            if leaked or set(rec["exclude"]["fire"]) & new_only:
                print(f"{date} {label}: 外す場合の🔥・買い目に新しく候補 {sorted(leaked)} が入った → 作らない")
                sys.exit(3)
        races[label] = rec

    inputs = {rel_name(p): sha256_file(p) for p in (pickup_path, xlsx_path, summary_path, OLD_MODULE,
                                                                        THRESHOLD_START, day / "invalidated.json")
              if p.exists()}
    key_src = {"inputs": inputs, "fixed": FIXED, "frozen_code": FROZEN_CODE, "version": VARIANTS_VERSION,
               "script": sha256_file(Path(__file__))}
    key = hashlib.sha256(json.dumps(key_src, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    out_dir = day / "new_candidates_variants"
    out_dir.mkdir(exist_ok=True)
    for p in sorted(out_dir.glob("*.json")):
        if (load_json(p) or {}).get("key") == key:
            print(f"{date}: 入力・設定・コードが同じ記録がある → 作らない（{p.name}）")
            return
    head = subprocess.run(["git", "-C", str(BASE_DIR), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S%f") + "_" + uuid.uuid4().hex[:8]
    out = {"date": date, "run_id": run_id, "key": key, "version": VARIANTS_VERSION,
           "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
           "code_head": head, "script_sha256": key_src["script"], "fixed": FIXED, "frozen_code": FROZEN_CODE,
           "inputs_sha256": inputs, "races": races}
    path = out_dir / f"{run_id}.json"
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    tmp.replace(path)
    n_el = sum(1 for r in races.values() if r["eligible"])
    n_new = sum(len(r.get("new_candidates", [])) for r in races.values() if r["eligible"])
    print(f"{date}: {len(races)}レース（評価の対象 {n_el}・対象外 {len(races) - n_el}）、対象のレースの新しく候補 {n_new}頭 → {path}")


if __name__ == "__main__":
    main()
