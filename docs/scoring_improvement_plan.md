# スコアリング改善方針（C〜F）

**作成日**: 2026-05-04
**スコアリングバージョン**: C〜F実施後は `SCORING_VERSION = "v5"` に更新すること
**ベースデータ**: 36日分（2025-12-13〜2026-05-03）/ 795頭
**対象**: `race_pickup.py` の `score_horses()` 関数

このドキュメントは、A（コメント修正）/ B（中4週以内 +1→+2）を実施した後の追加改善案。
**実装は別モデルで行うため、迷わず実装できる粒度で方針を記述する。**

---

## 前提：完了済み修正

- [x] **A**: `race_pickup.py:33` のコメント `±100m` → `±50m` 修正済み
- [x] **B**: `SCORE_RECENT_RACE = 1 → 2` に昇格済み
- [x] CLAUDE.md スコア表・最高合計を更新（14pt → 15pt）

実装後は **再キャリブレーション** が必要：
```bash
python3 rescore.py 全日付
python3 calibrate_threshold.py
```

---

## C. ポジション有利の廃止

### 現状
```python
SCORE_POSITION = 1   # 推定ポジション有利馬（4コーナーAI）
```

### 実績（要因）
- N=68, 勝率11.8%, 3着内41.2%, **単勝回収率34.6%**
- 組合せでも改善せず：
  - `同距離前走 + ポジション有利`: 32.9%
  - `前走指数70-89 + ポジション有利`: 46.6%
  - `中4週以内 + ポジション有利`: 63.3%（唯一マシだがN=12）

### 結論
**単独・組合せどちらでも収益貢献ゼロ。廃止する。**

### 実装方針
1. `race_pickup.py` の定数を `SCORE_POSITION = 0` に変更
2. コメントに廃止理由を追記：`(N=68 勝率11.8% 単勝回収率34.6% → 廃止)`
3. **`scrape_shutuba()` 内の `position_nums` 取得処理は残す**（pickup_scores.json の他フィールドで参照されている可能性、削除はしない）
4. CLAUDE.md スコア表を更新：`| 推定ポジション有利馬 | 0（廃止） | ... |`
5. 最高合計 15pt → 14pt
6. `score_horses()` 内のスコアリングブロック ① はそのまま残す（`SCORE_POSITION=0` なのでスコアに影響しない、breakdownには残ることに注意）
   - **breakdown に出さないようにする**：`if num in position_nums and SCORE_POSITION > 0:` のガードを追加

---

## D. 逃げ馬を予測ペース連動に変更

### 現状
```python
SCORE_FRONT_RUNNER = 1   # 逃げ馬（2走以上実績、horse_style_db使用）
```
無条件で2走以上の逃げ実績があれば +1。

### 実績
- N=35, 勝率11.4%, 3着内51.4%, **単勝回収率53.4%**
- 3着内率は高めだが、単勝回収率が低い → 人気サイドの逃げ馬にしか付かない

### 課題
- 予測ペース（S/M/H）は `scrape_shutuba()` で取得済みだが、`score_horses()` に渡されていない
- 逃げ馬が活きるのは Sペース（スロー）。Hペース（ハイ）では不利

### 実装方針

**前提**：データ件数が少ない（N=35）ため、まずは **「Sペース時のみ +1pt」** に変更し、実績を蓄積後に再評価。

1. `score_horses()` のシグネチャに `predicted_pace: str = None` を追加
   ```python
   def score_horses(
       triple_horses, shutuba_data, data_top_data,
       prev_db=None, race_max_prev_idx=None, race_date=None,
       horse_style_db=None, race_dist=None,
       predicted_pace=None,  # ← 追加
   ):
   ```

2. `scrape_shutuba()` の戻り値に既に `predicted_pace` がある（race_pickup.py:200）。**呼び出し元の `run_pickup_all.py` で `score_horses()` に渡す必要がある**。

3. 逃げ馬スコアリング部分を変更：
   ```python
   # ⑨ 逃げ馬（horse_style_db + 予測ペース連動）
   if horse_style_db is not None:
       hid_style = horse_id_map.get(num, "")
       style_entry = horse_style_db.get(hid_style, {}) if hid_style else {}
       if style_entry.get("style") == "逃げ" and style_entry.get("n_races", 0) >= 2:
           # Sペース（スロー）のときのみ加点。M/H/不明では加点しない
           if predicted_pace == "S":
               score += SCORE_FRONT_RUNNER
               breakdown.append({"label": f"逃げ馬({style_entry['n_races']}走実績・Sペース)", "pts": SCORE_FRONT_RUNNER})
   ```

4. `run_pickup_all.py` 側の `score_horses(...)` 呼び出しに `predicted_pace=shutuba_data["predicted_pace"]` を追加。
   - **既存の呼び出し箇所をすべて grep で確認**：`grep -n "score_horses(" *.py`
   - 漏れがあると逃げ馬スコアが常に0になるので注意

5. `rerun_failed_pickup.py`, `rescore.py` でも `score_horses()` を呼んでいる場合は同様に修正。
   - `rescore.py` は pickup_scores.json から再計算するので、保存済みの `predicted_pace` を読む必要あり
   - `pickup_scores.json` のレース単位データに `predicted_pace` が保存されているか確認：`run_pickup_all.py` で保存している
   - 保存されていれば `rescore.py` でそれを取り出して渡す

6. CLAUDE.md スコア表を更新：`| 逃げ馬（Sペース予測時のみ・2走以上実績） | +1 | ... |`

### 注意
- ペース予測が S でないレースの逃げ馬は加点ゼロになる
- 過去データの大半は M/H なので、再キャリブレーション後に「N=35 → N≈10〜15」程度に減る見込み
- データ蓄積後（3ヶ月後など）に再度評価。十分機能しなければ完全廃止も検討

