"""
verify_results.check_day（結果取得後の全頭照合）の回帰テスト。入力は自作の小さな JSON
（pickup_scores.json・race_results.json・race_nonfinishers.json・race_conditions.json の形に合わせた）。
実データでは 10/31（4レース46頭一致）と 11/1（104頭一致・取消2頭）で手作業の照合と同じ結果になることを確かめた。

実行: python3 -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from verify_results import check_day  # noqa: E402

DATE = "20261107"


class CheckDayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        (self.out / DATE).mkdir()
        self.write("pickup_scores.json", {"races": {
            "東京1R": {"race_id": "1", "horse_id_map": {"1": "A", "2": "B", "3": "C"},
                      "horse_name_full": {"1": "アー", "2": "ビー", "3": "シー"}},
            "東京2R": {"race_id": "2"},                       # 対応表が無い
        }})
        self.write("race_results.json", {"東京1R": [{"rank": 1, "num": "1", "name": "アー", "horse_id": "A"},
                                                   {"rank": 2, "num": "2", "name": "ビー", "horse_id": "B"}]})
        self.write("race_conditions.json", {"東京1R": {"race_id": "1"}})

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        (self.out / DATE / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_scratched_horse_matches_with_nonfinishers(self):
        self.write("race_nonfinishers.json", {"東京1R": [{"num": "3", "name": "シー", "horse_id": "C", "status": "取消"}]})
        r = check_day(DATE, self.out)
        self.assertEqual((r["races"], r["horses"], r["match"], r["mismatch"]), (1, 3, 3, []))
        self.assertEqual(r["not_checked"], {"東京2R": "馬番の対応表が無い"})

    def test_missing_without_nonfinishers_file_is_mismatch(self):
        r = check_day(DATE, self.out)
        self.assertEqual(r["mismatch"], ["東京1R 3番: 結果に無い（取消・除外等の記録が無い日）"])

    def test_wrong_horse_id_and_extra_horse(self):
        self.write("race_results.json", {"東京1R": [{"rank": 1, "num": "1", "name": "アー", "horse_id": "X"},
                                                   {"rank": 2, "num": "2", "name": "ビー", "horse_id": "B"},
                                                   {"rank": 3, "num": "3", "name": "シー", "horse_id": "C"},
                                                   {"rank": 4, "num": "4", "name": "ディー", "horse_id": "D"}]})
        r = check_day(DATE, self.out)
        self.assertEqual(r["mismatch"], ["東京1R 1番: 馬IDが違う（採点 A / 結果 X）",
                                         "東京1R 4番: 結果にあるが採点時の対応表に無い"])

    def test_race_id_mismatch(self):
        self.write("race_nonfinishers.json", {"東京1R": [{"num": "3", "name": "シー", "horse_id": "C", "status": "取消"}]})
        self.write("race_conditions.json", {"東京1R": {"race_id": "9"}})
        self.assertIn("東京1R: レースIDが違う（採点 1 / 結果 9）", check_day(DATE, self.out)["mismatch"])


if __name__ == "__main__":
    unittest.main()
