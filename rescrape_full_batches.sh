#!/bin/bash
# 全レースの結果の取り直しを、1回400レースまでに分けて4時間おきに続ける（IPブロック対策・2026-11-03）
# 全日が完了するか、取得が異常終了（連続失敗でブロックを疑う＝終了コード2）したら止まる
cd ~/Desktop/netkeiba
while true; do
  python3 rescrape_results_full.py --range 20250920 20261101 --max-races 400
  rc=$?
  left=$(python3 -c "
import json,re
from pathlib import Path
o=Path('output'); n=0
for f in o.glob('*.xlsx'):
    if re.fullmatch(r'\d{8}',f.stem) and '20250920'<=f.stem<='20261101':
        p=o/f.stem/'race_results_all.json'
        n+= not (p.exists() and json.load(open(p)).get('complete'))
print(n)")
  echo "$(date '+%Y-%m-%d %H:%M:%S') 1回分終了 終了コード=$rc 未完了の日=$left"
  if [ "$rc" -ne 0 ]; then echo "異常終了のため止める"; break; fi
  if [ "$left" -eq 0 ]; then echo "全日完了"; break; fi
  sleep 14400
done
