# v6 実装指示書（データ汚染修正 + スコアリング改善）

**作成日**: 2026-06-11
**背景資料**: `docs/factor_audit_202606.md`（必読・本指示書の全変更の根拠）
**検証ツール**: `analyze_factor_audit.py`（再現性監査）, `compare_scores.py`（前後比較）

このドキュメントは別モデルが迷わず実装できる粒度で記述する。
**タスク1〜4（データ整合性）を先に完了させてから、タスク5（スコアリング v6）に着手すること。**
タスク間の依存: 1→2→3 は順序必須。4・6 は独立。5 は 1〜3 完了後。

---

## 前提知識（実装前に理解すること）

1. **汚染問題**: `scrape_horse_prev()`（scrape_prev_data.py:54）と `scrape_horse_prev_page()`（run_pickup_all.py:69）は
   馬ページ `db.netkeiba.com/horse/{id}/` の結果テーブル**最新行**（`data_rows[0]`）を取る。
   過去日付の遡及生成や後日の rescore では「対象レースより未来のレース」が前走になる。
2. **クリーン境界**: 2026-03-28 以降の pickup_scores.json はリアルタイム採点で正当。
   それより前の48日分は前走系ファクター（前走指数/前走好走/中4週/同距離）が汚染、
   data_top系（ピックアップ/出走馬分析）は全馬N=0。
3. **horse_db.json** は horse_id 単位の単一キャッシュで日付次元がない。
   per-date スナップショット `output/{date}/prev_data.json` は現状 20260328 のみ存在。
4. 結果テーブルの日付セル（cells[0]）の形式は `"2026/03/28"`。対象日は `"YYYYMMDD"` 形式。

---

## タスク1: 前走取得の date-aware 化

### 1-1. `run_pickup_all.py` の `scrape_horse_prev_page()`

シグネチャに `target_date: str` を追加し、`data_rows[0]` 固定をやめて
「対象日より前の最初の行」を選ぶ:

```python
def scrape_horse_prev_page(page, horse_id: str, target_date: str) -> dict:
    """db.netkeiba の馬ページから target_date より前の直近レースを取得"""
    ...（goto 〜 data_rows 取得までは現状のまま）...

    target_row = None
    for row in data_rows:
        cells_tmp = [c.get_text(strip=True) for c in row.find_all("td")]
        race_date_str = cells_tmp[0].replace("/", "")  # "2026/03/28" → "20260328"
        if len(race_date_str) == 8 and race_date_str.isdigit() and race_date_str < target_date:
            target_row = row
            break
    if target_row is None:
        return {}

    cells = [c.get_text(strip=True) for c in target_row.find_all("td")]
    ...（以降 safe() と return は現状のまま）...
```

呼び出し元（run_pickup_all.py:218 付近）:
```python
prev = scrape_horse_prev_page(page, hid, date)
```

### 1-2. `scrape_prev_data.py` の `scrape_horse_prev()`

同一の修正を適用（こちらも `data_rows[0]` 固定）。呼び出し元（同ファイル main 内、
145行付近 `scrape_horse_prev(page, hid)`）に `date` を渡す。

### 1-3. キャッシュ判定の強化（run_pickup_all.py / scrape_prev_data.py 共通）

`is_cache_fresh()` を通過したキャッシュでも、`prev_date >= 対象日` なら未来データなので使用不可。
run_pickup_all.py:213 付近の `in_cache` 判定に条件を追加:

```python
def _prev_is_valid(entry: dict, date: str) -> bool:
    pd_str = (entry.get("prev_date") or "").replace("/", "")
    return len(pd_str) == 8 and pd_str.isdigit() and pd_str < date

in_cache = (hid in horse_db
            and is_cache_fresh(horse_db[hid], date)
            and _prev_is_valid(horse_db[hid], date))
```

`_prev_is_valid` は run_pickup_all.py に追加し、scrape_prev_data.py の
キャッシュ判定（114行付近）にも同条件を入れる。

### 検証
```bash
python3 - <<'EOF'
# 任意の horse_id で過去日付を指定し、prev_date < 指定日 になることを確認
# （ヘッドレスブラウザ起動が必要。cookies.json 必須）
EOF
```
最低限、修正後に `python3 run_pickup_all.py <次の開催日>` の通常運用で
prev_date が常に対象日より前であることを確認（ログ出力で目視）。

