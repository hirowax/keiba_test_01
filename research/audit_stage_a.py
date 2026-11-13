#!/usr/bin/env python3
"""段階A: データと検証方法の読み取り専用監査

handoff/netkeiba_claude_plan_20261028.md の段階A（A1〜A4）を実施する。

READ ONLY: output/ 配下は一切書き換えない。成果物は
research/artifacts/audit_<run_id>/ にのみ書き出す。

usage: python3 research/audit_stage_a.py [--run-id 20261028]
"""

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import sys as _sys  # 2026-11-13〜: 結果の保存が不完全な日があれば止める
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from data_quality import stop_if_any_results_save_incomplete
stop_if_any_results_save_incomplete()

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

# 前走系ファクターのクリーン境界（docs/factor_audit_202606.md）
CLEAN_START = "20260328"
# スコアリング時点オッズの保存開始（docs/roi_improvement_plan.md 柱1 Phase A）
ODDS_START = "20260711"
# ペーパートレード前向き記録の開始（事前登録値）
PAPER_TRADE_START = "20260912"

PER_DATE_FILES = [
    "pickup_scores.json",
    "prev_data.json",
    "race_results.json",
    "race_conditions.json",
    "payouts_jra.json",
    "paper_bets.json",
]
GLOBAL_FILES = [
    "horse_db.json",
    "horse_style.json",
    "horse_pedigree.json",
    "threshold_config.json",
    "dates.json",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": str(e)}


def git_state() -> dict:
    def run(*args):
        try:
            return subprocess.run(args, cwd=BASE_DIR, capture_output=True,
                                  text=True, check=False).stdout.strip()
        except Exception as e:
            return f"<error: {e}>"
    return {
        "head": run("git", "rev-parse", "HEAD"),
        "head_short": run("git", "rev-parse", "--short", "HEAD"),
        "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "head_subject": run("git", "log", "-1", "--format=%s"),
        "head_date": run("git", "log", "-1", "--format=%cI"),
        "status_porcelain": run("git", "status", "--porcelain"),
    }


def iter_date_dirs():
    for p in sorted(OUTPUT_DIR.iterdir()):
        if p.is_dir() and re.match(r"^\d{8}$", p.name):
            yield p.name, p


def norm_prev_date(s: str) -> str:
    """'2026/04/25' → '20260425'。不正なら空文字"""
    d = re.sub(r"[^\d]", "", str(s or ""))
    return d if len(d) == 8 else ""


# ─── A1: 状態と件数 ────────────────────────────────────────────────
def audit_a1(run_dir: Path) -> dict:
    inventory = []
    per_date = []
    for date, d in iter_date_dirs():
        row = {"date": date}
        for fn in PER_DATE_FILES:
            p = d / fn
            exists = p.exists()
            row[fn] = int(exists)
            if exists:
                inventory.append({
                    "path": str(p.relative_to(BASE_DIR)),
                    "bytes": p.stat().st_size,
                    "sha256": sha256(p),
                })
        per_date.append(row)

    for fn in GLOBAL_FILES:
        p = OUTPUT_DIR / fn
        if p.exists():
            inventory.append({
                "path": str(p.relative_to(BASE_DIR)),
                "bytes": p.stat().st_size,
                "sha256": sha256(p),
            })

    # 件数: 開催日 / レース / 馬 を区別して数える
    counts = {"date_dirs": len(per_date)}
    for fn in PER_DATE_FILES:
        counts[f"dates_with_{fn}"] = sum(r[fn] for r in per_date)

    races_ps = horses_scored = horses_anaba = 0
    races_rr = horses_rr = 0
    races_pay = 0
    for date, d in iter_date_dirs():
        ps = load_json(d / "pickup_scores.json") if (d / "pickup_scores.json").exists() else {}
        for rl, rd in (ps.get("races") or {}).items():
            if "error" in rd:
                continue
            races_ps += 1
            horses_scored += len(rd.get("scored") or [])
            horses_anaba += len(rd.get("anaba") or [])
        rr = load_json(d / "race_results.json") if (d / "race_results.json").exists() else {}
        if isinstance(rr, dict):
            for rl, hs in rr.items():
                if isinstance(hs, list):
                    races_rr += 1
                    horses_rr += len(hs)
        pay = load_json(d / "payouts_jra.json") if (d / "payouts_jra.json").exists() else {}
        if isinstance(pay, dict):
            races_pay += sum(1 for k, v in pay.items() if isinstance(v, dict))

    counts.update({
        "races_in_pickup_scores": races_ps,
        "horses_scored_candidates": horses_scored,
        "horses_anaba_rows": horses_anaba,
        "races_in_race_results": races_rr,
        "horses_in_race_results": horses_rr,
        "races_in_payouts": races_pay,
    })

    with open(run_dir / "a1_per_date_presence.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date"] + PER_DATE_FILES)
        w.writeheader()
        w.writerows(per_date)
    with open(run_dir / "a1_file_inventory.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["path", "bytes", "sha256"])
        w.writeheader()
        w.writerows(inventory)

    return {"counts": counts, "n_inventory": len(inventory), "per_date": per_date}


# ─── A2: 項目別の利用可能性 ───────────────────────────────────────
FIELD_SPECS = [
    # (列名, 説明, 由来: pre=予想時点 / post=結果由来 / none=未保存)
    ("race_id", "レースID", "pre"),
    ("horse_id", "馬ID", "pre"),
    ("umaban", "馬番", "pre"),
    ("field_size_prerace", "出走頭数（予想時点の保存値）", "none"),
    ("field_size_postrace", "出走頭数（race_conditions由来）", "post"),
    ("scratch_state", "取消・除外・中止の明示状態", "none"),
    ("idx_values_3", "3指数の実数値3つ", "pre"),
    ("idx_ranks_3", "3指数の順位3つ", "pre"),
    ("idx_basis_nraces", "指数根拠の出走回数", "none"),
    ("triple_flag", "3指数重複条件の判定", "pre"),
    ("breakdown", "加点要素の内訳", "pre"),
    ("score", "スコア", "pre"),
    ("scoring_version", "scoring_version", "pre"),
    ("generated_at", "予想生成時刻", "pre"),
    ("rescored_at", "再スコア日時", "pre"),
    ("odds_prerace", "予想時点単勝オッズ", "pre"),
    ("pop_prerace", "予想時点人気", "pre"),
    ("odds_fetch_time_per_race", "オッズのレース単位取得時刻", "none"),
    ("place_odds_prerace", "予想時点の複勝レンジ", "none"),
    ("prev_date", "前走日", "pre"),
    ("prev_fetch_time", "前走の取得時刻", "pre"),
    ("prev_snapshot_perdate", "前走の日別スナップショット", "pre"),
    ("style_history_dates", "脚質履歴の日付", "none"),
    ("final_rank", "確定着順", "post"),
    ("final_odds", "確定単勝オッズ", "post"),
    ("final_place_odds", "確定複勝オッズ", "post"),
    ("jra_payout", "JRA払戻", "post"),
    ("last3f", "上がり3F", "post"),
    ("corners", "コーナー通過順", "post"),
    ("margin", "着差", "post"),
]


def audit_a2(run_dir: Path) -> dict:
    rows = []  # 1行 = race_id × 馬番 × snapshot
    for date, d in iter_date_dirs():
        ps_path = d / "pickup_scores.json"
        if not ps_path.exists():
            continue
        ps = load_json(ps_path)
        if "__error__" in ps:
            continue
        generated_at = ps.get("generated_at") or ""
        rescored_at = ps.get("rescored_at") or ""
        version = ps.get("scoring_version") or ""
        rr = load_json(d / "race_results.json") if (d / "race_results.json").exists() else {}
        rc = load_json(d / "race_conditions.json") if (d / "race_conditions.json").exists() else {}
        pay = load_json(d / "payouts_jra.json") if (d / "payouts_jra.json").exists() else {}
        prev = load_json(d / "prev_data.json") if (d / "prev_data.json").exists() else {}
        prev = prev if isinstance(prev, dict) else {}

        for rl, rd in (ps.get("races") or {}).items():
            if "error" in rd:
                continue
            race_id = rd.get("race_id") or ""
            odds_map = rd.get("odds_map") or {}
            pop_map = rd.get("pop_map") or {}
            hid_map = rd.get("horse_id_map") or {}
            scored = {str(h.get("馬番")): h for h in (rd.get("scored") or [])}
            res_list = rr.get(rl) if isinstance(rr, dict) else None
            res = {str(h.get("num")): h for h in res_list} if isinstance(res_list, list) else {}
            cond = rc.get(rl) if isinstance(rc, dict) else None
            payr = pay.get(rl) if isinstance(pay, dict) else None

            universe = set(odds_map) | set(pop_map) | set(hid_map) | set(res) | set(scored)
            for num in sorted(universe, key=lambda x: (len(x), x)):
                s = scored.get(num)
                r = res.get(num, {})
                rows.append({
                    "date": date,
                    "month": date[:6],
                    "race_label": rl,
                    "race_id": race_id,
                    "umaban": num,
                    "is_candidate": int(s is not None),
                    # 識別子
                    "f_race_id": int(bool(race_id)),
                    "f_horse_id": int(bool(hid_map.get(num))),
                    "f_umaban": 1,
                    "f_field_size_prerace": 0,
                    "f_field_size_postrace": int(bool((cond or {}).get("num_horses"))),
                    "f_scratch_state": 0,
                    # 3指数
                    "f_idx_values_3": int(bool(s) and all(
                        s.get(k) not in (None, "") for k in
                        ("近走平均指数", "当該距離指数", "当該コース指数"))),
                    "f_idx_ranks_3": int(bool(s) and all(
                        s.get(k) not in (None, "") for k in
                        ("近走平均順位", "当該距離順位", "当該コース順位"))),
                    "f_idx_basis_nraces": 0,
                    "f_triple_flag": int(s is not None),
                    # スコア
                    "f_breakdown": int(bool(s) and s.get("breakdown") is not None),
                    "f_score": int(bool(s) and s.get("score") is not None),
                    "f_scoring_version": int(bool(version)),
                    "f_generated_at": int(bool(generated_at)),
                    "f_rescored_at": int(bool(rescored_at)),
                    # オッズ・人気（予想時点）
                    "f_odds_prerace": int(num in odds_map and odds_map.get(num) not in (None, "")),
                    "f_pop_prerace": int(num in pop_map and pop_map.get(num) not in (None, "")),
                    "f_odds_fetch_time_per_race": 0,
                    "f_place_odds_prerace": 0,
                    # 前走・脚質
                    "f_prev_date": int(bool(prev.get(hid_map.get(num, ""), {}).get("prev_date"))),
                    "f_prev_fetch_time": int(bool(prev.get(hid_map.get(num, ""), {}).get("scraped_at"))),
                    "f_prev_snapshot_perdate": int((d / "prev_data.json").exists()),
                    "f_style_history_dates": 0,
                    # 結果
                    "f_final_rank": int(r.get("rank") is not None),
                    "f_final_odds": int(r.get("odds") is not None),
                    "f_final_place_odds": int(r.get("place_odds") is not None),
                    "f_jra_payout": int(isinstance(payr, dict) and bool(payr)),
                    "f_last3f": int(bool(r.get("last3f"))),
                    "f_corners": int(bool(r.get("corners"))),
                    "f_margin": int(r.get("margin") not in (None, "")),
                })

    fields = [k for k in rows[0]] if rows else []
    with open(run_dir / "a2_rows.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    fcols = [c for c in fields if c.startswith("f_")]

    def rate_table(group_key, subset=None):
        buckets = defaultdict(list)
        for r in rows:
            if subset is not None and r["is_candidate"] != subset:
                continue
            buckets[r[group_key]].append(r)
        out = []
        for g in sorted(buckets):
            rs = buckets[g]
            row = {group_key: g, "n_rows": len(rs)}
            for c in fcols:
                row[c] = round(sum(x[c] for x in rs) / len(rs) * 100, 1)
            out.append(row)
        return out

    for name, table in [
        ("a2_rate_by_month_all.csv", rate_table("month")),
        ("a2_rate_by_month_candidates.csv", rate_table("month", subset=1)),
        ("a2_rate_by_month_noncandidates.csv", rate_table("month", subset=0)),
        ("a2_rate_by_date_all.csv", rate_table("date")),
    ]:
        if table:
            with open(run_dir / name, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(table[0]))
                w.writeheader()
                w.writerows(table)

    # オッズ欠損レースの割合（完全データのみ分析の偏り点検）
    race_odds = defaultdict(lambda: {"n": 0, "with_odds": 0})
    for r in rows:
        key = (r["date"], r["race_label"])
        race_odds[key]["n"] += 1
        race_odds[key]["with_odds"] += r["f_odds_prerace"]
    races_total = len(race_odds)
    races_no_odds = sum(1 for v in race_odds.values() if v["with_odds"] == 0)
    races_partial = sum(1 for v in race_odds.values()
                        if 0 < v["with_odds"] < v["n"])

    cand = [r for r in rows if r["is_candidate"] == 1]
    noncand = [r for r in rows if r["is_candidate"] == 0]
    overall = {
        "n_rows": len(rows),
        "n_candidate_rows": len(cand),
        "n_noncandidate_rows": len(noncand),
        "races_total": races_total,
        "races_with_zero_prerace_odds": races_no_odds,
        "races_with_partial_prerace_odds": races_partial,
        "rates_all": {c: round(sum(r[c] for r in rows) / len(rows) * 100, 1) for c in fcols} if rows else {},
        "rates_candidates": {c: round(sum(r[c] for r in cand) / len(cand) * 100, 1) for c in fcols} if cand else {},
        "rates_noncandidates": {c: round(sum(r[c] for r in noncand) / len(noncand) * 100, 1) for c in fcols} if noncand else {},
    }
    return overall


# ─── A3: 時点整合性 ───────────────────────────────────────────────
def audit_a3(run_dir: Path) -> dict:
    prev_viol = []
    prev_checked = 0
    provenance = []
    for date, d in iter_date_dirs():
        ps_path = d / "pickup_scores.json"
        if not ps_path.exists():
            continue
        ps = load_json(ps_path)
        if "__error__" in ps:
            continue
        gen = ps.get("generated_at") or ""
        resc = ps.get("rescored_at") or ""
        has_snapshot = (d / "prev_data.json").exists()
        # 証跡ラベル: 当時保存 / 再スコア / 遡及作成 / 証跡不明
        if not gen:
            label = "証跡不明"
        elif gen[:10].replace("-", "") < date:
            label = "当時保存(前日以前の生成)"
        elif gen[:10].replace("-", "") == date:
            label = "当日生成(発走前後は時刻のみでは不明)"
        else:
            label = "遡及作成(対象日より後に生成)"
        if resc:
            label += " / 再スコア済"
        provenance.append({
            "date": date,
            "generated_at": gen,
            "rescored_at": resc,
            "scoring_version": ps.get("scoring_version") or "",
            "has_prev_snapshot": int(has_snapshot),
            "clean_window": int(date >= CLEAN_START),
            "odds_era": int(date >= ODDS_START),
            "provenance_label": label,
        })

        prev = load_json(d / "prev_data.json") if has_snapshot else {}
        if isinstance(prev, dict):
            for hid, e in prev.items():
                if hid.startswith("__"):
                    continue
                pd_ = norm_prev_date(e.get("prev_date"))
                if not pd_:
                    continue
                prev_checked += 1
                if pd_ >= date:
                    prev_viol.append({"date": date, "horse_id": hid, "prev_date": pd_})

    with open(run_dir / "a3_provenance_by_date.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(provenance[0]) if provenance else ["date"])
        w.writeheader()
        w.writerows(provenance)
    if prev_viol:
        with open(run_dir / "a3_prev_date_violations.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["date", "horse_id", "prev_date"])
            w.writeheader()
            w.writerows(prev_viol)

    # horse_style.json: 日付次元の有無
    hs_path = OUTPUT_DIR / "horse_style.json"
    hs = load_json(hs_path) if hs_path.exists() else {}
    hs_keys = set()
    if isinstance(hs, dict):
        for v in list(hs.values())[:200]:
            if isinstance(v, dict):
                hs_keys |= set(v)
    # horse_db.json: グローバル単一キャッシュ（日付次元なし）
    hdb_path = OUTPUT_DIR / "horse_db.json"
    hdb = load_json(hdb_path) if hdb_path.exists() else {}
    hdb_future = 0
    hdb_checked = 0
    if isinstance(hdb, dict):
        for hid, e in hdb.items():
            if not isinstance(e, dict):
                continue
            pd_ = norm_prev_date(e.get("prev_date"))
            if pd_:
                hdb_checked += 1

    # 結合キーの一意性: race_id×馬番の重複
    dup = Counter()
    for date, d in iter_date_dirs():
        ps_path = d / "pickup_scores.json"
        if not ps_path.exists():
            continue
        ps = load_json(ps_path)
        for rl, rd in (ps.get("races") or {}).items():
            if "error" in rd:
                continue
            rid = rd.get("race_id") or f"{date}:{rl}"
            for h in (rd.get("scored") or []):
                dup[(rid, str(h.get("馬番")))] += 1
    dup_keys = {k: v for k, v in dup.items() if v > 1}

    prov_counter = Counter(p["provenance_label"] for p in provenance)
    return {
        "prev_date_checked": prev_checked,
        "prev_date_violations": len(prev_viol),
        "horse_style_value_keys": sorted(hs_keys),
        "horse_style_has_date_dimension": int(any("date" in k for k in hs_keys)),
        "horse_style_n_horses": len(hs) if isinstance(hs, dict) else 0,
        "horse_db_entries_with_prev_date": hdb_checked,
        "duplicate_raceid_umaban_keys": len(dup_keys),
        "provenance_counts": dict(prov_counter),
        "n_dates_profiled": len(provenance),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d"))
    args = ap.parse_args()

    run_dir = BASE_DIR / "research" / "artifacts" / f"audit_{args.run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now().isoformat()
    git = git_state()
    a1 = audit_a1(run_dir)
    a2 = audit_a2(run_dir)
    a3 = audit_a3(run_dir)

    summary = {
        "run_id": args.run_id,
        "started_at": started,
        "finished_at": datetime.now().isoformat(),
        "base_dir": str(BASE_DIR),
        "read_only": True,
        "git": git,
        "constants": {
            "CLEAN_START": CLEAN_START,
            "ODDS_START": ODDS_START,
            "PAPER_TRADE_START": PAPER_TRADE_START,
        },
        "a1": {"counts": a1["counts"], "n_inventory_files": a1["n_inventory"]},
        "a2": a2,
        "a3": a3,
        "field_specs": [{"field": f, "desc": dsc, "origin": o} for f, dsc, o in FIELD_SPECS],
    }
    with open(run_dir / "audit_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps({k: summary[k] for k in ("run_id", "constants")}, ensure_ascii=False))
    print("A1 counts:", json.dumps(a1["counts"], ensure_ascii=False, indent=2))
    print("A2:", json.dumps({k: v for k, v in a2.items() if not k.startswith("rates")},
                            ensure_ascii=False, indent=2))
    print("A3:", json.dumps(a3, ensure_ascii=False, indent=2))
    print("出力先:", run_dir.relative_to(BASE_DIR))


if __name__ == "__main__":
    sys.exit(main())
