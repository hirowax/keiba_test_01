"""残りのタスク13: 10/31・11/1 の race_results.json に、コーナー通過順だけを race_results_all.json から書き写す。
2026-11-03 決定（netkeiba にアクセスしない）。2026-11-05 実行。

照合（すべて満たす馬だけ書き写す。1つでも違えば書き写さず、理由別に数える）:
- レース: pickup_scores.json のレースID と race_results_all.json の同じレース名のレースID が一致
- 馬: race_results_all.json の同じレースに、同じ馬番の完走馬がちょうど1頭いて、馬ID・馬名（空白の違いだけ許す）・着順が一致
- race_results.json 側の通過順が空であること（空でなく値が違えば、上書きせず「食い違い」として数える）
書き換えるのは各馬の "corners" だけ。ほかの値は変えない。

usage: cd ~/Desktop/netkeiba && python3 research/corners_copy_20261105/copy_corners.py [--write]
       --write なしは数えるだけ。結果は同じフォルダの result.json に保存する。
"""
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DATES = ["20261031", "20261101"]
WRITE = "--write" in sys.argv


def norm(s):
    return re.sub(r"\s+", "", s or "")


def main():
    report = {"write": WRITE, "dates": {}}
    for d in DATES:
        out = BASE / "output" / d
        rr = json.loads((out / "race_results.json").read_text(encoding="utf-8"))
        ra = json.loads((out / "race_results_all.json").read_text(encoding="utf-8"))
        ps = json.loads((out / "pickup_scores.json").read_text(encoding="utf-8"))
        c = Counter()
        skipped = []
        for label, horses in rr.items():
            pid = (ps.get("races", {}).get(label) or {}).get("race_id")
            race = ra["races"].get(label)
            if race is None or not pid or race.get("race_id") != pid:
                c["レースを照合できない"] += len(horses)
                skipped.append({"race": label, "reason": "レースID", "pickup": pid, "all": race and race.get("race_id")})
                continue
            for h in horses:
                cand = [x for x in race["horses"] if str(x.get("num")) == str(h.get("num"))]
                if len(cand) != 1:
                    c["同じ馬番の馬が1頭でない"] += 1
                    skipped.append({"race": label, "num": h.get("num"), "reason": f"候補{len(cand)}頭"})
                    continue
                x = cand[0]
                diff = [k for k, a, b in (("horse_id", h.get("horse_id"), x.get("horse_id")),
                                          ("name", norm(h.get("name")), norm(x.get("name"))),
                                          ("rank", h.get("rank"), x.get("rank"))) if a != b]
                if diff:
                    c["馬が一致しない"] += 1
                    skipped.append({"race": label, "num": h.get("num"), "reason": "不一致: " + ",".join(diff)})
                    continue
                new = x.get("corners") or []
                old = h.get("corners") or []
                if not new:
                    c["取り直しにも通過順が無い"] += 1
                elif old and old != new:
                    c["既に別の通過順がある（上書きしない）"] += 1
                    skipped.append({"race": label, "num": h.get("num"), "reason": "食い違い", "old": old, "new": new})
                elif old == new:
                    c["既に同じ"] += 1
                else:
                    h["corners"] = new
                    c["書き写した"] += 1
        if WRITE and c["書き写した"]:
            path = out / "race_results.json"
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(rr, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        report["dates"][d] = {"races": len(rr), "horses": sum(len(v) for v in rr.values()),
                              "counts": dict(c), "skipped": skipped}
        print(d, f"{len(rr)}レース", dict(c), "書き写さなかった例:", skipped[:5])
    (Path(__file__).parent / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
