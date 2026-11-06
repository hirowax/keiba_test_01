"""
タスク3の参考集計の計画を書く前に、書き出した表がタスク7と同じ表の検査でどう分かれるかを、レースの件数だけ数える（2026-11-07）。
成績・候補は見ない。2026-11-07 にその場のコードで数えたものを、外部レビューの勧めで再実行できる形にした（同じ処理）。

- 読み込み: index_table_check.load_index_tables（タスク7と同じ）で tables/{日}_index_refetch.xlsx を読む。
- 分け方: xlsx にレースが無い／3つの表がそろわない／取得異常（ranked_horses の2つ目）／順位の食い違い（3つ目）／検査を通る。
  検査を通るレースは、tables/manifest.json のページの分類の組み合わせで分ける。

実行: python3 research/task3_index_refetch_20261106/count_table_checks.py
"""
import json
import logging
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import scraper  # noqa: E402
from index_table_check import load_index_tables  # noqa: E402

logging.disable(logging.CRITICAL)
HERE = Path(__file__).resolve().parent
DAYS = ["20251102", "20260110", "20260111", "20260117", "20260118", "20260124"]


def main():
    m = json.load(open(HERE / "tables" / "manifest.json", encoding="utf-8"))
    c = Counter()
    for d in DAYS:
        t = load_index_tables(HERE / "tables" / f"{d}_index_refetch.xlsx")
        ra = json.load(open(ROOT / "output" / d / "race_results_all.json", encoding="utf-8"))["races"]
        for lab in ra:
            dfs = t.get(lab, {})
            if not dfs:
                c["xlsxにレースが無い"] += 1
                continue
            if len(dfs) < 3:
                c["3つの表がそろわない"] += 1
                continue
            ch = [scraper.ranked_horses(v) for v in dfs.values()]
            if any(x[1] for x in ch):
                c["取得異常"] += 1
                continue
            if any(x[2] for x in ch):
                c["順位の食い違い"] += 1
                continue
            kinds = {p["kind"] for p in m["pages"] if p["date"] == d and p["label"] == lab}
            c["検査を通る:" + "/".join(sorted(kinds))] += 1
    print(dict(c))


if __name__ == "__main__":
    main()
