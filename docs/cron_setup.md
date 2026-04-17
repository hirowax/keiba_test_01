# 自動実行（cron）セットアップ

## 概要

JRA開催前日の17:00に、翌日のレースデータを自動取得→スコアリング→GitHub Pagesにpushする仕組み。
失敗・成功をLINEに通知する。

---

## 構成

```
[pmset] 毎日16:55 Mac自動スリープ解除
    ↓
[cron] 毎日17:00 run_cron.sh 起動
    ↓
[run_cron.sh] 翌日がJRA開催日？
    ├─ Yes → run.sh 実行 → LINE通知（成功/失敗）
    └─ No  → スキップ（通知なし）
```

---

## ファイル一覧

| ファイル | 役割 |
|---|---|
| `run_cron.sh` | cronラッパー。カレンダー判定→run.sh実行→LINE通知 |
| `run.sh` | メイン処理（scraper→pickup→calibrate→export→push） |
| `jra_calendar_2026.json` | JRA公式ICSから取得した2026年全108開催日 |
| `.env` | LINE_TOKEN / LINE_USER_ID（gitignore済） |
| `cron.log` | 実行ログ（自動生成） |

---

## セットアップ手順

### 1. pmset（スリープ解除スケジュール）

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 16:55:00
```

確認：
```bash
pmset -g sched
# → wakepoweron at 16:55:00 every day
```

### 2. crontab

```bash
crontab -e
# 以下を追加：
0 17 * * * ~/Desktop/netkeiba/run_cron.sh >> ~/Desktop/netkeiba/cron.log 2>&1
```

確認：
```bash
crontab -l
```

### 3. LINE通知（Messaging API）

`.env` に以下を追加済み：
```
LINE_TOKEN=（チャネルアクセストークン長期）
LINE_USER_ID=（あなたのユーザーID、Uで始まる）
```

取得元：
- [LINE Developers](https://developers.line.biz/) → プロバイダー → Messaging APIチャネル
- チャネル基本設定 → あなたのユーザーID
- Messaging API設定 → チャネルアクセストークン（長期）を発行

### 4. JRAカレンダー

JRA公式サイトのICSカレンダーから取得：
```bash
# ダウンロード・パース（年末に翌年分を実行）
curl -sL -o /tmp/jrakaisai2027.zip "https://www.jra.go.jp/keiba/common/calendar/jrakaisai2027.zip"
unzip -o /tmp/jrakaisai2027.zip -d /tmp/jra_ics/
python3 -c "
import re, json
from datetime import datetime, timedelta

with open('/tmp/jra_ics/jrakaisai2027.ics') as f:
    text = f.read()

events = re.findall(r'DTSTART;VALUE=DATE:(\d{8})\nDTEND;VALUE=DATE:(\d{8})', text)
all_dates = set()
for start, end in events:
    d = datetime.strptime(start, '%Y%m%d')
    e = datetime.strptime(end, '%Y%m%d')
    while d < e:
        all_dates.add(d.strftime('%Y%m%d'))
        d += timedelta(days=1)

data = {'year': 2027, 'source': 'JRA公式ICSカレンダー', 'updated': '2026-12-XX', 'dates': sorted(all_dates)}
with open('jra_calendar_2027.json', 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f'{len(all_dates)}日')
"
```

---

## LINE通知パターン

| 状況 | 通知メッセージ |
|---|---|
| 成功 | `[netkeiba] 20260418 のデータ準備完了 ✓` |
| 失敗 | `[netkeiba] 20260418 の処理が失敗しました ✗` |
| カレンダーなし | `[netkeiba] カレンダーファイルなし: jra_calendar_2026.json` |
| 非開催日 | 通知なし（スキップ） |
| 何も来ない | cronが動いていない（Mac未起動 or crontab未設定） |

---

## 注意事項

- **電源ケーブル必須**: バッテリー駆動だと pmset が動作しない場合がある
- **蓋閉じOK**: クラムシェルモードでも pmset はスリープ解除する
- **cookies期限**: 数週間に1回 `python3 save_cookies.py` で手動ログインが必要
  - cookies切れ時はrun.shが失敗 → LINE通知で検知可能
- **年末作業**: 翌年の `jra_calendar_YYYY.json` を上記手順で取得する
- **ログ確認**: `cat ~/Desktop/netkeiba/cron.log`

---

## 設定変更・削除

```bash
# cron削除
crontab -r

# pmset削除
sudo pmset repeat cancel

# 現在の設定確認
crontab -l
pmset -g sched
```

---

## 導入日

2026-04-17
