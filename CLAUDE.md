# netkeiba タイム指数スクレイパー & Webアプリ

## プロジェクト概要

netkeiba のタイム指数（近走平均・当該距離・当該コース）を自動取得し、
3指数すべてでトップ5に入る馬（注目馬）をスコアリングしてスマホで確認できるWebアプリ。

- **データ取得**: Playwright でログイン → speed.html スクレイプ
- **スコアリング**: 3指数重複馬 × 4つのボーナス要素（後述）
- **公開**: GitHub push → Render 自動デプロイ（スマホでどこでも閲覧可）

---

## ファイル構成

```
netkeiba/
├── scraper.py          # メインスクレイパー（タイム指数取得・Excel/CSV出力）
├── race_pickup.py      # レース別スコアリング（shutuba + data_top スクレイプ）
├── run_pickup_all.py   # 全レース一括ピックアップ実行
├── rescore.py          # 既存pickup_scores.jsonを再スコアリング（スクレイプ不要）
├── scrape_prev_data.py # 馬別前走データ収集（horse_db.jsonキャッシュ使用）
├── analyze_hypotheses.py # 仮説検証スクリプト（統計分析用）
├── app.py              # Flask Webアプリ（Render上で動作）
├── save_cookies.py     # 初回ログイン・クッキー保存用
├── run.sh              # 一括実行スクリプト（スクレイプ→push→デプロイ）
├── templates/index.html # Webアプリ フロントエンド（全UI）
├── requirements.txt
├── Procfile            # Render用（gunicorn app:app）
├── .env                # NETKEIBA_EMAIL / NETKEIBA_PASSWORD（gitignore済）
├── cookies.json        # ログインセッション（gitignore済）
├── output/
│   ├── YYYYMMDD.xlsx       # タイム指数（全場・全モード）
│   ├── YYYYMMDD/
│   │   ├── 全場_3指数重複馬.csv
│   │   ├── pickup_scores.json  # スコアリング結果（Webアプリが読む）
│   │   ├── race_results.json   # レース結果（着順・人気・horse_id）
│   │   └── prev_data.json      # 前走データ（per-date バックアップ）
│   └── horse_db.json       # 馬別前走データ グローバルキャッシュ（7日有効）
└── summary/
    └── YYYYMMDD.xlsx       # サマリー（レース別トップ5・重複馬）
```

---

## 通常の使い方（毎週末）

### 1. 一括実行（推奨）

```bash
cd ~/Desktop/netkeiba
./run.sh 20260329        # 日付指定
./run.sh                 # 引数なしで今日の日付
```

内部処理：
1. `scraper.py` → タイム指数取得・Excel/CSV出力
2. `run_pickup_all.py` → 全レースのピックアップスコアリング
3. `git push` → GitHub → Render 自動更新（1〜2分後）

### 2. 初回・クッキー切れ時のログイン

```bash
python3 save_cookies.py
```

ブラウザが開くので手動でログインして閉じる。

### 3. 既存データを再スコアリング（コード変更後）

```bash
python3 rescore.py 20260328
```

スクレイプ不要で pickup_scores.json を最新ロジックで再計算。その後 `git add output/ && git commit && git push`。

### 4. 前走データのみ収集

```bash
python3 scrape_prev_data.py 20260328
```

horse_db.json のキャッシュを使い、未取得の馬のみスクレイプ。

---

## スコアリングロジック（race_pickup.py）

3指数重複馬（A）に対して以下の加点：

| 項目 | 点数 | 内容 |
|------|------|------|
| 推定ポジション有利馬 | +3 | shutuba のAI展開図 4コーナー有利馬 |
| 各データ上位3頭 | +1/カテゴリー | shutuba の各データ上位3頭に登場した回数分 |
| データ分析ピックアップ | +2 | data_top のピックアップ3頭 |
| 出走馬分析 | +1/条件 | data_top の出走馬分析テーブル登場数 |
| 前走タイム指数90以上 | +2 | horse_db から取得 |
| 前走タイム指数70〜89 | +1 | horse_db から取得 |
| 巻き返し馬 | +2 | 前走1〜3番人気かつ4着以下（horse_db使用） |

`scrape_shutuba()` は `pop_map: {馬番: 人気}` も取得し、各馬の `today_pop` フィールドに格納。

ランク：★★★(5pt以上) / ★★(3〜4pt) / ★(1〜2pt) / －(0pt)

---

## Webアプリの構成（index.html）

- **最注目馬セクション**：★★★馬を常に最上部に表示。「軸向け」バッジ（馬連/ワイド軸向け）+ 人気表示
- **穴馬セクション**：スコア3pt以上 × 5人気以下の「期待値の高い穴馬」を最注目馬の直下に表示
- **タブ①「3指数重複馬」**：venue別・12R→1R順にアコーディオン表示
- **タブ②「サマリー」**：各レースのトップ5
- **タブ③「ピックアップ」**：スコア付き馬一覧・フィルター機能
  - ★★★馬：「軸向け」バッジ（青）
  - スコア3pt以上 × 5人気以下：「穴馬」バッジ（赤）+ 人気オレンジ表示
  - 1〜3R：「参考」バッジ（灰）※3歳未勝利・荒れやすいレース
  - 人気取得：`today_pop`フィールド（次回 run_pickup_all.py 実行時から表示）

venue表示順：東京→中山→京都→阪神→中京→新潟→福島→函館→札幌→小倉

---

## horse_db.json キャッシュ

- パス：`output/horse_db.json`
- キー：horse_id（netkeiba の馬ID）
- 有効期限：7日（`HORSE_DB_STALE_DAYS`）
- 内容：`prev_date, prev_pop, prev_rank, prev_idx, prev_idx_m, prev_dist, scraped_at`
- **gitにコミットする**（Renderに乗せる必要はないがローカルで蓄積）
- 初回は scrape_prev_data.py で全馬取得（約30分）、2回目以降は差分のみ

---

## アンチボット対策（human_sleep / human_browse）

`scraper.py` に定義、全スクレイパーで使用：

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

- **リポジトリ**：GitHub（hirowax/keiba_test_01）
- **ホスティング**：Render（無料プラン、Free tier）
- **自動デプロイ**：main ブランチへの push で自動更新（1〜2分）
- **データ更新方法**：output/ と summary/ を push するだけ（Render再デプロイ）

---

## 注意事項

- `.env` と `cookies.json` は **gitignore 済み**。Renderには置かない（スクレイプはローカルのみ）
- `debug_*.png` も gitignore 済み
- netkeiba のスクレイピングは**利用規約上グレー**。個人利用・低頻度・ログイン済みの範囲で使用すること
- JRA-VAN は公式データ配信サービスだが**Windows専用**（Mac非対応）
- `run.sh` はターミナルで `cd ~/Desktop/netkeiba` してから実行すること
- Render の無料プランはアクセスがないとスリープする（初回アクセスに数十秒かかる場合あり）
