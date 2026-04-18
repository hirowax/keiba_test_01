#!/bin/bash
# JRA開催前日17時に自動実行するcronラッパー
# crontab: 0 17 * * * ~/Desktop/netkeiba/run_cron.sh >> ~/Desktop/netkeiba/cron.log 2>&1
#
# 動作: 翌日がJRA開催日なら run.sh を実行、そうでなければスキップ
# 失敗時はLINEに通知

cd "$(dirname "$0")"

# スリープ防止（cron実行中はスリープしない）
/usr/bin/caffeinate -s -w $$ &

# .env 読み込み
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# LINE通知関数（通知自体の失敗は無視）
notify_line() {
    local msg="$1"
    if [ -n "$LINE_TOKEN" ] && [ -n "$LINE_USER_ID" ]; then
        curl -s -X POST https://api.line.me/v2/bot/message/push \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $LINE_TOKEN" \
            -d "{\"to\": \"$LINE_USER_ID\", \"messages\": [{\"type\": \"text\", \"text\": \"$msg\"}]}" \
            > /dev/null 2>&1 || true
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
        echo "$(date '+%Y-%m-%d %H:%M:%S') 翌日分完了"

        # 当日がJRA開催日なら結果もスクレイプ（17時台ならレース終了後）
        TODAY=$(date +%Y%m%d)
        if python3 -c "
import json, sys
with open('$CALENDAR') as f:
    dates = json.load(f)['dates']
sys.exit(0 if '$TODAY' in dates else 1)
"; then
            echo "  ▶ 当日($TODAY)の結果スクレイプ実行"
            if python3 scrape_results.py "$TODAY"; then
                echo "  ✓ 結果取得完了"
                git add output/"$TODAY"/ --ignore-errors
                git commit -m "results: $TODAY" || true
                git push || true
            else
                echo "  ✗ 結果取得失敗"
            fi
        fi

        notify_line "[netkeiba] $TOMORROW のデータ準備完了 ✓"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') 失敗 (exit code: $?)"
        notify_line "[netkeiba] $TOMORROW の処理が失敗しました ✗ cron.logを確認してください"
    fi
else
    echo "  - $TOMORROW は非開催日 → スキップ"

    # 非開催前日でも、当日が開催日なら結果だけスクレイプ
    TODAY=$(date +%Y%m%d)
    if python3 -c "
import json, sys
with open('$CALENDAR') as f:
    dates = json.load(f)['dates']
sys.exit(0 if '$TODAY' in dates else 1)
"; then
        echo "  ▶ 当日($TODAY)の結果スクレイプ実行"
        if python3 scrape_results.py "$TODAY"; then
            echo "  ✓ 結果取得完了"
            git add output/"$TODAY"/ --ignore-errors
            git commit -m "results: $TODAY" && git push || true
            notify_line "[netkeiba] $TODAY の結果を保存しました ✓"
        else
            echo "  ✗ 結果取得失敗"
        fi
    fi
fi
