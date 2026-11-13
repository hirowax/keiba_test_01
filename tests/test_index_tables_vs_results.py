"""
結果の後の、その日の全レースの指数の表と結果の照合（index_table_check.check_day_against_results。2026-11-13〜。
docs/incident_umaban_20261030.md タスク3の残り）の回帰テスト。
実ページの表（tests/fixtures/rank_202605041007_average.html、東京7R・11頭）から、結果のファイルと取得の記録を作って試す。

実行: python3 -m unittest discover -s tests -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_pages import read_fixture  # noqa: E402
import index_table_check as I  # noqa: E402
import verify_results as V  # noqa: E402
from scraper import parse_rank_html, ranked_horses  # noqa: E402

DATE = "20261114"
ALL3 = {"近走平均": "type=rank", "当該距離": "type=rank", "当該コース": "type=rank"}
NONE3 = {"近走平均": "なし", "当該距離": "なし", "当該コース": "なし"}


class AllRacesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.df = parse_rank_html(read_fixture("rank_202605041007_average.html"))
        self.horses = ranked_horses(self.df)[0]
        (self.tmp / DATE).mkdir()
        (self.tmp / f"{DATE}.xlsx").write_bytes(b"")   # 中身は load_index_tables を差し替えて渡す
        self.tables = {"東京7R": {"average": self.df, "distance": self.df, "course": self.df}}
        self.results = {"東京7R": self.race(), "東京5R": self.race(cls="サラ系２歳 新馬", nums=["1", "2"]),
                        "東京4R": self.race(cls="障害３歳以上 未勝利", nums=["1", "2"])}
        self.sources = {"東京7R": {"modes": dict(ALL3)}, "東京5R": {"modes": dict(NONE3)}, "東京4R": {"modes": dict(NONE3)}}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def race(self, cls="３歳以上１勝クラス", nums=None, scratched=()):
        hs = [{"num": h["馬番"], "name": h["馬名"], "horse_id": f"H{h['馬番']}"} for h in self.horses] if nums is None else \
             [{"num": n, "name": f"馬{n}", "horse_id": f"H{n}"} for n in nums]
        horses = [h for h in hs if h["num"] not in scratched]
        others = [dict(h, status="取消") for h in hs if h["num"] in scratched]
        return {"condition": {"class_name": cls}, "horses": horses, "others": others}

    def run_check(self):
        (self.tmp / DATE / "race_results_all.json").write_text(json.dumps({"races": self.results}, ensure_ascii=False))
        (self.tmp / DATE / "index_sources.json").write_text(json.dumps({"races": self.sources}, ensure_ascii=False))
        with mock.patch.object(I, "load_index_tables", return_value=self.tables):
            return I.check_day_against_results(DATE, self.tmp)

    def test_all_consistent(self):
        r = self.run_check()
        self.assertEqual((r["races"], r["tables"], r["problems"]), (3, 3, []))
        self.assertEqual(sorted(r["no_table_new_horse"]), ["東京5R 当該コース", "東京5R 当該距離", "東京5R 近走平均"])
        self.assertEqual(len(r["no_table_unknown"]), 3)
        self.assertTrue(all("障害" in x for x in r["no_table_unknown"]))

    def test_missing_extra_and_name_are_problems(self):
        """表の欠け（結果にある出走馬が表に無い）・余分・馬名の違いは問題"""
        col = next(c for c in self.df.columns if "馬番" in str(c))
        first = self.horses[0]["馬番"]
        self.tables["東京7R"]["course"] = self.df[self.df[col].astype(str).str.strip() != first].reset_index(drop=True)
        self.results["東京7R"]["horses"][1]["name"] = "ベツノウマ"
        r = self.run_check()
        self.assertTrue(any("当該コース" in p and "missing" in p for p in r["problems"]), r["problems"])
        self.assertTrue(any("name_mismatch" in p for p in r["problems"]), r["problems"])
        self.results["東京7R"] = self.race(nums=[h["馬番"] for h in self.horses][1:])   # 結果に無い馬番が表にある
        self.assertTrue(any("extra" in p for p in self.run_check()["problems"]))

    def test_scratched_horse_may_be_absent(self):
        """取消・除外の馬は、表にあってもなくてもよい（本番の照合と同じ規則）"""
        self.results["東京7R"] = self.race(scratched=(self.horses[0]["馬番"],))
        self.assertEqual(self.run_check()["problems"], [])

    def test_failed_or_missing_record_and_inconsistency_are_problems(self):
        self.sources["東京7R"]["modes"]["当該距離"] = "取得異常"
        del self.tables["東京7R"]["distance"]
        r = self.run_check()
        self.assertIn("東京7R 当該距離: 表が無く、取得の記録が「取得異常」", r["problems"])
        self.sources["東京7R"]["modes"]["当該距離"] = "なし"
        self.tables["東京7R"]["distance"] = self.df
        self.assertTrue(any("食い違い" in p for p in self.run_check()["problems"]))
        del self.sources["東京4R"]
        self.assertIn("東京4R: 結果にあるレースが、指数の取得の記録に無い", self.run_check()["problems"])

    def test_table_without_record_is_problem(self):
        self.tables["東京9R"] = {"average": self.df}
        self.assertIn("東京9R: 指数の表があるのに、取得の記録にレースが無い", self.run_check()["problems"])

    def test_missing_files_are_not_checked(self):
        (self.tmp / f"{DATE}.xlsx").unlink()
        r = self.run_check()
        self.assertEqual(r["not_checked"], {"（その日）": f"指数の表のファイル（{DATE}.xlsx）が無い"})

    def test_verify_results_exit_code_and_output(self):
        """verify_results の main: 指数の表の問題があれば終了コード1、results_check.json に index_tables"""
        self.results["東京7R"]["horses"][1]["name"] = "ベツノウマ"
        (self.tmp / DATE / "race_results_all.json").write_text(json.dumps({"races": self.results}, ensure_ascii=False))
        (self.tmp / DATE / "index_sources.json").write_text(json.dumps({"races": self.sources}, ensure_ascii=False))
        with mock.patch.object(I, "load_index_tables", return_value=self.tables), \
                mock.patch.object(V, "OUTPUT_DIR", self.tmp), mock.patch.object(V.sys, "argv", ["x", DATE]), \
                mock.patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                V.main()
        self.assertEqual(cm.exception.code, 1)
        saved = json.loads((self.tmp / DATE / "results_check.json").read_text())
        self.assertTrue(saved["index_tables"]["problems"])


if __name__ == "__main__":
    unittest.main()
