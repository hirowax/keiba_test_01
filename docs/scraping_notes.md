# スクレイピングの注意（アンチボット・認証・公開タイミング）

CLAUDE.md から移動（2026-10-30）。

### アンチボット対策

#### ステルス設定（全スクレイパー共通）
- `--disable-blink-features=AutomationControlled`: Chromium の自動化フラグ無効化
- `navigator.webdriver = undefined`: headless 検出回避
- `locale=ja-JP`, `timezone_id=Asia/Tokyo`: 自然なブラウザ設定
- User-Agent: Chrome 124 に統一

#### 行動パターン（`scraper.py` に定義）
- `human_sleep(min, max)`：ランダム待機（3〜9秒、12%確率で追加2〜6秒）
- `human_browse(page, url)`：35%確率でランダムな中間ページを経由
- `_random_scroll(page)`：1〜3回のランダムスクロール

#### 認証・安全弁
- **自動ログイン廃止**: cookiesのみ使用。`login()` による再ログインはしない（プレミアムcookies上書き防止）
- **プリフライトチェック**: 起動時に1レースだけ type=rank を試行。プレミアムコンテンツにアクセスできなければ即停止
- **fallback 大量発動ガード**: 過半数のレースで fallback に落ちたら停止（cookiesの期限切れを検知）
- cookies切れ時: `python3 save_cookies.py` で手動ログイン（数週間に1回程度）

#### タイム指数の公開タイミング
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