---

## タスク2: per-date prev_data.json の毎回保存

run_pickup_all.py の `save_horse_db(horse_db)`（323行付近）の直後に、
当日使用した馬の prev エントリをスナップショット保存する:

```python
# per-date スナップショット（rescore.py が後日参照する。タスク3参照）
all_hids = set()
for rdata in results.values():
    if isinstance(rdata, dict):
        all_hids.update(v for v in rdata.get("horse_id_map", {}).values() if v)
snapshot = {hid: horse_db[hid] for hid in all_hids if hid in horse_db}
prev_data_path = BASE_DIR / "output" / date / "prev_data.json"
with open(prev_data_path, "w", encoding="utf-8") as f:
    json.dump(snapshot, f, ensure_ascii=False, indent=2)
logger.info(f"prev_data.json 保存: {len(snapshot)}頭")
```

※ `results` は races 辞書を保持している変数。実際の変数名はコードを確認して合わせること
（pickup_scores.json に書き込んでいる `existing["races"]` 相当）。
`rerun_failed_pickup.py` にも同様の保存処理を追加（既存 prev_data.json があれば**マージ**して上書き）。

---

## タスク3: rescore.py の汚染防止

グローバル horse_db.json の使用をやめ、per-date `prev_data.json` を使う:

1. `output/{date}/prev_data.json` が存在すればそれを `horse_db` として読み込む
2. 存在しない場合は**エラーで停止**:
   ```
   ❌ {date} に prev_data.json がありません。
      グローバル horse_db での再スコアは前走データ汚染を起こすため中止します。
      （強行する場合: --allow-global-db。クリーン境界 20260328 以降の直近日付のみ推奨）
   ```
3. `--allow-global-db` フラグ付きのときのみ従来動作（horse_db.json）を許可。
   その場合も各馬で `prev_date >= date` のエントリは prev_db から除外してから渡す
   （タスク1-3の `_prev_is_valid` を流用）。

※ 20260328 のみ prev_data.json が既存。それ以外の過去日付は原則 rescore 不可になるが、
   **これは意図した仕様**（汚染データの再生産を防ぐ）。

---

## タスク4: calibrate_threshold.py / analyze_roi.py のクリーン日付限定

両ファイルにモジュール定数を追加し、日付ループでフィルタする:

```python
CLEAN_START = "20260328"  # これより前は前走系ファクター汚染のため除外（docs/factor_audit_202606.md）
```

- `calibrate_threshold.py` の `load_pairs()`: `if date_dir.name < CLEAN_START: continue`
- `analyze_roi.py` の `load_all_data()`: 同様。ただし全期間を見たい場合のために
  `--all` フラグで解除できるようにする（argparse か `sys.argv` 判定の軽い実装でよい）

実行して閾値が変わるか確認:
```bash
python3 calibrate_threshold.py
```
クリーン窓のみだと 8pt 以上は N=90・3着内68.9%（監査時点）。TARGET_RATE=0.70 をわずかに
割るため閾値が 9pt に動く可能性がある。**閾値が変わった場合はその旨をユーザーに報告すること**
（threshold_config.json は run.sh でも自動更新されるため、ここでの変更自体は正常動作）。

---

## タスク5: スコアリング v6（race_pickup.py）

**根拠は全て docs/factor_audit_202606.md。タスク1〜3完了後に着手。**

### 5-1. 定数変更

```python
SCORING_VERSION = "v6"
# v6: 3指数すべて1位+1・距離延長>50m -1・前走10着以下-1・中4週+2→+1 (2026-06-11)

SCORE_RECENT_RACE   = 1   # 中4週以内 (旧2→1: クリーン窓で2/3期間liftマイナス、回収283.7%は汚染データ由来で再現せず)
SCORE_TRIPLE_RANK1  = 1   # 【新】3指数すべて1位 (N=225 3着内48.9% lift全期間+10pp以上)
SCORE_DIST_EXTEND   = -1  # 【新】距離延長>50m (N=150 3着内24.0% lift全期間マイナス)
SCORE_PREV_BAD      = -1  # 【新】前走10着以下 (N=126 3着内20.6% lift全期間マイナス)
```

