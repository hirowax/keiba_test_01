#!/usr/bin/env python3
"""
結果取得後の全頭照合（2026-11-02〜。残りのタスク2）。

採点時に保存した馬番・馬ID・正式名（pickup_scores.json の horse_id_map / horse_name_full）を、
結果（race_results.json と race_nonfinishers.json）と全頭照合する。レースIDも照合する。
2026-10-30 の取得ミス（枠番を馬番として読んでいた）を、次からは結果が出た日に気づけるようにするため。

- 一致: 馬番の馬IDと正式名が結果と同じ
- 不一致: 馬ID か正式名が違う、採点時の馬番が結果に無い、結果の馬番が採点時に無い、レースIDが違う
- 照合できない: 馬番の対応表が無いレース（修正前・無効レース等）。数を出す
- 無効レース（invalidated.json）は照合しない

結果は output/{date}/results_check.json に保存。不一致が1件でもあれば終了コード1（cron が LINE で知らせる）。

使い方: python3 verify_results.py 20261101
"""
import json
import re
import sys
from pathlib import Path

from data_quality import load_invalidated, results_save_issue

OUTPUT_DIR = Path(__file__).parent / "output"
SPACES = re.compile(r"[\s　]+")


def load(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_day(date: str, output_dir: Path = OUTPUT_DIR) -> dict:
    d = output_dir / date
    pickup = load(d / "pickup_scores.json") or {}
    results = load(d / "race_results.json") or {}
    others = load(d / "race_nonfinishers.json")
    conditions = load(d / "race_conditions.json") or {}
    invalid = load_invalidated(date, output_dir)

    out = {"date": date, "races": 0, "horses": 0, "match": 0, "mismatch": [], "not_checked": {},
           "nonfinishers_file": others is not None}
    issue = results_save_issue(d)
    if issue:   # ファイルが新旧で混ざっている可能性がある → 照合しない（不一致として知らせる。2026-11-13〜）
        out["save_incomplete"] = issue
        out["mismatch"].append(f"その日の結果を照合しない: {issue}")
        return out
    for label, r in sorted(pickup.get("races", {}).items()):
        if label in invalid:
            out["not_checked"][label] = "無効レース"
            continue
        id_map = r.get("horse_id_map") or {}
        if not id_map:
            out["not_checked"][label] = "馬番の対応表が無い"
            continue
        if label not in results:
            out["not_checked"][label] = "結果が無い"
            continue
        names = r.get("horse_name_full") or {}
        rows = {str(x["num"]): x for x in results[label]}
        rows.update({str(x["num"]): x for x in (others or {}).get(label, [])})
        out["races"] += 1
        rid = (conditions.get(label) or {}).get("race_id")
        if rid and r.get("race_id") and str(rid) != str(r["race_id"]):
            out["mismatch"].append(f"{label}: レースIDが違う（採点 {r['race_id']} / 結果 {rid}）")
        for num, hid in sorted(id_map.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
            out["horses"] += 1
            x = rows.get(str(num))
            if x is None:
                # race_nonfinishers.json が無い日（2026-11-02 より前の取得）は、取消・除外等と区別できない
                why = "結果に無い" + ("" if others is not None else "（取消・除外等の記録が無い日）")
                out["mismatch"].append(f"{label} {num}番: {why}")
                continue
            if str(x.get("horse_id")) != str(hid):
                out["mismatch"].append(f"{label} {num}番: 馬IDが違う（採点 {hid} / 結果 {x.get('horse_id')}）")
                continue
            full = names.get(str(num))
            if full and SPACES.sub("", full) != SPACES.sub("", x.get("name", "")):
                out["mismatch"].append(f"{label} {num}番: 馬名が違う（採点 {full} / 結果 {x.get('name')}）")
                continue
            out["match"] += 1
        extra = sorted(set(rows) - {str(n) for n in id_map}, key=lambda n: int(n) if n.isdigit() else 0)
        for num in extra:
            out["mismatch"].append(f"{label} {num}番: 結果にあるが採点時の対応表に無い")
    return out


def main():
    date = sys.argv[1]
    out = check_day(date)
    with open(OUTPUT_DIR / date / "results_check.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    summary = (f"{date} 全頭照合: {out['races']}レース {out['horses']}頭中 {out['match']}頭一致・"
               f"不一致{len(out['mismatch'])}件・照合できないレース{len(out['not_checked'])}")
    print(summary)
    for m in out["mismatch"]:
        print("  不一致: " + m)
    for label, why in out["not_checked"].items():
        print(f"  照合できない: {label}（{why}）")
    sys.exit(1 if out["mismatch"] else 0)


if __name__ == "__main__":
    main()
