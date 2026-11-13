"""
指数の表の列の決め方（scraper._table_columns。2026-11-13〜）の回帰テスト。

それまでは「名前に『指数』を含む最初の列」を指数の列にしていたので、指数の列より前に「指数」を含む列が増えると、
別の値を読む危険があった（2026-11-13 外部レビュー。同じ日に馬ページの前走の指数で、見出しの文字に頼った読み取りが
全頭失敗した）。今は、順位・馬番・馬名を見出しの完全一致で1つずつ決め、指数の列は決まった形の見出し（INDEX_HEADER_RE）が
ちょうど1つで、馬名のすぐ次にあるものだけにする。決められなければ取得異常。
保存済みの 10,336 表（output/*.xlsx）で、今までの読み方と結果が完全に同じことを確かめた（2026-11-13）。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scraper as S  # noqa: E402

HEAD = ["順位", "枠", "馬番", "印", "馬名"]


def df(idx_header="近5走平均指数", extra=(), head=None):
    cols = list(head or HEAD) + [idx_header] + list(extra)
    rows = [["1位", "1", "3", "--", "ウマA", "90"] + ["x"] * len(extra),
            ["2位", "2", "5", "--", "ウマB", "80"] + ["x"] * len(extra)]
    return pd.DataFrame(rows, columns=cols)


class ColumnsTest(unittest.TestCase):
    def test_known_headers(self):
        for h in ("近5走平均指数", "芝1800最高指数", "ダート1200最高指数", "障害3000最高指数",
                  "東京芝コース最高指数", "中山障害コース最高指数", "小倉ダートコース最高指数", "指数"):
            horses, errors, _ = S.ranked_horses(df(h))
            self.assertEqual(errors, [], h)
            self.assertEqual([(x["馬番"], x["値"]) for x in horses], [("3", 90), ("5", 80)], h)

    def test_average_table_with_race_values_column(self):
        """近走平均の右の「近5走の指数」（各レースの値）は指数の列にしない"""
        horses, errors, _ = S.ranked_horses(df(extra=("近5走の指数", "col7")))
        self.assertEqual(errors, [])
        self.assertEqual(horses[0]["値"], 90)

    def test_unknown_or_moved_index_column_is_error(self):
        """知らない見出し・馬名の次でない・決まった形の見出しが2つ、は取得異常（別の列を読まない）"""
        cases = [df("近5走最高指数"),                                     # 知らない見出し
                 df("芝1800最高指数 "[:-1] + "X"),                          # 形が違う
                 pd.DataFrame([["1位", "1", "3", "--", "ウマA", "z", "90"]],
                              columns=HEAD + ["スタート指数", "近5走平均指数"]),   # 指数の列が馬名の次でない
                 df(extra=("指数",))]                                       # 決まった形が2つ
        for d in cases:
            horses, errors, _ = S.ranked_horses(d)
            self.assertEqual(horses, [], list(d.columns))
            self.assertTrue(errors and errors[0].startswith("列を決められない"), (list(d.columns), errors))

    def test_old_rule_would_read_wrong_column(self):
        """前の読み方なら、馬名の前に「指数」を含む列（参考指数）が増えると、その列（55）を読んでいた。
        今は決まった形の見出しで馬名の次の列（90）を読む"""
        d = pd.DataFrame([["1位", "1", "3", "55", "ウマA", "90"]], columns=["順位", "枠", "馬番", "参考指数", "馬名", "近5走平均指数"])
        horses, errors, _ = S.ranked_horses(d)
        self.assertEqual(errors, [])
        self.assertEqual(horses[0]["値"], 90)

    def test_rank_num_name_must_be_exact_and_unique(self):
        bad = [["着順", "枠", "馬番", "印", "馬名"],          # 順位の見出しが無い
               ["枠", "順位", "馬番", "印", "馬名"],          # 順位が先頭でない
               ["順位", "枠", "馬番2", "印", "馬名"],         # 馬番の完全一致が無い
               ["順位", "馬番", "馬番", "印", "馬名"]]        # 馬番が2つ
        for head in bad:
            horses, errors, _ = S.ranked_horses(df(head=head))
            self.assertEqual(horses, [], head)
            self.assertTrue(errors[0].startswith("列を決められない"), head)


if __name__ == "__main__":
    unittest.main()
