#!/bin/bash
# レース結果を当日18:30(夏19:30)に自動取得してpushするcronラッパー
# crontab:
#   30 18 * 1-6,10-12 * ~/Desktop/netkeiba/run_results_cron.sh >> ~/Desktop/netkeiba/cron.log 2>&1
#   30 19 * 7-9 *       ~/Desktop/netkeiba/run_results_cron.sh >> ~/Desktop/netkeiba/cron.log 2>&1
#
# 動作: 今日がJRA開催日なら scrape_results.py を実行してpush

cd "$(dirname "$0")"

# スリープ防止
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

TODAY=$(date +%Y%m%d)
YEAR=$(date +%Y)
CALENDAR="jra_calendar_${YEAR}.json"

echo "────────────────────────────────────"
echo "$(date '+%Y-%m-%d %H:%M:%S') results cron実行開始"
echo "  対象日: $TODAY"

# カレンダーファイル確認
if [ ! -f "$CALENDAR" ]; then
    echo "  ✗ カレンダーファイルなし: $CALENDAR → スキップ"
    notify_line "[netkeiba] カレンダーファイルなし: $CALENDAR"
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

    # scrape_results.py 実行
    if ! python3 scrape_results.py "$TODAY"; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') scrape_results.py 失敗"
        notify_line "[netkeiba] $TODAY のレース結果取得が失敗しました ✗ cron.logを確認してください"
        exit 1
    fi

    # git commit & push（各ステップの失敗を個別ハンドリング）
    git add "output/${TODAY}/"
    git commit -m "results: ${TODAY}" || echo "  (変更なし — コミットスキップ)"

    if git push; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') 完了"
        notify_line "[netkeiba] $TODAY のレース結果を取得・公開しました ✓"
    else
        echo "$(date '+%Y-%m-%d %H:%M:%S') git push 失敗"
        notify_line "[netkeiba] $TODAY の結果は取得済みですがpushに失敗しました ✗ 手動で git push してください"
    fi
else
    echo "  - $TODAY は非開催日 → スキップ"
fi

# ── カレンダー照合: 直近14日のレース結果欠損を1日分補完 ──────────────────
MISSING_RESULTS=$(python3 -c "
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
    if cutoff <= d < today_str and not Path(f'output/{d}/race_results.json').exists():
        print(d)
        break
" 2>/dev/null)

if [ -n "$MISSING_RESULTS" ]; then
    echo "────────────────────────────────────"
    echo "$(date '+%Y-%m-%d %H:%M:%S') レース結果欠損補完: $MISSING_RESULTS"
    if python3 scrape_results.py "$MISSING_RESULTS"; then
        git add "output/${MISSING_RESULTS}/"
        git commit -m "results: ${MISSING_RESULTS} (catchup)" || true
        git push || true
        notify_line "[netkeiba] 過去結果補完完了: $MISSING_RESULTS ✓"
    else
        echo "  補完失敗: $MISSING_RESULTS"
        notify_line "[netkeiba] 過去結果補完失敗: $MISSING_RESULTS ✗"
    fi
fi
