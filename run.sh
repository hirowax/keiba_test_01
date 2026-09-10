#!/bin/bash
# スクレイパーを実行してGitHubにデータをpushするスクリプト
# 使い方: ./run.sh [YYYYMMDD]

set -e
cd "$(dirname "$0")"

# ── 二重起動防止（IPブロック・cookie競合・git push競合対策） ──
LOCKFILE="/tmp/netkeiba_run.lock"
if [ -f "$LOCKFILE" ]; then
    LOCK_PID=$(cat "$LOCKFILE")
    if ps -p "$LOCK_PID" > /dev/null 2>&1; then
        echo "❌ 既に run.sh が実行中です (PID: $LOCK_PID)。1日ずつ順番に実行してください。"
        exit 1
    fi
fi
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

DATE=${1:-$(date +%Y%m%d)}

# ── 日付・曜日の確認 ──────────────────────────────────────────
TODAY_ACTUAL=$(date +%Y%m%d)
DOW=$(date +%u)  # 1=月 ... 6=土 7=日
DOW_NAMES=("" "月" "火" "水" "木" "金" "土" "日")
echo "▶ 本日の日付: ${TODAY_ACTUAL} (${DOW_NAMES[$DOW]}曜日)"
echo "▶ 処理対象日: $DATE"

# 土日以外の場合は警告（--auto フラグで対話スキップ）
if [ "$DOW" -ne 6 ] && [ "$DOW" -ne 7 ]; then
    echo "⚠️  今日は平日 (${DOW_NAMES[$DOW]}曜日) です。指数データが未公開の可能性があります。"
    if [ "$AUTO_MODE" = "1" ]; then
        echo "▶ 自動モード: 続行"
    else
        read -p "続行しますか？ [y/N]: " confirm
        if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
            echo "中止しました。"
            exit 1
        fi
    fi
fi

echo "▶ スクレイパー実行: $DATE"
python3 scraper.py "$DATE"

echo "▶ スクレイパー完了後3分間休憩（netkeiba IPブロック対策）..."
sleep 180

echo "▶ 注目馬ピックアップ実行: $DATE"
python3 run_pickup_all.py "$DATE"

echo "▶ 期待値閾値キャリブレーション実行..."
python3 calibrate_threshold.py

echo "▶ GitHub Pages用 JSON エクスポート..."
python3 export_json.py "$DATE"

echo "▶ ペーパートレード買い目生成..."
python3 paper_trade.py generate "$DATE" || echo "⚠️  paper_trade 失敗（本体データには影響なし・続行）"

echo "▶ GitHubにデータをpush中..."
git add output/ summary/ --ignore-errors
git commit -m "data: $DATE"
git push

echo "✅ 完了！GitHub Pagesが自動更新されます（1〜2分後にスマホで確認できます）"
echo ""
echo "📝 レース終了後（当日夜〜翌日）に以下を実行するとデータが蓄積されます:"
echo "   python3 scrape_results.py $DATE"
echo "   git add output/$DATE/ && git commit -m 'results: $DATE' && git push"
