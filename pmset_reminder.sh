#!/bin/bash
# 夏季開催前後のpmset変更リマインド
# crontab:
#   0 8 29 6 * ~/Desktop/netkeiba/pmset_reminder.sh summer >> ~/Desktop/netkeiba/cron.log 2>&1
#   0 8 28 9 * ~/Desktop/netkeiba/pmset_reminder.sh winter >> ~/Desktop/netkeiba/cron.log 2>&1

cd "$(dirname "$0")"

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

MODE="$1"

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

if [ "$MODE" = "summer" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') 夏季pmsetリマインド送信"
    notify_line "[netkeiba] 🌞 夏季開催（7月〜）が近づいています。
pmsetを18:29:55に変更してください:
sudo pmset repeat wakepoweron MTWRFSU 18:29:55"
elif [ "$MODE" = "winter" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') 夏季終了pmsetリマインド送信"
    notify_line "[netkeiba] 🍂 夏季開催終了（10月〜）です。
pmsetを16:59:55に戻してください:
sudo pmset repeat wakepoweron MTWRFSU 16:59:55"
else
    echo "usage: $0 summer|winter"
    exit 1
fi
