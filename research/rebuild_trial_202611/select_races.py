"""
タスク8の試しの対象レースを、決まった規則で選ぶ（2026-11-07。ページは取りに行かない。成績・着順・払戻は見ない）。

規則（docs/rebuild_trial_plan_202611.md の 3.1）:
- 日: 対照 20261107 から2レース、馬番を直した後の 20261031・20261101 から1レースずつ、直す前の範囲の最初と最後の開催日
  20260912・20261025 から1レースずつ。
- レース: その日の output/{日}.xlsx で、タスク7と同じ表の検査（3つの表がそろう・取得異常なし・順位の食い違いなし）を通り、
  今の定義（scraper.find_triple_top5_rows）で候補が1頭以上いる平地のレース。平地か障害戦かは、当該距離の表の指数の列の見出し
  （例「障害2910最高指数」「芝1600最高指数」）に「障害」があるかで見分ける（前日の表だけで決まり、結果を読まない。11/7 は結果がまだ無い）。
  invalidated.json に載ったレースは外す。
  さらに、当時採点したレース（pickup_scores.json にあるレース）に限る。保存した入力（上位3頭など・予測ペース・馬ID）と、
  後から取り直した値を比べるのが試しの目的だから（2026-11-07 に一度、当時採点していないレースも含めて選んだところ、10/31・11/1 で
  当時採点していないレースが選ばれ、保存値が無かったので、規則をこう決め直した。成績は見ていない）。
  限界: 新しい定義で初めて候補が出るレース（当時採点していないレース）は、この試しに入らない。
  レースIDは pickup_scores.json の race_id。その中でレースIDの小さい順に必要な数だけ。
- 着順・払戻・結果のファイルは読まない。

実行: python3 research/rebuild_trial_202611/select_races.py
出力: このフォルダの selected_races.json
"""
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import scraper  # noqa: E402
from index_table_check import load_index_tables  # noqa: E402

logging.disable(logging.CRITICAL)
HERE = Path(__file__).resolve().parent
PLAN = [("20261107", 2, "対照（本番の入力の記録がある）"), ("20261031", 1, "馬番を直した後"), ("20261101", 1, "馬番を直した後"),
        ("20260912", 1, "直す前（範囲の最初の開催日）"), ("20261025", 1, "直す前（範囲の最後の開催日）")]


def main():
    out = []
    for d, n, why in PLAN:
        tables = load_index_tables(ROOT / "output" / f"{d}.xlsx")
        pk = json.load(open(ROOT / "output" / d / "pickup_scores.json", encoding="utf-8"))["races"]
        inv_p = ROOT / "output" / d / "invalidated.json"
        inv = json.load(open(inv_p, encoding="utf-8")).get("races", {}) if inv_p.exists() else {}
        cands = []
        for lab, dfs in tables.items():
            rid = (pk.get(lab) or {}).get("race_id")
            if not rid:
                continue
            if lab in inv or set(dfs) != set(scraper.MODES):
                continue
            idx_col = next((c for c in dfs["distance"].columns if "指数" in str(c)), "")
            if "障害" in str(idx_col):
                continue
            ch = [scraper.ranked_horses(v) for v in dfs.values()]
            if any(c[1] or c[2] for c in ch):
                continue
            if scraper.find_triple_top5_rows(lab, dfs):
                cands.append((rid, lab))
        for rid, lab in sorted(cands)[:n]:
            out.append({"date": d, "label": lab, "race_id": rid, "why": why, "candidates_in_day": len(cands),
                        })
    for x in out:
        print(x)
    (HERE / "selected_races.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
