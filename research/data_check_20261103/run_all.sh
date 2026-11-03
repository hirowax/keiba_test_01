#!/bin/bash
# リポジトリの根から実行する: bash research/data_check_20261103/run_all.sh
set -e
D=research/data_check_20261103
python3 $D/manifest.py before
python3 $D/check_index_vs_results.py > $D/results/index_vs_results.txt
python3 $D/short_tables.py 20251102 20260110 20260111 20260117 20260118 20260124 > $D/results/short_tables.txt
python3 $D/tansho_check.py > $D/results/tansho.txt
python3 $D/prev_pop_check.py > $D/results/prev_pop.txt
python3 $D/prev_pop_groups.py > $D/results/prev_pop_groups.txt
python3 $D/payout_combo_check.py > $D/results/payout_combo.txt
python3 $D/manifest.py after
python3 - <<'PY'
import json
d='research/data_check_20261103/results/'
b=json.load(open(d+'manifest_before.json')); a=json.load(open(d+'manifest_after.json'))
ch=[k for k in set(a)|set(b) if a.get(k,{}).get('sha256')!=b.get(k,{}).get('sha256')]
json.dump(sorted(ch),open(d+'changed_during_run.json','w'),ensure_ascii=False,indent=0)
print('実行中に変わった入力ファイル', sorted(ch))
PY
