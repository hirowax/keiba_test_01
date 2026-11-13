"""使った入力ファイルの一覧と sha256 を results/manifest_{before,after}.json に書く。照合の前後で実行し、取り直しで変わったファイルを分かるようにする。"""
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import OUT
import sys as _sys  # 2026-11-13〜: 結果の保存が不完全な日があれば止める
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from data_quality import stop_if_any_results_save_incomplete
stop_if_any_results_save_incomplete()
pats = ["*.xlsx", "*/race_results.json", "*/race_results_all.json", "*/payouts_jra.json", "*/prev_data.json"]
files = {}
for pat in pats:
    for f in sorted(OUT.glob(pat)):
        if pat == "*.xlsx" and not f.stem.isdigit(): continue
        e = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest(), "bytes": f.stat().st_size}
        if f.name == "race_results_all.json":
            j = json.loads(f.read_bytes()); e["complete"] = j.get("complete"); e["races"] = len(j.get("races", {}))
        files[str(f.relative_to(OUT.parent))] = e
name = sys.argv[1]
json.dump(files, open(Path(__file__).parent / f"results/manifest_{name}.json", "w"), ensure_ascii=False, indent=0)
print(name, len(files), "files")