**変更しないもの**: `SCORE_PREV_IDX_MID = 1`（前走指数70-89）は廃止候補だが、
クリーン窓22日のみの根拠のため今回は据え置き（クリーン90日蓄積後に再判定）。
`SCORE_PREV_IDX_TOP1 = 2` も同様に据え置き。

### 5-2. `score_horses()` への追加ロジック

**(a) 3指数すべて1位** — prev_db 不要。ループ先頭（②の前あたり）に追加:

```python
# ⓪ 3指数すべて1位（トリプルCSV由来の順位フィールド）
def _is_rank1(v) -> bool:
    return bool(re.match(r"^1位", str(v or "").strip()))

if (_is_rank1(horse.get("近走平均順位"))
        and _is_rank1(horse.get("当該距離順位"))
        and _is_rank1(horse.get("当該コース順位"))):
    score += SCORE_TRIPLE_RANK1
    breakdown.append({"label": "3指数すべて1位", "pts": SCORE_TRIPLE_RANK1})
```

`_is_rank1` は module レベル関数にしてよい。順位フィールドは `"1位"` 形式の文字列
（`"1位タイ"` 等の表記揺れに備えて startswith 判定にしている）。

**(b) 距離延長>50m 減点** — 既存⑪（同距離前走）のブロックを拡張。
同距離（±50m）は +1 のまま、延長（+51m以上）に -1 を追加:

```python
# ⑪ 同距離前走（±50m）/ 距離延長（>50m）減点
if race_dist:
    try:
        prev_dist_str = prev.get("prev_dist", "")
        if prev_dist_str:
            m = re.search(r"(\d{3,4})", prev_dist_str)
            if m:
                diff = race_dist - int(m.group(1))
                if abs(diff) <= 50:
                    score += SCORE_SAME_DIST
                    breakdown.append({"label": f"同距離前走({m.group(1)}m→{race_dist}m)", "pts": SCORE_SAME_DIST})
                elif diff > 50:
                    score += SCORE_DIST_EXTEND
                    breakdown.append({"label": f"距離延長({m.group(1)}m→{race_dist}m)", "pts": SCORE_DIST_EXTEND})
    except (ValueError, TypeError):
        pass
```

※ 距離短縮（diff < -50）は検証の結果不安定だったため**何もしない**こと。

**(c) 前走10着以下 減点** — ⑩（前走好走）の後に追加:

```python
# ⑫ 前走大敗（10着以下）
try:
    prev_rank_val = int(prev.get("prev_rank", "") or 0)
    if prev_rank_val >= 10:
        score += SCORE_PREV_BAD
        breakdown.append({"label": f"前走大敗({prev_rank_val}着)", "pts": SCORE_PREV_BAD})
except (ValueError, TypeError):
    pass
```

(b)(c) は `if prev_db is not None:` ブロック内（prev 取得済みの位置）に置く。

### 5-3. 負スコアの扱い

減点導入によりスコアが負になり得る。
- `score_horses()` 末尾の星ランク判定は現状 `else: rank = "－"` なので負値も "－" になる → 変更不要
- `results.sort(key=lambda x: -x["score"])` も問題なし
- **index.html** が負の pts / 負の score を表示しても崩れないか目視確認
  （breakdown の pts に -1 が入る。スタイル崩れがあれば報告のみ・勝手に直してよいのは表示部のみ）

### 5-4. 呼び出し元の確認

`score_horses()` のシグネチャは**変更なし**。新ファクターに必要な情報
（順位フィールド = triple_horses 内、prev_dist / prev_rank = prev_db 内、race_dist = 引数）は
全呼び出し元（run_pickup_all.py / rescore.py / rerun_failed_pickup.py / race_pickup.analyze_race）で
既に渡されていることを確認済み。`grep -n "score_horses(" *.py` で再確認だけすること。

### 5-5. CLAUDE.md の更新

スコア表に以下を反映:
- 中4週以内: +2 → **+1**（理由: v6監査でlift不安定・旧根拠は汚染データ）
- 追加行: `| 3指数すべて1位 | +1 | 近走平均・当該距離・当該コースの順位がすべて1位 |`
- 追加行: `| 距離延長>50m | -1 | 前走距離より50m超の延長（horse_db使用） |`
- 追加行: `| 前走10着以下 | -1 | 前走大敗（horse_db使用） |`
- 最高合計: 12pt のまま（中4週-1 と 3指数1位+1 が相殺。減点は最高値に影響しない）
- `SCORING_VERSION` の説明行に v6 を追記

