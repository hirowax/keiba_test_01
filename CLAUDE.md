# netkeiba タイム指数スクレイパー & Webアプリ

## プロジェクト概要

netkeiba のタイム指数（近走平均・当該距離・当該コース）を自動取得し、
3指数すべてでトップ3に入る馬をスコアリングしてスマホで確認できる静的Webアプリ。

- **データ取得**: Playwright でログイン → speed.html スクレイプ（ローカルMacのみ）
- **スコアリング**: 3指数重複馬 × 加点要素（下記）
- **公開**: GitHub push → GitHub Pages 自動更新（コールドスタートなし・完全無料）
- **URL**: `https://hirowax.github.io/keiba_test_01/`

---

## データ整合性ルール（2026-10-30 の取得ミス2件を受けて・最優先で守る）

2026-10-30 に「枠番を馬番として読んでいた」「指数の隠し値（1000＋指数）を指数として読んでいた」の2件が見つかった。
どちらも実ページを確かめずに書いた取り方が原因。経緯・影響は `docs/incident_umaban_20261030.md`。以下は恒久ルール。

- **要素は明示して取る**: スクレイプはクラス名等で対象セルを特定する。「最初の数字セル」「何列目」等の位置推測や、並び替え用の隠し値は使わない。画面に見えている値を読む。見つからなければそのレース/行を失敗にする。
- **値の範囲を検査する**: 指数は200以上を異常（0・マイナスは正当な値。実データの最小は-720）、頭数はページの「N頭」と一致など。範囲は実データで決め、根拠を `docs/data_sources.md` に書く。異常は空の値にせず、取得異常として記録してそのモード・レースを使わない。
- **重複を黙って捨てない**: 「同じキーは初回のみ登録」は禁止。重複キー・重複IDは異常として検出・記録する。
- **取得率と正しさは別物**: 新しい取得項目・パーサー変更の合格条件は「件数」ではなく、正解データ（`race_results.json` の馬番・馬ID）との**全頭照合一致**。
- **推測で閉じない**: 充足率の低下・横ばい等の異常に推測で説明を付けた場合、その推測を検証するまでタスクを閉じない。
- **テスト必須**: `race_pickup.py` 等のパーサーを変更したら `python3 -m unittest discover -s tests` を通す。多頭数（枠番≠馬番）のケースを必ず含める。
- **過去データの品質**: `collector_version` が無い pickup_scores.json（修正前）の前走成績による加点・オッズ・人気は研究・検証に使わない。過去ファイルは上書きしない。
- **造語を使わない**: 報告・ドキュメント・データ値に自作の略語や区分名（例: c1/c2、A群/B群）を使わない。誰が見ても分かる言葉（例: 修正前/修正後）にする。必要ならユーザーと意味を揃えてから使う。
- **版を分ける**: スコア式（`SCORING_VERSION`）、馬番・馬IDの取り方（`race_pickup.COLLECTOR_VERSION`）、指数の読み方（`scraper.INDEX_READER_VERSION`）は別管理。取り方を変えたら該当する版の値を変える。
- **品質はレース単位で判定する**: pickup_scores.json の各レースの `quality`（馬番の版・指数の読み方の版・指数の取得経路）と `invalidated.json` を `data_quality.race_quality_issues()` で判定する。版が1つあるだけで使える扱いにしない。研究・閾値調整・精算はこの判定を通す。
- 取得している値ごとの取り方と確認状況は `docs/data_sources.md`。**値を新しく取る・取り方を変えたら必ず更新し、実ページか正解データで確認してから使う。**

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
├── data_quality.py       # レース単位の品質判定（馬番の版・指数の読み方・無効化）
├── tests/                # 回帰テスト（python3 -m unittest discover -s tests）
├── docs/data_sources.md  # 取得している値ごとの取り方・確認状況（取り方を変えたら更新）
├── rerun_failed_pickup.py # エラーレースのみ再ピックアップ・pickup_scores.jsonにマージ
├── rescrape_results_all.py # 全日付のrace_results.jsonを再スクレイプ（フィールド追加時等に使用）
├── run_history_batch.py  # 過去日付のXLSX+pickup_scores.json一括生成（完了済みはスキップ）
├── scrape_history.py     # 過去日付のレース結果を一括取得（pickup_scores.json不要）
├── scrape_jra_payouts.py # JRA公式(jra.go.jp)から払戻を取得（Issue #1）
├── paper_trade.py        # ペーパートレード台帳 generate/settle/report（Issue #3）
├── validate_trifecta_202608.py # 3連複の事前登録検証（Issue #2・紐定義の実装元）
├── analyze_anaba*.py / validate_anaba_*.py # 穴馬ファクター研究（2026-05に完全保留）
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
│   │   ├── race_results.json     # 全馬着順・人気・馬体重・父/母父等（scrape_results.py出力）
│   │   ├── race_conditions.json  # 馬場・天気・距離・クラス等（scrape_results.py出力）
│   │   ├── payouts_jra.json      # JRA公式払戻（scrape_jra_payouts.py出力）
│   │   └── paper_bets.json       # ペーパートレード買い目・精算結果（paper_trade.py出力）
│   ├── horse_db.json         # 馬別前走データ グローバルキャッシュ（28日有効）
│   ├── horse_pedigree.json   # 馬別血統（父・母父）永続キャッシュ（scrape_results.pyが収集）
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

