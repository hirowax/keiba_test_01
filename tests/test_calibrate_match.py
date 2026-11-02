"""
calibrate_threshold.match_result（閾値計算の結果照合）の回帰テスト。
2026-11-02 まで「馬番か馬名のどちらかが一致」で照合していた（残りのタスク6）。
入力は自作の小さな結果データ（race_results.json・race_nonfinishers.json の形に合わせた）。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from calibrate_threshold import NOT_TOP3, match_result  # noqa: E402

ROWS = [{"rank": 1, "num": "3", "name": "アルファ"}, {"rank": 2, "num": "5", "name": "ベータ"}]
OTHERS = {"7": {"num": "7", "name": "ガンマ", "status": "取消"}, "8": {"num": "8", "name": "デルタ", "status": "中止"}}


class MatchTest(unittest.TestCase):
    def test_both_match(self):
        self.assertEqual(match_result(ROWS, OTHERS, "3", "アルファ"), 1)
        self.assertEqual(match_result(ROWS, OTHERS, "3", "アル ファ"), 1)  # 空白の違いだけは許す

    def test_name_only_match_is_not_used(self):
        """馬名だけ合っても、馬番が違えば同じ馬としない（旧: 一致扱い）"""
        self.assertEqual(match_result(ROWS, OTHERS, "4", "アルファ"),
                         "馬番が結果に無い（取消・除外・中止・失格の区別がつかない日を含む）")

    def test_number_only_match_is_not_used(self):
        self.assertEqual(match_result(ROWS, OTHERS, "5", "アルファ"), "馬番は合うが馬名が違う")

    def test_nonfinishers(self):
        self.assertEqual(match_result(ROWS, OTHERS, "7", "ガンマ"), "取消")
        self.assertEqual(match_result(ROWS, OTHERS, "8", "デルタ"), NOT_TOP3)

    def test_no_results(self):
        self.assertEqual(match_result(None, {}, "3", "アルファ"), "結果が無い")


if __name__ == "__main__":
    unittest.main()
