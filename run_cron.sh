#!/bin/bash
# JRA開催前日17時に自動実行するcronラッパー
# crontab: 0 17 * * * ~/Desktop/netkeiba/run_cron.sh >> ~/Desktop/netkeiba/cron.log 2>&1
#
# 動作: 翌日がJRA開催日なら run.sh を実行、そうでなければスキップ
# 失敗時はLINEに通知

set -e
cd "$(dirname "$0")"

# .env 読み込み
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# LINE通知関数
notify_line() {
    local msg="$1"
    if [ -n "$LINE_TOKEN" ] && [ -n "$LINE_USER_ID" ]; then
        curl -s -X POST https://api.line.me/v2/bot/message/push \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $LINE_TOKEN" \
            -d "{\"to\": \"$LINE_USER_ID\", \"messages\": [{\"type\": \"text\", \"text\": \"$msg\"}]}" \
            > /dev/null 2>&1
    fi
}

# 翌日の日付 (macOS date)
TOMORROW=$(date -v+1d +%Y%m%d)
YEAR=$(date -v+1d +%Y)
CALENDAR="jra_calendar_${YEAR}.json"

echo "────────────────────────────────────"
echo "$(date '+%Y-%m-%d %H:%M:%S') cron実行開始"
echo "  翌日: $TOMORROW"

# カレンダーファイル確認
if [ ! -f "$CALENDAR" ]; then
    echo "  ✗ カレンダーファイルなし: $CALENDAR → スキップ"
    notify_line "[netkeiba] カレンダーファイルなし: $CALENDAR"
    exit 0
fi

# 翌日がJRA開催日か確認
if python3 -c "
import json, sys
with open('$CALENDAR') as f:
    dates = json.load(f)['dates']
sys.exit(0 if '$TOMORROW' in dates else 1)
"; then
    echo "  ✓ $TOMORROW はJRA開催日 → run.sh 実行"

    if AUTO_MODE=1 ./run.sh "$TOMORROW"; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') 完了"
        notify_line "[netkeiba] $TOMORROW のデータ準備完了 ✓"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') 失敗"
        notify_line "[netkeiba] $TOMORROW の処理が失敗しました ✗\ncron.logを確認してください"
    fi
else
    echo "  - $TOMORROW は非開催日 → スキップ"
fi
