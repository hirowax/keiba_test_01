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
├── build_horse_style.py  # race_results.jsonのコーナー通過順から脚質推定・horse_style.json更新
├── compare_scores.py     # 旧スコアvs新スコアの前後比較（コード変更の効果検証用）
├── rerun_failed_pickup.py # エラーレースのみ再ピックアップ・pickup_scores.jsonにマージ
├── rescrape_results_all.py # 全日付のrace_results.jsonを再スクレイプ（フィールド追加時等に使用）
├── run_history_batch.py  # 過去日付のXLSX+pickup_scores.json一括生成（完了済みはスキップ）
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
│   ├── horse_db.json         # 馬別前走データ グローバルキャッシュ（28日有効）
│   ├── horse_style.json      # 馬別脚質データ（build_horse_style.pyが出力・run_pickup_all.pyが参照）
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

### 7. 過去開催のスクレイプ（未取得週末の遡及）

「過去開催の1週末分スクレイプして」と言われたら以下の手順で実行する。

> **重要**: JRA開催は土日2日とは限らない。祝日がある週は土日月の3日開催、日月のみ、日のみ等がある。
> **必ずJRAカレンダーを参照して開催日を確認してから実行すること。**

#### ① 未取得週末を特定し、開催日を確認する

**2026年の場合**（jra_calendar_2026.json を使用）:

```python
import json, os, itertools
with open('jra_calendar_2026.json') as f:
    all_dates = sorted(json.load(f)['dates'])
missing = [d for d in all_dates if not os.path.exists(f'output/{d}/pickup_scores.json')]
# 最古の未取得日が属する「週」（連続開催日グループ）を特定
# 同一週末の日付は通常3日以内に連続している
print('未取得の開催日（先頭10件）:', missing[:10])
```

**2025年以前の場合**（カレンダーファイルなし）:

```bash
# 最古のpickupデータを確認
find output -name "pickup_scores.json" | sort | head -5
```

その直前の週のサタ〜月（4日分）について、以下で開催確認:

```python
from playwright.sync_api import sync_playwright
from scraper import load_cookies, get_race_ids, load_env
load_env()
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(); load_cookies(ctx)
    page = ctx.new_page()
    for d in ['20251025', '20251026', '20251027']:  # 候補日を列挙
        races = get_race_ids(page, d)
        print(d, len(races), '件')
    browser.close()
```

レース数 > 0 の日が開催日。それらが「1週末」のセット。

#### ② 開催日ごとに順番にスクレイプ（1日ずつ）

```bash
AUTO_MODE=1 bash run.sh YYYYMMDD  # 1日目（完了を待ってから2日目へ）
AUTO_MODE=1 bash run.sh YYYYMMDD  # 2日目
AUTO_MODE=1 bash run.sh YYYYMMDD  # 3日目（3日開催の場合のみ）
```

- **平日実行でも OK**（`AUTO_MODE=1` が警告をスキップ）
- 過去レース（約6ヶ月以上前）は `type=rank` がサブスク誘導ページを返す仕様だが、
  `shutuba fallback` が自動で使われるため正常動作する（scraper.py 修正済み 2026-05-21）
- run.sh は 1 日ずつ順番に実行する（同時実行不可）

#### ③ レース結果をスクレイプしてpush

```bash
python3 scrape_results.py YYYYMMDD  # 開催日ごとに実行
# 全日完了後まとめてpush
git add output/ && git commit -m "results: YYYYMMDD YYYYMMDD ..." && git push
```

---

## スコアリングロジック（race_pickup.py）

3指数重複馬（A）に対して以下の加点。選出基準は**3指数すべてトップ3**（旧：トップ5）：

