# 取得データの照合（2026-11-03）

残りのタスク3・4（`docs/incident_umaban_20261030.md`）の照合。保存済みのデータだけを使い、netkeiba・JRA には新しくアクセスしていない。
結果の記録は `docs/incident_umaban_20261030.md` のタスク3・4。

## 実行
リポジトリの根から:

```bash
bash research/data_check_20261103/run_all.sh
```

- Python 3.9.6、pandas 2.3.3、openpyxl 3.1.5（2026-11-03 の実行環境）
- 出力は `results/`。`*.txt` は集計、`*.json` は集計と明細（不一致・照合できなかったもの）。

## 入力の固定
- 実行の前後で、使う入力ファイルの sha256 を `results/manifest_before.json`・`manifest_after.json` に書いた。実行中に変わったファイルは `results/changed_during_run.json`（2026-11-03 22:35 の実行では0件）。
- 実行時点の結果の取り直し（`race_results_all.json`）: 50日分。うち完了49日（20250920〜20260228 と 20261101）、途中1日（20260301。36レース中13レース）。取り直しは続いているので、後で実行すると結果が変わる。
- `output/*.xlsx`（指数の表）は `.gitignore` でリポジトリに入っていない。sha256 だけを記録した。
- `race_results_all.json` は 2026-11-03 時点でコミットしていない。

## 各スクリプト
| スクリプト | 内容 | 結果の選び方・除外・数え方 |
|---|---|---|
| `check_index_vs_results.py` | タスク3: 指数の表の馬番・馬名を結果と全頭照合 | レースごとに、`race_results_all.json` にそのレースがあれば、着順のある馬と着順が数字でない馬の両方を使う。無ければ `race_results.json`（旧版。着順が数字でない馬を捨てていた）を使う。結果にレースが無い表は数えるだけで照合しない。2段目の見出し行（1列目が「5走前」「着度数」）は読み飛ばす。馬名は空白の違いだけを許す。件数は「表×馬」の延べ |
| `short_tables.py` | タスク3: 表が3行の6日の形 | 行数は2段目の見出し行を含む |
| `tansho_check.py` | 4-1: 1着の馬（同着なら全員）の確定オッズ×100 と JRA の単勝払戻 | `race_results.json` を使う。結果が0頭のレースは照合しない |
| `prev_pop_check.py` | 4-2: 馬ページから読んだ前走の着順・人気（`prev_data.json`）と自前の結果 | 同じ馬ID・同じ日付のレースが結果に1つだけあるときだけ照合する。（馬ID, 前走日）の組は1回だけ数える（`prev_data.json` を日付順に読んで最初の値） |
| `prev_pop_groups.py` | 4-2 追加: 人気が違った組（同じ単勝オッズの馬）ごとの人気の集まり、前走好走の判定が変わるか | 馬ページの値は（前走日, 馬ID）が合う全 `prev_data.json` から集める。「前走好走」の判定は、この（前走日, 馬ID）の組ごとに数える |
| `payout_combo_check.py` | 4-3: 払戻の組み合わせと着順 | 3着以内に同着があるレースは外す。金額は見ない。複勝は払戻の頭数 k について上から k 頭と一致するかだけを見る（k が正しいかは見ていない）。枠連の発売の有無と複勝の払戻頭数は、着順のある頭数との対応を「未検証」として出すだけ。着順のある頭数は出走頭数ではない |
| `hidden_value_scope.py` | タスク9（2026-11-04 追加）: 指数の列で、読めた整数値が200以上の表・レース・日。候補・障害戦・ペーパートレードの買い目・旧集計との関係・当時のコードの再現 | 引数で入力のフォルダを渡す（2026-11-04 は `output/` の写しを読んだ。sha256 は `results/hidden_value_scope_manifest.json`）。`YYYYMMDD.xlsx` だけを対象にする。セルを空・整数・読めない値に分けて表ごとに件数を残す。障害戦はクラス名に「障害」を含むか（`race_conditions.json`→`race_results_all.json`→`race_results.json` の順に探す）。買い目は `paper_bets.json` の `races` と `skipped_races` を区別する。再現は、候補の一覧を最後にコミットした時点の `scraper.find_triple_top5_rows()` を git から取り出して呼ぶ |
| `manifest.py` | 入力の一覧と sha256 | — |

## タスク9の実行の記録（2026-11-04）
- 入力: `output/` の必要なファイル703件の写し（リポジトリに無い）。sha256 は `results/hidden_value_scope_manifest.json`。
- 基準のコミット: `--base-commit 2d1bc13`（2d1bc1397becd5cfa54f4430b7726d75205f3a7b。集計した時点の HEAD）。
- 実行中に入力が変わると、スクリプトは結果ファイルを書かずに終了コード1で止まる。これは小さな写しで試した。
- 集計は、10/25 東京4R を無効にする（`output/20261025/invalidated.json`、コミット c22f1e4）前の入力で行った。そのため結果の 10/25 は `invalidated: false` で、manifest にもこのファイルは無い。
- 無効にした後の確認は別に行った。`paper_trade.py report` の前後の出力が `results/paper_report_before_invalidate_20261025.txt`・`paper_report_after_invalidate_20261025.txt`。

## 確かめていないこと
- 単勝以外の払戻の金額。
- 同着のあるレース（単勝の1レースを除く）。
- 複勝の払戻頭数・枠連の発売が正しいか（出走頭数と各馬の状態が要る）。
- 前走人気について、netkeiba の結果ページと馬ページで差が出る理由、JRA 公式の人気との一致。
- 指数の表を取ったときの取り方が正しいか（保存済みの表と結果の対応だけを見た）。
