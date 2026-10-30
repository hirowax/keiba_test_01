# 環境構築・デプロイ詳細

CLAUDE.md から移動（2026-10-30）。

### 環境構築（初回）

```bash
cd ~/Desktop/netkeiba
pip3 install -r requirements.txt
playwright install chromium

# .env に認証情報を設定
echo "NETKEIBA_EMAIL=your@email.com" > .env
echo "NETKEIBA_PASSWORD=yourpassword" >> .env

# 初回ログイン
python3 save_cookies.py
```

### 静的化の対応表（旧Render+Flask → 現GitHub Pages）

| 旧APIエンドポイント | 現静的ファイル |
|---|---|
| `/api/data/<date>` | `output/{date}/triple.json` + `summary/{date}.json` |
| `/api/pickup_all/<date>` | `output/{date}/pickup_scores.json` |
| `/api/threshold_config` | `output/threshold_config.json` |
| 日付一覧（Jinja2） | `output/dates.json` |
