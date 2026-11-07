"""
タスク8の試し・対照の手順1（docs/rebuild_trial_plan_202611.md の 3.2）: 11/7 の本番の点数を、保存した入力と当時のコードで再現する。
2026-11-07。ページは取りに行かない。成績・着順は見ない。

- 当時のコード: 本番の実行のコミット（prev_inputs の記録の commit = 7b65ee7）。git worktree でそのコミットを別のフォルダに出し、
  そこにある race_pickup.py・prev_inputs.py・horse_style.json を使う（今のコードを混ぜない）。
  実行: python3 research/rebuild_trial_202611/reproduce_control.py <worktree のフォルダ>
- 保存した入力（今のリポジトリの output/20261107/。書き換えない）:
  - 全場_3指数重複馬.csv（候補。本番の run_pickup_all.py もこれを読む）
  - pickup_scores.json の position_nums・top3_hits・horse_id_map・pop_map・odds_map・scratched_nums・pickup_nums・analysis_hits・
    predicted_pace・race_dist
  - prev_inputs/{実行ID}.json（前走データの記録。prev_db_entry と race_top1 で採点の入力にする。本番の collect_prev_for_race と同じ）
- pickup_nums は pickup_scores.json に集合の文字列（例 "{'10', '6', '7'}"）で保存されている（本番が集合を json.dump の
  default=str で書いたため）。そのまま渡すと「1」が文字列の一部として一致してしまうので、ast.literal_eval で集合に戻す
  （2026-11-07 に、最初の実行で京都3R の1番に、ピックアップの加点が誤って付いたことで分かった）。
- 比べるもの: 各レースの scored（馬番・点数・ランク・内訳）を pickup_scores.json と完全一致で比べる。
- 対象: 11/7 に採点した全19レース（試しの対象の福島4R・福島7Rを含む）。
出力: このフォルダの control_step1_20261107.json
"""
import ast
import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
WT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(WT))
logging.disable(logging.CRITICAL)

import pandas as pd  # noqa: E402
import race_pickup  # noqa: E402
import prev_inputs  # noqa: E402

assert Path(race_pickup.__file__).resolve().parent == WT, race_pickup.__file__
assert Path(prev_inputs.__file__).resolve().parent == WT, prev_inputs.__file__

DATE = "20261107"
OUT = ROOT / "output" / DATE


def main():
    pk = json.load(open(OUT / "pickup_scores.json", encoding="utf-8"))
    tri = pd.read_csv(OUT / "全場_3指数重複馬.csv", encoding="utf-8-sig")
    style = json.load(open(WT / "output" / "horse_style.json", encoding="utf-8"))
    runs = {}
    res, n_same, n_diff = {}, 0, 0
    for lab, r in sorted(pk["races"].items()):
        run_id = r["prev_inputs_run"]
        if run_id not in runs:
            runs[run_id] = json.load(open(OUT / "prev_inputs" / f"{run_id}.json", encoding="utf-8"))
        rec = runs[run_id]
        assert rec["commit"].startswith("7b65ee7"), rec["commit"]
        g = tri[(tri["開催場"] + tri["レース番号"]) == lab]
        triple = g.to_dict(orient="records")
        shutuba = {k: r.get(k) for k in ("position_nums", "top3_hits", "horse_id_map", "pop_map", "odds_map", "scratched_nums")}
        shutuba["map_issues"] = []
        pn = r["pickup_nums"]
        pn = set(ast.literal_eval(pn)) if isinstance(pn, str) and pn != "set()" else (set() if pn in (None, "set()") else set(pn))
        data_top = {"pickup_nums": {str(x) for x in pn}, "analysis_hits": r["analysis_hits"]}
        entries = rec["races"][lab]["horses"]
        active = race_pickup.active_horse_ids(shutuba)
        top1 = prev_inputs.race_top1(active, entries)
        prev_db = {hid: v for hid, e in entries.items() if (v := prev_inputs.prev_db_entry(e)) is not None}
        scored = race_pickup.score_horses(triple, shutuba, data_top, prev_db=prev_db, race_max_prev_idx=top1["race_max"],
                                          race_date=DATE, horse_style_db=style, race_dist=r.get("race_dist"),
                                          predicted_pace=r.get("predicted_pace"))
        same = json.loads(json.dumps(scored, ensure_ascii=False)) == r["scored"]
        n_same += same
        n_diff += not same
        res[lab] = {"same": same, "reproduced": scored if not same else None,
                    "saved": r["scored"] if not same else None,
                    "summary": [(h["馬番"], h["score"], [b["label"] for b in h["breakdown"]]) for h in scored]}
        print(lab, "一致" if same else "違う", [(h["馬番"], h["score"]) for h in scored])
    print("レース", len(res), "一致", n_same, "違う", n_diff)
    files = [OUT / "pickup_scores.json", OUT / "全場_3指数重複馬.csv", WT / "output" / "horse_style.json"] + \
        [OUT / "prev_inputs" / f"{k}.json" for k in runs]
    code = [WT / "race_pickup.py", WT / "prev_inputs.py", Path(__file__).resolve()]
    out = {"worktree_commit": (WT / ".git").read_text().strip() if (WT / ".git").is_file() else None,
           "races": res, "same": n_same, "diff": n_diff,
           "inputs_sha256": {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
           "code_sha256": {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in code}}
    (HERE / "control_step1_20261107.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