---

## E. 各データ上位3頭の上限を2ptに

### 現状
```python
SCORE_TOP3_EACH = 1   # 各データ上位3頭 カテゴリー登場1回 = 1pt
# 実装：cnt × 1pt（上限なし）
```
shutuba.html の「各データ上位3頭」セクションの登場カテゴリー数 × 1pt。

### 実績
- N=552（795頭中70%）→ **識別力が低い**
- 勝率13.9%, 3着内38.2%, 回収率75.1% → 全体平均並み
- 多重カウントで 4〜5pt つく馬がいると、過剰加点になる

### 実装方針

**カテゴリ数の上限を 2pt にキャップ**する。

1. `race_pickup.py` の該当部分を変更：
   ```python
   # ② 各データ上位3頭（shutuba）
   cnt = top3_hits.get(num, 0)
   if cnt > 0:
       pts = min(cnt * SCORE_TOP3_EACH, 2)  # 上限2pt
       score += pts
       breakdown.append({"label": f"各データ上位3頭 {cnt}カテゴリー(上限2pt)", "pts": pts})
   ```

2. CLAUDE.md スコア表を更新：`| 各データ上位3頭 | +1/カテゴリー（上限2pt） | ... |`

3. 最高合計の再計算：
   - C実施後（ポジション廃止）: 14pt
   - E実施後（上位3頭が制限なし最大4ptと仮定 → 2ptに）: -2pt → **12pt**
   - ※実際には B（中4週以内 +2）で +1 されているので 13pt が最終形

### 注意
- 「出走馬分析（data_top）」も同様に多重加点しているが、こちらはN=95と適切な識別力でROI 107.9%。**触らない**。

---

## F. 重複加点ガード（前走指数）

### 現状
- `SCORE_PREV_IDX_HIGH = 2`（前走指数90以上）
- `SCORE_PREV_IDX_TOP1 = 2`（前走指数レース内1位）

これら2つは**同じ「前走指数の絶対値」を評価する重複ファクター**。前走指数95でレース内1位の馬は **両方加算されて +4pt** になる。

### 実績
- 前走指数90以上: N=155, 回収率87.0%
- 前走指数レース内1位: N=124, 回収率118.4%
- AND: `前走指数90以上 + 前走指数レース内1位` N=78, 回収率121.2%
  - 単独「レース内1位」と比較してわずか +3% → **重複加点 +4pt の追加価値は小さい**

### 仮説
スコア10pt+ の3着内率が頭打ち（75.9%）になっているのは、この重複加点で「実力の上振れ評価」が起きている可能性。

### 実装方針

**前走指数HIGH（+2）と前走指数TOP1（+2）の合算上限を3ptにキャップ**する。

1. `race_pickup.py` の該当部分を変更：
   ```python
   # ⑤ 前走タイム指数 + ⑦ レース内1位 → 合算上限3pt
   prev_idx_score = 0
   prev_idx_label = []

   try:
       prev_idx = float(prev.get("prev_idx", ""))
       if prev_idx >= 90:
           prev_idx_score += SCORE_PREV_IDX_HIGH
           prev_idx_label.append(f"前走指数{prev_idx:.0f}(90以上)")
       elif prev_idx >= 70:
           prev_idx_score += SCORE_PREV_IDX_MID
           prev_idx_label.append(f"前走指数{prev_idx:.0f}(70以上)")

       if (race_max_prev_idx is not None and race_max_prev_idx > 0
               and prev_idx == race_max_prev_idx):
           prev_idx_score += SCORE_PREV_IDX_TOP1
           prev_idx_label.append(f"前走指数レース内1位({prev_idx:.0f})")
   except (ValueError, TypeError):
       pass

   # 合算上限3pt
   if prev_idx_score > 3:
       prev_idx_score = 3
       prev_idx_label.append("(合算上限3pt)")

   if prev_idx_score > 0:
       score += prev_idx_score
       breakdown.append({"label": " + ".join(prev_idx_label), "pts": prev_idx_score})
   ```

2. **既存の `breakdown` への追加ロジックを変更する点に注意**：
   - 旧：HIGH/MID と TOP1 を別エントリで追加
   - 新：合算してエントリは1つに統合する

3. CLAUDE.md スコア表に注記を追加：
   ```
   ※「前走指数HIGH/MID」と「前走指数レース内1位」は合算上限3pt（重複加点ガード）
   ```

4. 最高合計の再計算：
   - 旧：90以上(+2) + レース内1位(+2) = 4pt
   - 新：合算上限3pt
   - 差分 -1pt → **12pt**（C+E+F全実施後）

---

## 実装順序（推奨）

1. **C（ポジション廃止）** — 最も単純、影響軽微
2. **E（上位3頭 上限2pt）** — 単純な修正、識別力向上
3. **F（前走指数 合算上限）** — ロジック変更があるためテスト必須
4. **D（逃げ馬 ペース連動）** — 引数追加で複数ファイル修正、最後に実施

各ステップ後に：
```bash
python3 rescore.py YYYYMMDD  # 1日ぶん試して確認
python3 analyze_roi.py        # 全体への影響を見る
python3 calibrate_threshold.py  # 閾値再計算
```

---

## 期待される効果

| 改善 | 期待効果 |
|------|---------|
| C: ポジション廃止 | ノイズ除去、9pt付近の精度向上 |
| D: 逃げ馬ペース連動 | 単勝回収率向上（人気サイドの逃げ除外） |
| E: 上位3頭上限2pt | 過剰加点抑制、10pt超の頭打ちを解消 |
| F: 前走指数合算上限 | 同上、重複評価を是正 |

**目標**：閾値を 8pt → 7pt に下げても 3着内率70%が維持できる状態（＝穴馬の取りこぼし減少）
