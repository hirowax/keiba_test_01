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

カレンダーファイル（`jra_calendar_YYYY.json`）が対象年に存在すれば使う。なければ netkeiba で直接確認。

```python
import json, os, glob
from pathlib import Path

# 最古の未取得データを確認
have = sorted(p.parent.name for p in Path('output').glob('*/pickup_scores.json') if p.parent.name.isdigit())
print('最古pickup:', have[0] if have else 'なし')

# 全カレンダーファイルを検索し、未取得日を列挙
for cal_file in sorted(glob.glob('jra_calendar_????.json')):
    with open(cal_file) as f:
        all_dates = sorted(json.load(f)['dates'])
    missing = [d for d in all_dates if not os.path.exists(f'output/{d}/pickup_scores.json')]
    if missing:
        print(f'{cal_file}: 未取得 {len(missing)}日 → 先頭: {missing[:5]}')
```

**カレンダーファイルがない年の開催日確認**（候補の土〜月を直接問い合わせ）:

```python
from playwright.sync_api import sync_playwright
from scraper import load_cookies, get_race_ids, load_env
load_env()
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(); load_cookies(ctx)
    page = ctx.new_page()
    for d in ['YYYYMMDD', 'YYYYMMDD', 'YYYYMMDD']:  # 候補の土・日・月を列挙
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
| 中4週以内（前走28日以内） | +1 | horse_db の prev_date から計算（v6で+2→+1: クリーン窓でlift不安定・旧根拠283.7%は汚染データ由来） |
| 逃げ馬（Sペース予測時のみ・2走以上実績） | +1 | horse_style.json のコーナー通過順履歴から推定。Sペース(スロー)のときのみ加点 |
| 巻き返し馬 | 0（廃止） | 前走1〜3番人気かつ4着以下（N=83勝率2%回収10%→廃止） |
| 前走好走 | +2 | 前走1〜6番人気かつ1〜3着（horse_db使用） |
| 同距離前走（±50m） | +1 | horse_db の prev_dist と当日距離を比較 |
| 3指数すべて1位 | +1 | 近走平均・当該距離・当該コースの順位がすべて1位（v6新設: N=225 3着内48.9%） |
| 距離延長>50m | -1 | 前走距離より50m超の延長（v6新設: N=150 3着内24.0%、horse_db使用） |
| 前走10着以下 | -1 | 前走大敗（v6新設: N=126 3着内20.6%、horse_db使用） |

最高合計: **12pt**（減点によりマイナスもあり得る）
※「前走指数HIGH/MID」と「前走指数レース内1位」は合算上限3pt（重複加点ガード）
※v6の根拠は `docs/factor_audit_202606.md`（再現性監査）参照

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

## 回収率改善プラン（docs/roi_improvement_plan.md）

的中率を保ちつつ回収率を上げるための計画。詳細・実装仕様・判定基準はすべて `docs/roi_improvement_plan.md` に記載。
**以下の指示を受けたら、必ず同ドキュメント（と `docs/anaba_research_log.md` の教訓）を読んでから着手すること。**

**Issue管理・スケジュール・担当割当は `docs/betting_workflow_issues.md` に一元化**（2026-07-18策定）。
担当モデル（Sonnet/Opus/Fable/人間）とエスカレーションルールも同ドキュメント参照。

| ユーザーの指示 | 内容 | 担当 | 前提条件 |
|---|---|---|---|
| 「Issue #1 やって」 | JRA公式払戻スクレイパー実装（scrape_jra_payouts.py） | Sonnet | なし（次のアクション） |
| 「Issue #2 やって」 | 3連複の事前登録検証（過去80日分） | Opus | Issue #1合格 |
| 「Issue #3 やって」 | ペーパートレード台帳（paper_trade.py） | Sonnet | Issue #1合格 |
| 「Issue #N レビューして」 | featureブランチを受入基準でレビュー→合否判定 | Fable | 該当Issueの実装完了 |
| 「月次レポートやって」 | ペーパートレード累積成績+90日到達予測の更新 | Fable | Issue #3合格後・毎月初 |
| 「Phase Bやって」 | スコア確率化 + EVフィルターのバックテスト | Fable | クリーンデータ90日以上（2027-01末頃） |
| 「Phase Cやって」 | 損益分岐オッズ表示のUI実装 | Sonnet | Phase Bが採用基準クリア |

- Phase A（オッズ保存）は**完了**（実装2026-07-06・確認2026-07-18合格・オッズ取得率100%）。
  `pickup_scores.json` に `odds_map`（レース単位）と `today_odds`（scored各馬）が保存される（2026-07-11以降）
- 実銭での馬券購入は常に人間が行う（AIは判断材料の提示まで）
- 事前登録した閾値・判定基準をデータを見た後に動かすのは禁止（多重検定回避）

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

**ログ判読ルール**: `scraper.py`/`run.sh` 実行時、早い番組（1R/2R台等）で
「テーブル待機タイムアウト」「◯◯: データなし」「type=rank 未公開 → shutuba fallback」の
WARNINGが出るのは大半が新馬戦。新馬戦は**タイム指数そのものが存在しない**ため
（「未公開」ではなく仕様上スクレイプ不要）、取得を試みても必ずタイムアウト・データなしになる。
cookies切れ等の異常ではないので、同時にプリフライトチェックが通過していれば無視してよい。
3指数重複馬がスコアリング対象なので、新馬戦は自然に除外される。

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
- **git push について**: このプロジェクトの通常データ更新フロー（`run.sh`／`scrape_results.py`実行後のcommit、cronによる自動実行）では、commit後に**都度確認せず自動でpushする**。グローバル設定の「pushは明示指示があるまで待つ」は、破壊的操作やこのフロー以外のpush（コード変更・force push等）にのみ適用する。

---

## 血統辞典検証（gushiken_umapro note記事）

出典: https://note.com/gushiken_umapro/n/ncd90f116cb3b

### 血統辞典の条件一覧

スクリーンショット（31枚）から読み取った「推奨血統×競馬場×距離」条件。
父(sire) または 母父(dam_sire) がこれに該当する馬を「血統推奨馬」として扱う。

#### 札幌
| 距離 | 芝/ダ | 推奨父系 | 推奨母父系 |
|------|-------|----------|------------|
| 1200m | 芝 | ロードカナロア、キズナ | ディープインパクト、ハーツクライ |
| 1500m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 1800m | 芝 | キズナ、エピファネイア | ディープインパクト、ハーツクライ |
| 2000m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2600m | 芝 | キズナ | ディープインパクト |
| 1000m | ダ | ヘニーヒューズ、サウスヴィグラス | キングカメハメハ |
| 1700m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ、ゴールドアリュール |

#### 函館
| 距離 | 芝/ダ | 推奨父系 | 推奨母父系 |
|------|-------|----------|------------|
| 1200m | 芝 | ロードカナロア、ビッグアーサー | ディープインパクト |
| 1800m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2000m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2600m | 芝 | キズナ | ディープインパクト |
| 1000m | ダ | ヘニーヒューズ、サウスヴィグラス | キングカメハメハ |
| 1700m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |

#### 福島
| 距離 | 芝/ダ | 推奨父系 | 推奨母父系 |
|------|-------|----------|------------|
| 1200m | 芝 | ロードカナロア、ダイワメジャー | ディープインパクト |
| 1800m | 芝 | エピファネイア、キズナ | ディープインパクト、ハーツクライ |
| 2000m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 1150m | ダ | ヘニーヒューズ、サウスヴィグラス | キングカメハメハ |
| 1700m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |

#### 新潟
| 距離 | 芝/ダ | 推奨父系 | 推奨母父系 |
|------|-------|----------|------------|
| 1000m | 芝 | ロードカナロア | ディープインパクト |
| 1200m | 芝 | ロードカナロア、ビッグアーサー | ディープインパクト |
| 1400m | 芝 | ロードカナロア、キズナ | ディープインパクト |
| 1600m | 芝 | キズナ、エピファネイア | ディープインパクト、ハーツクライ |
| 1800m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2000m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2200m | 芝 | キズナ | ディープインパクト |
| 1200m | ダ | ヘニーヒューズ、サウスヴィグラス | キングカメハメハ |
| 1800m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |

#### 東京
| 距離 | 芝/ダ | 推奨父系 | 推奨母父系 |
|------|-------|----------|------------|
| 1400m | 芝 | ロードカナロア、ダイワメジャー | ディープインパクト |
| 1600m | 芝 | エピファネイア、キズナ | ディープインパクト、ハーツクライ |
| 1800m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2000m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2400m | 芝 | キズナ、エピファネイア | ディープインパクト、ハーツクライ |
| 2500m | 芝 | キズナ | ディープインパクト |
| 1300m | ダ | ヘニーヒューズ、サウスヴィグラス | キングカメハメハ |
| 1400m | ダ | ヘニーヒューズ | キングカメハメハ |
| 1600m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |
| 2100m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ、ゴールドアリュール |

#### 中山
| 距離 | 芝/ダ | 推奨父系 | 推奨母父系 |
|------|-------|----------|------------|
| 1200m | 芝 | ロードカナロア、ダイワメジャー | ディープインパクト |
| 1600m | 芝 | エピファネイア、キズナ | ディープインパクト、ハーツクライ |
| 1800m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2000m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2200m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2500m | 芝 | キズナ | ディープインパクト |
| 1200m | ダ | ヘニーヒューズ、サウスヴィグラス | キングカメハメハ |
| 1800m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |

#### 京都
| 距離 | 芝/ダ | 推奨父系 | 推奨母父系 |
|------|-------|----------|------------|
| 1200m | 芝 | ロードカナロア、ビッグアーサー | ディープインパクト |
| 1400m | 芝 | ロードカナロア、キズナ | ディープインパクト |
| 1600m | 芝 | エピファネイア、キズナ | ディープインパクト、ハーツクライ |
| 1800m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2000m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2200m | 芝 | キズナ | ディープインパクト |
| 3000m | 芝 | キズナ | ディープインパクト |
| 3200m | 芝 | キズナ | ディープインパクト |
| 1400m | ダ | ヘニーヒューズ、サウスヴィグラス | キングカメハメハ |
| 1800m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |
| 1900m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |

#### 阪神
| 距離 | 芝/ダ | 推奨父系 | 推奨母父系 |
|------|-------|----------|------------|
| 1200m | 芝 | ロードカナロア、ダイワメジャー | ディープインパクト |
| 1400m | 芝 | ロードカナロア、キズナ | ディープインパクト |
| 1600m | 芝 | エピファネイア、キズナ | ディープインパクト、ハーツクライ |
| 1800m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2000m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2200m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2400m | 芝 | キズナ | ディープインパクト |
| 1200m | ダ | ヘニーヒューズ、サウスヴィグラス | キングカメハメハ |
| 1400m | ダ | ヘニーヒューズ | キングカメハメハ |
| 1800m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |
| 2000m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ、ゴールドアリュール |

#### 中京
| 距離 | 芝/ダ | 推奨父系 | 推奨母父系 |
|------|-------|----------|------------|
| 1200m | 芝 | ロードカナロア、ビッグアーサー | ディープインパクト |
| 1400m | 芝 | ロードカナロア、キズナ | ディープインパクト |
| 1600m | 芝 | エピファネイア、キズナ | ディープインパクト、ハーツクライ |
| 2000m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 1400m | ダ | ヘニーヒューズ、サウスヴィグラス | キングカメハメハ |
| 1800m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |
| 1900m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |

#### 小倉
| 距離 | 芝/ダ | 推奨父系 | 推奨母父系 |
|------|-------|----------|------------|
| 1200m | 芝 | ロードカナロア、ダイワメジャー | ディープインパクト |
| 1800m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2000m | 芝 | キズナ、エピファネイア | ディープインパクト |
| 2600m | 芝 | キズナ | ディープインパクト |
| 1000m | ダ | ヘニーヒューズ、サウスヴィグラス | キングカメハメハ |
| 1700m | ダ | ヘニーヒューズ、パイロ | キングカメハメハ |

### 血統データ取得計画（プランA / プランB）

#### プランB（実装済み・2026-06-20〜）
`scrape_results.py` を改修し、レース結果取得時に各馬の `sire`（父）・`dam_sire`（母父）を同時取得するようにした。

- 取得元: `db.netkeiba.com/horse/{horse_id}/` の血統表（blood_table）
  - パース構造: 父=最初の `rowspan=4` td、母父=母（2番目の `rowspan=4`）と同じ `<tr>` 内の最初の `rowspan=2` td（3世代血統表の標準構造）
- キャッシュ: `output/horse_pedigree.json`（永続・馬IDをキーに永続保存）
- 保存先: `output/{date}/race_results.json` の各馬に `sire`/`dam_sire` フィールドを追加
- 初回実行時のみ多数の馬ページをスクレイプするため時間がかかる（出走全頭・1日約250頭で +20〜45分）
- 2回目以降はキャッシュが効くためほぼ追加時間なし
- **安全機構**:
  - 血統取得20頭ごとに `horse_pedigree.json` を定期保存（途中中断しても取得済みを失わない）
  - 血統取得が10連続失敗したら中断（IPブロック等を疑う・取得済みは保存される）

**運用上の注意:**
- `horse_pedigree.json` は `output/` 直下にあり、結果cron（`run_results_cron.sh`）は `output/{date}/` しか `git add` しないため**自動コミットされない**。ローカルディスクには残るためキャッシュは毎回効く（フル再取得にはならない）。`horse_db.json` 同様に git バックアップしたい場合のみ手動で `git add output/horse_pedigree.json` する。
- **初回検証**: 初回実行のログで `sire`/`dam_sire` が実際に埋まるか確認すること（blood_table のHTML構造はライブでの検証が必要。`血統表が見つかりません` の警告が多発する場合はパース構造を見直す）。
- 初回の結果スクレイプは大幅に長くなるため、稼働の空き時間に手動で `python3 scrape_results.py YYYYMMDD` を先行実行してキャッシュを seed しておくとcronが詰まりにくい。

#### プランA（「Aやって」と言われたら実行）
過去のレース結果データ（race_results.json）に遡って血統情報を付与し、血統辞典条件との照合で回収率・的中率を集計する。

**実行手順（「Aやって」と言われたら）：**
1. `output/*/race_results.json` の horse_id を全件収集（推定 5,000〜8,000頭）
2. `horse_pedigree.json` にないものだけ `db.netkeiba.com/horse/{horse_id}/` をスクレイプ
3. 血統辞典条件（競馬場 × 距離 × 父 or 母父）に合致する馬を「血統推奨馬」として抽出
4. race_conditions.json と race_results.json を突き合わせて着順・オッズを取得
5. 以下を出力:
   - 血統推奨馬の単勝回収率・複勝回収率・3着内率
   - 競馬場別・距離別の内訳
   - サンプル数とともに表形式で出力

**注意**: スクレイプは1日1週末分まで（ブロック懸念）。大量取得の場合は複数日に分割。
