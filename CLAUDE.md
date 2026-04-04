# netkeiba タイム指数スクレイパー & Webアプリ

## プロジェクト概要

netkeiba のタイム指数（近走平均・当該距離・当該コース）を自動取得し、
3指数すべてでトップ5に入る馬をスコアリングしてスマホで確認できる静的Webアプリ。

- **データ取得**: Playwright でログイン → speed.html スクレイプ（ローカルMacのみ）
- **スコアリング**: 3指数重複馬 × 加点要素（下記）
- **公開**: GitHub push → GitHub Pages 自動更新（コールドスタートなし・完全無料）
- **URL**: `https://hirowax.github.io/keiba_test_01/`

---

## ファイル構成

```
netkeiba/
├── scraper.py            # タイム指数取得・Excel/CSV出力
├── race_pickup.py        # レース別スコアリング（shutuba + data_top スクレイプ）
├── run_pickup_all.py     # 全レース一括ピックアップ実行
├── scrape_results.py     # レース結果を包括的スクレイプ（当日夜〜翌日実行）
├── scrape_prev_data.py   # 馬別前走データ収集（horse_db.jsonキャッシュ使用）
├── rescore.py            # 既存pickup_scores.jsonを再スコアリング（スクレイプ不要）
├── calibrate_threshold.py # 期待値🔥閾値を過去データから自動キャリブレーション
├── export_json.py        # CSV/Excel → JSON変換 + dates.json生成（GitHub Pages用）
├── analyze_hypotheses.py # 仮説検証スクリプト（統計分析用）
├── index.html            # GitHub Pages メインページ（静的・パスワードゲート付き）
├── app.py                # 旧Flask Webアプリ（Render移行前・現在未使用）
├── save_cookies.py       # 初回ログイン・クッキー保存用
├── run.sh                # 一括実行スクリプト（スクレイプ→push）
├── templates/index.html  # 旧Flaskテンプレート（現在未使用）
├── Dockerfile            # 旧Koyeb/Render用（現在未使用）
├── requirements.txt      # 全依存パッケージ（ローカル用）
├── requirements-server.txt # 旧サーバー用（現在未使用）
├── Procfile              # 旧Render用（現在未使用）
├── .env                  # NETKEIBA_EMAIL / NETKEIBA_PASSWORD（gitignore済）
├── cookies.json          # ログインセッション（gitignore済）
├── output/
│   ├── YYYYMMDD.xlsx         # タイム指数（全場・全モード）
│   ├── YYYYMMDD/
│   │   ├── 全場_3指数重複馬.csv
│   │   ├── pickup_scores.json    # スコアリング結果（Webアプリが読む）
│   │   ├── triple.json           # 3指数重複馬（GitHub Pages用JSON）
│   │   ├── race_results.json     # 全馬着順・人気・馬体重等（scrape_results.py出力）
│   │   └── race_conditions.json  # 馬場・天気・距離・クラス等（scrape_results.py出力）
│   ├── horse_db.json         # 馬別前走データ グローバルキャッシュ（7日有効）
│   ├── dates.json            # 利用可能な日付一覧（GitHub Pages用）
│   └── threshold_config.json # 期待値🔥閾値設定（calibrate_threshold.pyが更新）
└── summary/
    ├── YYYYMMDD.xlsx     # サマリー（レース別トップ5・重複馬）
    └── YYYYMMDD.json     # 上記のJSON版（GitHub Pages用）
```

---

## 通常の使い方（毎週末）

### 1. 一括実行（推奨）

```bash
cd ~/Desktop/netkeiba
./run.sh 20260405        # 日付指定
./run.sh                 # 引数なしで今日の日付
```

内部処理：
1. `scraper.py` → タイム指数取得・Excel/CSV出力
2. `run_pickup_all.py` → 全レースのピックアップスコアリング
3. `calibrate_threshold.py` → 期待値🔥閾値を過去実績から自動更新
4. `export_json.py` → CSV/Excel を JSON に変換・dates.json 更新
5. `git push` → GitHub Pages 自動更新（1〜2分後）

### 2. 初回・クッキー切れ時のログイン

```bash
python3 save_cookies.py
```

ブラウザが開くので手動でログインして閉じる。

### 3. レース結果を保存（当日夜〜翌日）

```bash
python3 scrape_results.py 20260404
git add output/20260404/ && git commit -m "results: 20260404" && git push
```

保存先：
- `output/{date}/race_results.json` — 全馬の着順・人気・オッズ・馬体重・増減・騎手・上がり3F・コーナー通過順
- `output/{date}/race_conditions.json` — 馬場状態・天気・距離・芝/ダート・クラス・出走頭数

データが蓄積されると `calibrate_threshold.py` の精度向上・`analyze_hypotheses.py` での分析が可能になる。

### 4. 閾値を手動で再キャリブレーション

```bash
python3 calibrate_threshold.py
```

過去の pickup_scores.json + race_results.json を照合し、3着内率が TARGET_RATE(70%) 以上で
馬数が最大になる閾値を選んで threshold_config.json に保存する。run.sh では自動実行される。

### 5. 既存データを再スコアリング（コード変更後）

```bash
python3 rescore.py 20260404
git add output/ && git commit -m "rescore: 20260404" && git push
```

スクレイプ不要で pickup_scores.json を最新ロジックで再計算。

