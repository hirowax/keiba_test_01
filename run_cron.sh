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

    AUTO_MODE=1 ./run.sh "$TOMORROW" 2>&1 | tee /tmp/netkeiba_run.log
    RUN_STATUS=${PIPESTATUS[0]}
    if [ $RUN_STATUS -eq 0 ]; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') 翌日分完了"
        notify_line "[netkeiba] $TOMORROW のデータ準備完了 ✓"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') 失敗 (exit code: $RUN_STATUS)"
        if grep -q "プレミアムコンテンツにアクセスできません" /tmp/netkeiba_run.log; then
            notify_line "[netkeiba] ❌ Cookie期限切れ: python3 save_cookies.py を実行後、AUTO_MODE=1 bash run.sh $TOMORROW を手動実行してください"
        else
            notify_line "[netkeiba] $TOMORROW の処理が失敗しました ✗ cron.logを確認してください"
        fi
    fi
else
    echo "  - $TOMORROW は非開催日 → スキップ"
fi

# ── カレンダー照合: 直近14日のピックアップ欠損を1日分補完 ──────────────────
MISSING_PICKUP=$(python3 -c "
import json, sys
from pathlib import Path
from datetime import datetime, timedelta
today = datetime.today()
cutoff = (today - timedelta(days=14)).strftime('%Y%m%d')
today_str = today.strftime('%Y%m%d')
years = {cutoff[:4], today.strftime('%Y')}
all_dates = []
for y in years:
    cf = Path(f'jra_calendar_{y}.json')
    if cf.exists():
        all_dates.extend(json.loads(cf.read_text())['dates'])
for d in sorted(set(all_dates)):
    if cutoff <= d < today_str and not Path(f'output/{d}/pickup_scores.json').exists():
        print(d)
        break
" 2>/dev/null)

if [ -n "$MISSING_PICKUP" ]; then
    echo "────────────────────────────────────"
    echo "$(date '+%Y-%m-%d %H:%M:%S') ピックアップ欠損補完: $MISSING_PICKUP"
    AUTO_MODE=1 ./run.sh "$MISSING_PICKUP" 2>&1 | tee /tmp/netkeiba_patch.log
    PATCH_STATUS=${PIPESTATUS[0]}
    if [ $PATCH_STATUS -eq 0 ]; then
        notify_line "[netkeiba] 過去データ補完完了: $MISSING_PICKUP ✓"
    else
        echo "  補完失敗: $MISSING_PICKUP"
        if grep -q "プレミアムコンテンツにアクセスできません" /tmp/netkeiba_patch.log; then
            notify_line "[netkeiba] ❌ Cookie期限切れ: python3 save_cookies.py を実行後、AUTO_MODE=1 bash run.sh $MISSING_PICKUP を手動実行してください"
        else
            notify_line "[netkeiba] 過去データ補完失敗: $MISSING_PICKUP ✗ cron.logを確認してください"
        fi
    fi
fi
