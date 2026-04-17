#!/bin/bash
# レース結果を当日18:30に自動取得してpushするcronラッパー
# crontab: 30 18 * * * ~/Desktop/netkeiba/run_results_cron.sh >> ~/Desktop/netkeiba/cron.log 2>&1
#
# 動作: 今日がJRA開催日なら scrape_results.py を実行してpush

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

TODAY=$(date +%Y%m%d)
YEAR=$(date +%Y)
CALENDAR="jra_calendar_${YEAR}.json"

echo "────────────────────────────────────"
echo "$(date '+%Y-%m-%d %H:%M:%S') results cron実行開始"
echo "  対象日: $TODAY"

# カレンダーファイル確認
if [ ! -f "$CALENDAR" ]; then
    echo "  ✗ カレンダーファイルなし: $CALENDAR → スキップ"
    exit 0
fi

# 今日がJRA開催日か確認
if python3 -c "
import json, sys
with open('$CALENDAR') as f:
    dates = json.load(f)['dates']
sys.exit(0 if '$TODAY' in dates else 1)
"; then
    echo "  ✓ $TODAY はJRA開催日 → scrape_results.py 実行"

    if python3 scrape_results.py "$TODAY"; then
        git add "output/${TODAY}/"
        git commit -m "results: ${TODAY}" || echo "  (変更なし)"
        git push

        echo "$(date '+%Y-%m-%d %H:%M:%S') 完了"
        notify_line "[netkeiba] $TODAY のレース結果を取得・公開しました ✓"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') 失敗"
        notify_line "[netkeiba] $TODAY のレース結果取得が失敗しました ✗\ncron.logを確認してください"
    fi
else
    echo "  - $TODAY は非開催日 → スキップ"
fi
