"""タスク3: 指数の表が少ない日の形を数える（表の数・表のあるレース数・行数・馬番の組・200以上の値の数・結果のレース数）。
引数に日付を並べる。行数は2段目の見出し行を含む。"""
import json, re, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from xlsx_blocks import load_day
from common import OUT
out = {}
for d in sys.argv[1:]:
    day = load_day(OUT / f"{d}.xlsx")
    tables = [df for modes in day.values() for df in modes.values()]
    sets = collections.Counter(tuple(sorted(int(x) for x in df["馬番"] if str(x).isdigit())) for df in tables)
    vals = [str(v) for df in tables for v in df["指数"] if str(v) not in ("", "-")]
    hidden = sum(1 for v in vals if re.fullmatch(r"-?\d+", v) and int(v) >= 200)
    ra = OUT / d / "race_results_all.json"
    n_res = len(json.load(open(ra))["races"]) if ra.exists() else None
    out[d] = {"表": len(tables), "表のあるレース": len(day), "結果のレース(race_results_all)": n_res,
              "行数の分布": dict(collections.Counter(len(df) for df in tables)),
              "馬番の組(多い順3つ)": [[list(k), v] for k, v in sets.most_common(3)],
              "値の数": len(vals), "200以上の値": hidden}
json.dump(out, open(Path(__file__).parent / "results/short_tables.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
