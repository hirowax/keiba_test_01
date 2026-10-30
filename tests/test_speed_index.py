"""
タイム指数セルの読み取り（scraper.speed_cell_score / index_out_of_range）の回帰テスト。

2026-10-30 発覚: shutuba_submenu の指数セルの隠し値（並び替え用「1000＋指数」）を指数として読み、
データ無しの馬が指数1000で1位扱いになっていた（docs/incident_umaban_20261030.md 追記）。
セル構造は 2026-10-30 に実ページ（race_id=202608041004, 障害レース）で確認したもの。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper import index_out_of_range, speed_cell_score  # noqa: E402


def cell(hidden, visible):
    html = (f'<td class="cellcolor_ sk__average_index">'
            f'<span class="Sort_Function_Data_Hidden">{hidden}</span>{visible}</td>')
    return BeautifulSoup(html, "html.parser").td


class SpeedCellTest(unittest.TestCase):
    def test_visible_index_is_used_not_hidden_sort_value(self):
        self.assertEqual(speed_cell_score(cell("1083", "83")), "83")
        self.assertEqual(speed_cell_score(cell("1102", "102")), "102")

    def test_no_data_is_blank(self):
        for visible in ("-", "未", ""):
            with self.subTest(visible=visible):
                self.assertEqual(speed_cell_score(cell("1000", visible)), "")

    def test_out_of_range_is_rejected(self):
        self.assertEqual(speed_cell_score(cell("", "1000")), "")

    def test_none_cell(self):
        self.assertEqual(speed_cell_score(None), "")

    def test_original_cell_not_modified(self):
        c = cell("1083", "83")
        speed_cell_score(c)
        self.assertIsNotNone(c.find(class_="Sort_Function_Data_Hidden"))

    def test_rank_table_range_check(self):
        ok = pd.DataFrame({"馬名": ["a", "b"], "指数": ["90", "-"]})
        bad = pd.DataFrame({"馬名": ["a", "b"], "指数": ["1000", "1083"]})
        self.assertEqual(index_out_of_range(ok), [])
        self.assertEqual(index_out_of_range(bad), [1000.0, 1083.0])
        self.assertEqual(index_out_of_range(None), [])


if __name__ == "__main__":
    unittest.main()
