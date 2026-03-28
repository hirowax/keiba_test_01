#!/bin/bash
# スクレイパーを実行してGitHubにデータをpushするスクリプト
# 使い方: ./run.sh [YYYYMMDD]

set -e
cd "$(dirname "$0")"

DATE=${1:-$(date +%Y%m%d)}
echo "▶ スクレイパー実行: $DATE"
python3 scraper.py "$DATE"

echo "▶ 注目馬ピックアップ実行: $DATE"
python3 run_pickup_all.py "$DATE"

echo "▶ GitHubにデータをpush中..."
git add output/ summary/
git commit -m "data: $DATE"
git push

echo "✅ 完了！Renderが自動更新されます（1〜2分後にスマホで確認できます）"