---

## タスク6: scrape_results.py のフィールド欠損修正

### 6-1. last3f（上がり3F）が全日付でほぼ 0.0

scrape_results.py:299 のセレクタ `td class~ Last3F|Agari|agari` が実HTMLと不一致の疑い。
手順:
1. `debug_result_html.py`（リポジトリ既存）等で実際のレース結果ページHTMLを保存し、
   上がり3Fセルの実クラス名を確認する（netkeiba の結果テーブルは `td.Time` が複数あり、
   上がりは末尾近くのセルのことが多い）
2. セレクタを実態に合わせて修正。クラスで特定できない場合は
   「`33.0〜46.0` 範囲の小数1桁パターン `^\d{2}\.\d$` を持つ末尾側の td」をフォールバックにする
3. 修正後、直近開催日で再スクレイプして確認:
   ```bash
   python3 rescrape_results_all.py  # または scrape_results.py <date> を単日
   ```
   **注意**: rescrape_results_all.py は全日付を再スクレイプしてアクセス数が多い。
   まず1日だけ `python3 scrape_results.py 20260607` で検証し、問題なければ全日へ。
   既存ファイルの上書き仕様を確認してから実行すること。

受け入れ基準: 対象日の race_results.json で last3f > 0 の馬が9割以上。

### 6-2. corners が直近土曜のみ空（4/18, 4/25, 5/2, 5/23, 5/30, 6/6）

土曜夜の取得タイミングではコーナー通過順が未掲載の可能性が高い。
1. 上記6日付のうち1日を `scrape_results.py <date>` で再取得し、corners が埋まるか確認
2. 埋まるなら6日付すべて再取得（race_results.json は上書き保存される。
   スキップロジックがある場合は既存ファイルを退避してから）
3. 再取得後 `python3 build_horse_style.py` を実行して horse_style.json を更新
4. 恒久対策として、scrape_results.py に「corners が全馬空なら WARNING ログを出す」を追加
   （cron ログで気づけるようにする）

---

## タスク7: クリーン日付の v6 再スコアと検証

タスク1〜5完了後:

```bash
# 1) prev_data.json がある日付のみ rescore（現状 20260328 のみ。
#    タスク2実装後に蓄積される日付は順次可能になる）
python3 rescore.py 20260328

# 2) 前後比較（スコア分布の変化を確認）
python3 compare_scores.py  # 使い方はファイル先頭の docstring を確認

# 3) 再現性監査の再実行（v6スコアで【G】セクションが大きく劣化していないこと）
python3 analyze_factor_audit.py > /tmp/audit_v6.txt
diff <(grep '^■' /tmp/audit_v6.txt) <(echo 確認用)  # 目視でよい

# 4) 閾値再計算
python3 calibrate_threshold.py
```

**20260328 より前の日付は rescore しない**（タスク3の停止仕様どおり）。
v5 のままの過去ファイルが混在するが、analyze_roi.py の【10】バージョン別比較で
v5/v6 を分けて見られるため問題ない。

---

## 禁止事項・注意

- `git push` はユーザーの明示指示があるまで実行しない。コミットは論理単位で分ける
  （例: ①date-aware修正＋snapshot ②rescore/calibrate ③v6スコアリング ④scrape_results修正）
- `git add -A` / `git add .` 禁止。ファイル個別に add
- `.env` / `cookies.json` に触れない
- 過去日付の一括再スクレイプ（rescrape_results_all.py）は実行前にユーザーへ確認
  （アクセス量が多い。IPブロックは24時間解除されない）
- スクレイプを伴う検証はアンチボット対策（CLAUDE.md 参照）の範囲内で実行
- 「期待ROI」をUIに表示しない（docs/anaba_research_log.md の原則）
- UIの「1-3R参考バッジ」見直しは**今回のスコープ外**（ユーザー判断待ち。
  根拠データは factor_audit_202606.md にあり）

## 完了報告に含めること

1. タスクごとの変更ファイル一覧と diff 要点
2. calibrate_threshold.py 実行後の ev_threshold（変わった場合は新旧）
3. compare_scores.py の前後比較サマリ（スコア上昇/下降した馬の件数）
4. last3f / corners の修正後カバレッジ
5. 未実施・保留にしたものとその理由
