"""
compare_index_top3_202611.py の参考集計のオプション（--days・--index-dir。2026-11-07 追加）の検査。
2026-11-07 外部レビュー指摘: 省略したときに今の結果と同じになるだけでは、新しい入力先を正しく使うかは分からない。
自作の小さなデータ（架空の日 20990101・20990102）で確かめる。成績の数字そのものは見ない。

- 指定した日の、指定したフォルダの xlsx だけを読む（output/{日}.xlsx は読まない）
- 指定したファイルが無ければ止まる（output/{日}.xlsx に戻らない）。結果が未完了の日も止まる
- 実行の記録に、実際に読んだ xlsx を残す
- 参考集計では前半・後半を作らない。通常の実行では今の出力の形のまま
- 払戻の不足を単勝・複勝で別々に数える

実行: python3 -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research" / "task3_index_refetch_20261106"))
import compare_index_top3_202611 as cmp  # noqa: E402
from export_tables import write_day  # noqa: E402

OLD = str(ROOT / "research" / "index_top3_compare_202611" / "old_scraper_6f2ed8d.py")
COLS = ["順位", "枠", "馬番", "印", "馬名", "テスト指数"]
LABELS = {"average": "近走平均", "distance": "当該距離", "course": "当該コース"}


def table(values):
    """values: {馬番: 指数の文字}。値の大きい順にページの順位を付ける（同じ値は無い前提）"""
    data = [["着度数", "", "", "", "", ""]]
    for i, (n, v) in enumerate(sorted(values.items(), key=lambda kv: -int(kv[1]))):
        data.append([f"{i + 1}位", "1", str(n), "", f"馬{n}", v])
    return pd.DataFrame(data, columns=COLS)


def write_xlsx(path, values):
    write_day(path, [("東京", "東京1R", LABELS[m], table(values)) for m in LABELS])


class ReferenceOptions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.out = base / "output"
        self.idx = base / "tables"
        self.out.mkdir()
        self.idx.mkdir()
        self.days = ["20990101", "20990102"]
        good = {1: "90", 2: "80", 3: "70", 4: "60"}
        bad = {1: "1090", 2: "80", 3: "70", 4: "60"}   # 200以上 → 取得異常で外れる
        for d in self.days:
            (self.out / d).mkdir()
            # output/{日}.xlsx は取得異常の表、取り直した表は正常。どちらを読んだかがレースの外し方で分かる
            write_xlsx(self.out / f"{d}.xlsx", bad)
            write_xlsx(self.idx / f"{d}_index_refetch.xlsx", good)
            horses = [{"num": n, "name": f"馬{n}", "rank": n, "odds": 2.0 * n} for n in (1, 2, 3, 4)]
            (self.out / d / "race_results_all.json").write_text(json.dumps(
                {"complete": True, "races": {"東京1R": {"race_id": f"{d[:4]}05010101", "horses": horses, "others": []}}}),
                encoding="utf-8")
            # 単勝だけあって複勝が無い
            (self.out / d / "payouts_jra.json").write_text(json.dumps({"東京1R": {"tansho": {"1": 200}}}), encoding="utf-8")
        self.saved = (cmp.OUTPUT_DIR, cmp.START, cmp.END)
        cmp.OUTPUT_DIR, cmp.START, cmp.END = self.out, "20990101", "20991231"

    def tearDown(self):
        cmp.OUTPUT_DIR, cmp.START, cmp.END = self.saved
        self.tmp.cleanup()

    def run_main(self, *extra):
        res = Path(self.tmp.name) / "result.json"
        cmp.main(["--old-module", OLD, "--out", str(res), *extra])
        return json.loads(res.read_text(encoding="utf-8"))

    def ref_args(self, days=None):
        return ["--days", ",".join(days or self.days), "--index-dir", str(self.idx)]

    def test_reads_only_index_dir(self):
        r = self.run_main(*self.ref_args())
        self.assertEqual(r["race_exclusions"], {"比較の対象": 2})      # 取得異常の output/ の表は読んでいない
        self.assertEqual(r["days"], 2)
        self.assertEqual(sorted(r["run"]["index_files"]), self.days)
        for d in self.days:
            f = r["run"]["index_files"][d]
            self.assertEqual(f["path"], str(self.idx / f"{d}_index_refetch.xlsx"))
            self.assertEqual(f["sha256"], cmp.sha256(self.idx / f"{d}_index_refetch.xlsx"))
            self.assertNotIn(f"{d}.xlsx", r["run"]["inputs_sha256"])
            self.assertNotIn(f"{d}/全場_3指数重複馬.csv", r["run"]["inputs_sha256"])

    def test_only_given_days(self):
        r = self.run_main(*self.ref_args(["20990102"]))
        self.assertEqual(r["days"], 1)
        self.assertEqual(list(r["run"]["index_files"]), ["20990102"])
        self.assertEqual(r["race_exclusions"], {"比較の対象": 1})

    def test_stops_when_index_file_missing(self):
        (self.idx / "20990102_index_refetch.xlsx").unlink()
        self.assertTrue((self.out / "20990102.xlsx").exists())
        with self.assertRaises(SystemExit) as e:
            self.run_main(*self.ref_args())
        self.assertIn("指数の xlsx が無い", str(e.exception))

    def test_stops_when_results_incomplete(self):
        p = self.out / "20990101" / "race_results_all.json"
        j = json.loads(p.read_text(encoding="utf-8"))
        j["complete"] = False
        p.write_text(json.dumps(j), encoding="utf-8")
        with self.assertRaises(SystemExit):
            self.run_main(*self.ref_args())

    def test_days_and_index_dir_together(self):
        with self.assertRaises(SystemExit):
            self.run_main("--days", "20990101")
        with self.assertRaises(SystemExit):
            self.run_main("--index-dir", str(self.idx))

    def test_no_halves_in_reference(self):
        r = self.run_main(*self.ref_args())
        self.assertTrue(r["results"])
        self.assertTrue(all(k.startswith("全期間・") for k in r["results"]))
        self.assertEqual(r["repro_diff_days"], [])

    def test_payout_by_kind(self):
        r = self.run_main(*self.ref_args())
        miss = r["races_missing_payout_by_kind"]
        self.assertEqual(miss["tansho"], [])
        self.assertEqual(sorted(map(tuple, miss["fukusho"])), [(d, "東京1R") for d in self.days])
        self.assertEqual(r["races_without_payout"], [])   # 今までの数え方（単勝だけ）では不足が見えない

    def test_normal_mode_unchanged_shape(self):
        r = self.run_main()
        self.assertEqual(r["race_exclusions"], {"取得異常（200以上・読めない値・順位の形・馬番の重複）": 2})  # output/ を読む
        self.assertEqual({k.split("・")[0] for k in r["results"]}, {"全期間", "前半", "後半"})
        self.assertNotIn("index_files", r["run"])
        self.assertNotIn("payout_coverage_by_kind", r)
        self.assertNotIn("races_missing_payout_by_kind", r)
        self.assertIn("20990101.xlsx", r["run"]["inputs_sha256"])
        self.assertEqual(sorted(r), sorted(["files", "day_skip", "days", "race_exclusions", "unmatched", "repro_diff_days",
                                            "status", "payout_coverage", "races_without_payout", "run", "results"]))


if __name__ == "__main__":
    unittest.main()