| 項目 | 点数 | 内容 |
|------|------|------|
| 推定ポジション有利馬 | 0（廃止） | N=68 勝率11.8% 単勝回収率34.6% → 廃止 |
| 各データ上位3頭 | +1/カテゴリー（上限2pt） | shutuba の各データ上位3頭に登場した回数分 |
| データ分析ピックアップ | +1 | data_top のピックアップ3頭（旧+2→+1: 単体回収率66%、組合せ依存） |
| 出走馬分析 | +1/条件 | data_top の出走馬分析テーブル登場数 |
| 前走タイム指数90以上 | +2 | horse_db から取得 |
| 前走タイム指数70〜89 | +1 | horse_db から取得 |
| 前走指数レース内1位 | +2 | horse_db × 出走全馬比較（旧+1→+2: N=73勝率29%回収146%） |
| 中4週以内（前走28日以内） | +2 | horse_db の prev_date から計算（旧+1→+2: N=65 単勝回収率283.7%） |
| 逃げ馬（Sペース予測時のみ・2走以上実績） | +1 | horse_style.json のコーナー通過順履歴から推定。Sペース(スロー)のときのみ加点 |
| 巻き返し馬 | 0（廃止） | 前走1〜3番人気かつ4着以下（N=83勝率2%回収10%→廃止） |
| 前走好走 | +2 | 前走1〜6番人気かつ1〜3着（horse_db使用） |
| 同距離前走（±50m） | +1 | horse_db の prev_dist と当日距離を比較 |

最高合計: **12pt**
※「前走指数HIGH/MID」と「前走指数レース内1位」は合算上限3pt（重複加点ガード）

スコアリングバージョン: `race_pickup.py` の `SCORING_VERSION` 定数で管理。pickup_scores.json の `scoring_version` フィールドに埋め込まれる。`rescore.py` 実行時も更新される。`analyze_roi.py` の【10】セクションでバージョン別比較が可能。

`scrape_shutuba()` は `pop_map: {馬番: 人気}` も取得し、各馬の `today_pop` フィールドに格納。
人気取得: shutuba.html の `td.Ninki` → 取れない場合 speed.html (`rf=shutuba_submenu`) の `sk__ninki` から取得。

ランク：★★★(5pt以上) / ★★(3〜4pt) / ★(1〜2pt) / －(0pt)

---

## Webアプリの構成（index.html）

パスワードゲート付き（SHA-256ハッシュ照合・localStorage保存）。

- **最注目馬セクション**：ev_threshold以上の馬を最上部に表示
  - **次点セクション**：ev_threshold-2 〜 ev_threshold-1 の馬をグレーで小さく表示
- **開催場タブ**：中山/阪神/福島 など当日の会場ごとにタブ切替
  - 各レースをカード表示（12R→1R順）
  - カード内: スコア付き馬一覧（スコア降順）+ ファクター + 人気
  - 🔥バッジ: ev_threshold以上の馬
  - 参考バッジ（灰）：1〜3R（3歳未勝利・荒れやすいレース）
  - **単指数1位馬**（紫バッジ）：3指数重複外だが1指数で1位の馬

venue表示順：東京→中山→京都→阪神→中京→新潟→福島→函館→札幌→小倉

---

## 期待値🔥閾値（threshold_config.json）

- パス：`output/threshold_config.json`
- `ev_threshold`：期待値🔥マークを付ける最低スコア（現在 **8pt**）
- `calibrate_threshold.py` が run.sh 実行時に毎回自動更新
- ロジック：過去データで **3着内率70%以上**・サンプル5頭以上を満たす最低閾値を採用
- 実績（2026-05-04時点・v5・38日分 2025-12-13〜2026-05-03）：
  - 9pt以上：31頭 → 3着内26頭（83.9%）
  - 8pt以上：64頭 → 3着内48頭（75.0%） ← 現在の閾値
  - 7pt以上：115頭 → 3着内80頭（69.6%）

---

## horse_db.json キャッシュ