> **注意**: `rescore.py` は過去のスコアを書き換えるが、既存の `paper_bets.json` は
> `paper_trade.py generate` が上書きしないため影響を受けない（前向き記録の不変性を保つ設計）。

### 5-2. ペーパートレードの精算（払戻取得後）

```bash
python3 scrape_jra_payouts.py --all   # 払戻を分割取得（1回3日分まで）
python3 paper_trade.py settle --all   # payouts_jra.json で精算
python3 paper_trade.py report         # メニュー別の累積成績
```

買い目生成（`generate`）は run.sh が自動実行するので手動操作は不要。
精算はcron未組み込みのため、月次レポート前に上記を手動実行する。

精算しないもの（2026-10-30〜。払戻0円・不的中として集計しない）:
- `output/{date}/invalidated.json` に載った無効レース（買い目ファイルは書き換えず、report に件数と理由を出す）
- 払戻データが無いレース（その日は `settled=False` のまま）
- 買い目の馬番が結果データに無いメニュー（取消・除外・競走中止・失格のどれか区別できない。返還の可能性あり）
report は `status: settled` のメニューだけを集計する。2026-10-30 より前に精算したファイルには status が無いので、`settle --all` をやり直すと集計に入る。

### 6. 前走データのみ収集

```bash
python3 scrape_prev_data.py 20260404
```

horse_db.json のキャッシュを使い、未取得の馬のみスクレイプ。

### 7. 過去開催のスクレイプ（未取得週末の遡及）

手順は `docs/runbook_backfill.md`（開催日は祝日で変わるので必ずカレンダー確認）。

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
| 中4週以内（前走28日以内） | +2 | horse_db の prev_date から計算 |
| 逃げ馬（Sペース予測時のみ・2走以上実績） | +1 | horse_style.json のコーナー通過順履歴から推定。Sペース(スロー)のときのみ加点 |
| 巻き返し馬 | 0（廃止） | 前走1〜3番人気かつ4着以下（N=83勝率2%回収10%→廃止） |
| 前走好走 | +2 | 前走1〜6番人気かつ1〜3着（horse_db使用） |
| 同距離前走（±50m） | +1 | horse_db の prev_dist と当日距離を比較 |

最高合計: 名目 **13pt**（出走馬分析は上限なしのため固定上限ではない。実データの最高は13pt）
※「前走指数HIGH/MID」と「前走指数レース内1位」は合算上限3pt（重複加点ガード）
※**現行は v5**。v6（3指数すべて1位+1・距離延長>50m -1・前走10着以下-1・中4週+2→+1）は
2026-10-23 に実装・64日再スコアまで行ったうえで差し戻した。経緯と前後比較は
`docs/ops_log_202610.md` と `docs/v6_rescore_compare_202610.txt` を参照。
根拠データは `docs/factor_audit_202606.md`、実装手順は `docs/v6_implementation_plan.md`。

スコアリングバージョン: `race_pickup.py` の `SCORING_VERSION` 定数で管理。pickup_scores.json の `scoring_version` フィールドに埋め込まれる。`rescore.py` 実行時も更新される。`analyze_roi.py` の【10】セクションでバージョン別比較が可能。

`scrape_shutuba()` は `parse_shutuba_rows()` で出馬表の各行から 馬番（`td.Umaban*`）・馬ID・単勝オッズ・人気を**同一行由来**で取得し、`today_pop`/`today_odds` に格納。対応検証（`map_issues`）でNGのレースはスコアリングしない。
データの取り方は pickup_scores.json の `collector_version` に記録（例: `修正後_20261030_馬番を正しく取得`）。**この項目が無いデータ＝修正前（〜2026-10-30）は枠番を馬番として読んでおり、前走成績・オッズ・人気が他の馬の値**（`docs/incident_umaban_20261030.md`）。
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
- `ev_threshold`：期待値🔥マークを付ける最低スコア（現在 **9pt**）
- `calibrate_threshold.py` が run.sh 実行時に毎回自動更新。**ただし修正後（collector_version あり）のレースのみ使い、照合馬数200頭未満なら更新しない**（2026-10-30〜。現行9ptは暫定値で有効性未確認）
- ロジック：過去データで **3着内率70%以上**・サンプル5頭以上を満たす最低閾値を採用
- ⚠️ 以下の実績値は修正前（枠番を馬番として読んでいた）のデータに基づく。修正後データで再評価が必要（`docs/incident_umaban_20261030.md`）
- 実績（2026-10-23時点・v5・クリーン窓63日 20260328〜20261018）：
  - 9pt以上：134頭 → 3着内79頭（59.0%） ← 現在の閾値
  - 8pt以上：204頭 → 3着内120頭（58.8%）
  - 7pt以上：319頭 → 3着内170頭（53.3%）