### 6. 前走データのみ収集

```bash
python3 scrape_prev_data.py 20260404
```

horse_db.json のキャッシュを使い、未取得の馬のみスクレイプ。

---

## スコアリングロジック（race_pickup.py）

3指数重複馬（A）に対して以下の加点：

| 項目 | 点数 | 内容 |
|------|------|------|
| 推定ポジション有利馬 | +1 | shutuba のAI展開図 4コーナー有利馬 |
| 各データ上位3頭 | +1/カテゴリー | shutuba の各データ上位3頭に登場した回数分 |
| データ分析ピックアップ | +2 | data_top のピックアップ3頭 |
| 出走馬分析 | +1/条件 | data_top の出走馬分析テーブル登場数 |
| 前走タイム指数90以上 | +2 | horse_db から取得 |
| 前走タイム指数70〜89 | +1 | horse_db から取得 |
| 巻き返し馬 | +2 | 前走1〜3番人気かつ4着以下（horse_db使用） |

最高合計: **9pt**

`scrape_shutuba()` は `pop_map: {馬番: 人気}` も取得し、各馬の `today_pop` フィールドに格納。

ランク：★★★(5pt以上) / ★★(3〜4pt) / ★(1〜2pt) / －(0pt)

---

## Webアプリの構成（index.html）

パスワードゲート付き（SHA-256ハッシュ照合・localStorage保存）。

- **最注目馬セクション**：ev_threshold以上の馬を常に最上部に表示 + 人気表示
- **タブ①「★重複馬」**：venue別・12R→1R順
  - 3指数重複馬テーブル（馬番・馬名・近走平均・当該距離・当該コース）
  - **単指数1位馬**（紫バッジ）：3指数重複外だが1指数で1位の馬
- **タブ②「レース別」**：各レースのトップ5（アコーディオン）
- **タブ③「注目馬PU」**：スコア付き馬一覧・フィルター機能
  - 期待値🔥マーク：馬名の横にインライン表示（ev_threshold 以上）
  - 穴馬バッジ（赤）：スコア3pt以上 × 5人気以下
  - 参考バッジ（灰）：1〜3R（3歳未勝利・荒れやすいレース）
  - 人気：`today_pop`フィールド（run_pickup_all.py 実行時に取得）
  - 馬番：馬名の直前に小バッジで表示

venue表示順：東京→中山→京都→阪神→中京→新潟→福島→函館→札幌→小倉

---

## 期待値🔥閾値（threshold_config.json）

- パス：`output/threshold_config.json`
- `ev_threshold`：期待値🔥マークを付ける最低スコア（現在 **7pt**）
- `calibrate_threshold.py` が run.sh 実行時に毎回自動更新
- ロジック：過去データで **3着内率70%以上**・サンプル5頭以上を満たす最低閾値を採用
- 実績（2026-04-03時点、2日分）：
  - 7pt以上：12頭 → 3着内9頭（75%） ← 現在の閾値
  - 5pt以上：27頭 → 3着内16頭（59%）

---

## horse_db.json キャッシュ

- パス：`output/horse_db.json`
- キー：horse_id（netkeiba の馬ID）
- 有効期限：7日（`HORSE_DB_STALE_DAYS`）
- 内容：`prev_date, prev_pop, prev_rank, prev_idx, prev_idx_m, prev_dist, scraped_at`
- git にコミットして蓄積する（初回は scrape_prev_data.py で全馬取得 約30分）

---

## アンチボット対策（human_sleep / human_browse）

`scraper.py` に定義、全スクレイパーで共通使用：

- `human_sleep(min, max)`：ランダム待機（3〜9秒、12%確率で追加2〜6秒）
- `human_browse(page, url)`：35%確率でランダムな中間ページを経由
- `_random_scroll(page)`：1〜3回のランダムスクロール
- ログイン時はトップページ経由・フォーム入力間隔もランダム

**IPブロックされた場合**：30〜60分待てば解除される。
ブロック確認：ブラウザで `race.netkeiba.com` を手動で開けるか確認。

---

## 環境構築（初回）

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

---

## デプロイ構成

- **リポジトリ**：GitHub（hirowax/keiba_test_01）※ public
- **ホスティング**：GitHub Pages（コールドスタートなし・完全無料）
- **URL**：`https://hirowax.github.io/keiba_test_01/`
- **自動デプロイ**：main ブランチへの push で自動更新（1〜2分）

### 静的化の対応表（旧Render+Flask → 現GitHub Pages）

| 旧APIエンドポイント | 現静的ファイル |
|---|---|
| `/api/data/<date>` | `output/{date}/triple.json` + `summary/{date}.json` |
| `/api/pickup_all/<date>` | `output/{date}/pickup_scores.json` |
| `/api/threshold_config` | `output/threshold_config.json` |
| 日付一覧（Jinja2） | `output/dates.json` |

---

## 注意事項

- `.env` と `cookies.json` は **gitignore 済み**（スクレイプはローカルのみ）
- `debug_*.png` も gitignore 済み
- netkeiba のスクレイピングは**利用規約上グレー**。個人利用・低頻度・ログイン済みの範囲で使用すること
- `run.sh` はターミナルで `cd ~/Desktop/netkeiba` してから実行すること
- リポジトリは public だが、パスワードゲートで一般閲覧を制限している