- パス：`output/horse_db.json`
- キー：horse_id（netkeiba の馬ID）
- 有効期限：28日（`HORSE_DB_STALE_DAYS`）: 2週目以降のリクエスト数を1/3以下に削減
- 内容：`prev_date, prev_pop, prev_rank, prev_idx, prev_idx_m, prev_dist, scraped_at`
- git にコミットして蓄積する（初回は scrape_prev_data.py で全馬取得 約30分）

---

## アンチボット対策

### ステルス設定（全スクレイパー共通）
- `--disable-blink-features=AutomationControlled`: Chromium の自動化フラグ無効化
- `navigator.webdriver = undefined`: headless 検出回避
- `locale=ja-JP`, `timezone_id=Asia/Tokyo`: 自然なブラウザ設定
- User-Agent: Chrome 124 に統一

### 行動パターン（`scraper.py` に定義）
- `human_sleep(min, max)`：ランダム待機（3〜9秒、12%確率で追加2〜6秒）
- `human_browse(page, url)`：35%確率でランダムな中間ページを経由
- `_random_scroll(page)`：1〜3回のランダムスクロール

### 認証・安全弁
- **自動ログイン廃止**: cookiesのみ使用。`login()` による再ログインはしない（プレミアムcookies上書き防止）
- **プリフライトチェック**: 起動時に1レースだけ type=rank を試行。プレミアムコンテンツにアクセスできなければ即停止
- **fallback 大量発動ガード**: 過半数のレースで fallback に落ちたら停止（cookiesの期限切れを検知）
- cookies切れ時: `python3 save_cookies.py` で手動ログイン（数週間に1回程度）

### タイム指数の公開タイミング
- **水曜20時**にその週末のレースの type=rank データが公開される
- 新馬・障害は対象外
- `speed.html?race_id=...&type=rank&mode={mode}` で全出走馬のランキングが閲覧可能

**IPブロックされた場合**：**24時間**待てば解除される。別IPに切り替えても可。
ブロック確認：ブラウザで `race.netkeiba.com` を手動で開けるか確認。
スクリプトはブロックを検知した時点で自動停止する（run_pickup_all.py）。

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

## 自動実行（cron）

詳細は `docs/cron_setup.md`。

- **pmset**: 毎日16:55にMac自動スリープ解除
- **crontab（データ取得）**: `0 17 * * * ~/Desktop/netkeiba/run_cron.sh >> ~/Desktop/netkeiba/cron.log 2>&1`
  - 翌日がJRA開催日なら `run.sh` を実行してデータ取得→push
- **crontab（結果取得）**:
  - `30 18 * 1-6,10-12 * ~/Desktop/netkeiba/run_results_cron.sh >> ~/Desktop/netkeiba/cron.log 2>&1`（通常期：18:30）
  - `30 19 * 7-9 *       ~/Desktop/netkeiba/run_results_cron.sh >> ~/Desktop/netkeiba/cron.log 2>&1`（夏期7〜9月：19:30）
  - 当日がJRA開催日なら `scrape_results.py` を実行して結果→push
  - `pickup_scores.json` がない場合でもレース一覧ページから race_id を取得してフォールバック
- **カレンダー**: `jra_calendar_YYYY.json`（JRA公式ICSから取得）
- **LINE通知**: 成功/失敗をMessaging APIでpush（`.env` に LINE_TOKEN / LINE_USER_ID）
- **年末作業**: 翌年の `jra_calendar_YYYY.json` を取得する
- **cookies期限切れ時**: cronが `❌ プレミアムコンテンツにアクセスできません` で失敗→手動で `python3 save_cookies.py` を実行してから再実行

---

## 注意事項

- `.env` と `cookies.json` は **gitignore 済み**（スクレイプはローカルのみ）
- `debug_*.png` も gitignore 済み
- netkeiba のスクレイピングは**利用規約上グレー**。個人利用・低頻度・ログイン済みの範囲で使用すること
- `run.sh` はターミナルで `cd ~/Desktop/netkeiba` してから実行すること
- リポジトリは public だが、パスワードゲートで一般閲覧を制限している