- 注意：クリーン窓限定の集計では**どの閾値も TARGET_RATE(70%) に到達しない**ため、
  候補中の最高値が採用される。旧記載の「8pt=75.0%」は汚染期間込み（2025-12〜2026-05）の
  数値でクリーン窓では再現しない。

---

## 回収率改善プラン（docs/roi_improvement_plan.md）

的中率を保ちつつ回収率を上げるための計画。詳細・実装仕様・判定基準はすべて `docs/roi_improvement_plan.md` に記載。
**以下の指示を受けたら、必ず同ドキュメント（と `docs/anaba_research_log.md` の教訓）を読んでから着手すること。**

**Issue管理・スケジュール・担当割当は `docs/betting_workflow_issues.md` に一元化**（2026-07-18策定）。
担当モデル（Sonnet/Opus/Fable/人間）とエスカレーションルールも同ドキュメント参照。

### 進行中・今後のタスク（2026-10-30時点）

| ユーザーの指示 | 内容 | 担当 | 前提条件 |
|---|---|---|---|
| — | 取得ミス2件の後始末（10/31結果との全頭照合、自動照合とLINE通知、要確認リスト消化、9/12以降の再採点） | — | **最優先**（一覧と順番は `docs/incident_umaban_20261030.md` 末尾「残りのタスク」） |
| 「払戻バックフィルやって」 | `scrape_jra_payouts.py --all` を分割実行（残り約14日分） | Sonnet | なし（**次のアクション**） |
| 「月次レポートやって」 | 払戻バックフィル→`paper_trade.py settle --all`→累積成績+90日到達予測 | Fable | 毎月初 |
| 「Phase Bやって」 | スコア確率化 + EVフィルターのバックテスト | Fable | クリーンデータ90日以上（2027-01末頃） |
| 「Phase Cやって」 | 損益分岐オッズ表示のUI実装 | Sonnet | Phase Bが採用基準クリア |
| 「Aやって」 | 血統辞典条件の過去データ照合（下記「プランA」） | — | 未着手 |
| 「Issue #N やって/レビューして」 | 新規Issueの実装・レビュー | 同ドキュメント参照 | — |

### 完了済み

| Issue | 内容 | 結果 |
|---|---|---|
| #1 | JRA公式払戻スクレイパー | ✅ 2026-08-08 レビュー合格 |
| #2 | 3連複の事前登録検証 | ✅ 2026-08-10 **3連複は基準未達→不採用で終了**（回収率64.4%/53.2%） |
| #3 | ペーパートレード台帳 `paper_trade.py` | ✅ 2026-09-10 マージ済み。**20260912から前向き記録を開始** |
| #4 | Phase A（オッズ保存） | ⚠️ 2026-07-18 合格としたが、2026-10-30 に**オッズは枠番キーで他の馬の値**と判明（件数しか見ていなかった） |

- Phase A により `pickup_scores.json` に `odds_map`（レース単位）と `today_odds`（scored各馬）が保存される（2026-07-11以降）
- ペーパートレードは `run.sh` 末尾で `paper_trade.py generate` が自動実行される。
  精算は払戻データ取得後に手動で `python3 paper_trade.py settle --all`（cron未組み込み）
- `PAPER_TRADE_START = 20260912` は事前登録値。**データを見た後に動かさない**
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

## スクレイピングの注意

詳細（ステルス設定・待機・認証の安全弁・指数の公開タイミング）は `docs/scraping_notes.md`。要点:
- cookies のみ使用（自動ログインしない）。切れたら `python3 save_cookies.py` を手動実行
- 早い番組の「データなし」「type=rank 未公開 → shutuba fallback」は大半が新馬戦・障害で正常（プリフライト通過なら無視可）
- IPブロックは24時間待てば解除

---

## 環境構築・デプロイ詳細

`docs/setup.md`

---

## デプロイ構成

- **リポジトリ**：GitHub（hirowax/keiba_test_01）※ public
- **ホスティング**：GitHub Pages（コールドスタートなし・完全無料）
- **URL**：`https://hirowax.github.io/keiba_test_01/`
- **自動デプロイ**：main ブランチへの push で自動更新（1〜2分）

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

## 血統辞典検証

条件一覧とプランA/Bは `docs/pedigree_dictionary.md`。「Aやって」と言われたらそれに従う。
