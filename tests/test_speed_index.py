"""
タイム指数の読み取り（scraper.classify_index_text / speed_cell_value / index_read_errors /
parse_rank_html / parse_speed_shutuba_html）の回帰テスト。

経緯（docs/incident_umaban_20261030.md 追記）:
- 2026-10-30: shutuba_submenu の指数セルの隠し値（並び替え用「1000＋指数」）を指数として読み、
  データ無しの馬が指数1000で1位扱いになっていた。
- 同日夜: その修正で入れた範囲検査（0より大きく200より小さい）が、正当な0・マイナスの指数を
  異常扱いしていた。代わりの取り方では「72*」（5走分のデータが無い馬の近走平均）やマイナスを
  データ無しとして消していた。

tests/fixtures/ の HTML は 2026-10-30 に実ページから取得し、表だけを抜き出したもの。
- rank_202604040403_course.html  … 新潟3R（10/25）当該コース。マイナスの指数（-3, -4）を含む
- rank_202605041007_average.html … 東京7R（10/31）近走平均。「96*」等を含む
- submenu_*.html                 … 同じ2レースの rf=shutuba_submenu（代わりの取り方）

テストで確認できるのは「この4ページの形を正しく読めること」まで。ほかの形の値が実ページに
出てくる可能性は残る（未知の形は取得異常として、そのモードを使わない）。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper import (  # noqa: E402
    classify_index_text, index_read_errors, parse_rank_html,
    parse_speed_shutuba_html, speed_cell_value, _is_valid_speed_df,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


def cell(hidden, visible):
    html = (f'<td class="cellcolor_ sk__average_index">'
            f'<span class="Sort_Function_Data_Hidden">{hidden}</span>{visible}</td>')
    return BeautifulSoup(html, "html.parser").td


class ClassifyTest(unittest.TestCase):
    def test_valid_values(self):
        for text, v in (("83", 83.0), ("96*", 96.0), ("0", 0.0), ("-3", -3.0),
                        ("-720", -720.0), ("-3*", -3.0), ("199", 199.0), ("85.5", 85.5)):
            with self.subTest(text=text):
                self.assertEqual(classify_index_text(text), ("ok", v))

    def test_no_data(self):
        for text in ("-", "未", "未経験", "", None, "−"):
            with self.subTest(text=text):
                self.assertEqual(classify_index_text(text)[0], "none")

    def test_errors(self):
        for text in ("200", "1000", "1083", "abc", "8 3x", "**"):
            with self.subTest(text=text):
                self.assertEqual(classify_index_text(text)[0], "error")


class SpeedCellTest(unittest.TestCase):
    def test_visible_index_is_used_not_hidden_sort_value(self):
        self.assertEqual(speed_cell_value(cell("1083", "83")), ("ok", "83"))
        self.assertEqual(speed_cell_value(cell("1072", "72*")), ("ok", "72*"))
        self.assertEqual(speed_cell_value(cell("996", "-4")), ("ok", "-4"))

    def test_no_data_is_blank(self):
        for visible in ("-", "未", ""):
            with self.subTest(visible=visible):
                self.assertEqual(speed_cell_value(cell("1000", visible)), ("none", ""))

    def test_error_is_not_blanked(self):
        self.assertEqual(speed_cell_value(cell("", "1000")), ("error", "1000"))

    def test_none_cell(self):
        self.assertEqual(speed_cell_value(None), ("none", ""))

    def test_original_cell_not_modified(self):
        c = cell("1083", "83")
        speed_cell_value(c)
        self.assertIsNotNone(c.find(class_="Sort_Function_Data_Hidden"))


class RankPageTest(unittest.TestCase):
    def test_negative_index_is_not_error(self):
        df = parse_rank_html(fixture("rank_202604040403_course.html"))
        self.assertTrue(_is_valid_speed_df(df))
        self.assertEqual(index_read_errors(df), [])
        vals = dict(zip(df["馬番"], df["新潟芝コース最高指数"]))
        self.assertEqual(vals["12"], "-3")
        self.assertEqual(vals["6"], "-4")

    def test_starred_average_is_not_error(self):
        df = parse_rank_html(fixture("rank_202605041007_average.html"))
        self.assertEqual(index_read_errors(df), [])
        self.assertEqual(dict(zip(df["馬番"], df["近5走平均指数"]))["3"], "96*")

    def test_hidden_value_in_table_is_error(self):
        df = parse_rank_html(fixture("rank_202605041007_average.html"))
        df.loc[df["馬番"] == "4", "近5走平均指数"] = "1083"
        self.assertEqual(index_read_errors(df), ["1083"])
        self.assertEqual(index_read_errors(None), [])


class SubmenuPageTest(unittest.TestCase):
    def test_same_ranking_as_rank_page(self):
        """代わりの取り方で作った順位表が、ランキングページ（当該コース）と同じになる"""
        rank = parse_rank_html(fixture("rank_202604040403_course.html"))
        rank = rank[rank["順位"].str.match(r"^\d+位$")]
        sub = parse_speed_shutuba_html(fixture("submenu_202604040403.html"), "202604040403")["course"]
        sub = sub[sub["順位"] != "-"]
        self.assertEqual(list(zip(sub["順位"], sub["馬番"], sub["指数"])),
                         list(zip(rank["順位"], rank["馬番"], rank["新潟芝コース最高指数"])))

    def test_star_kept_as_on_page(self):
        """近走平均の「*」は画面どおり残す（ランキングページ経由と同じ扱いにするため）"""
        avg = parse_speed_shutuba_html(fixture("submenu_202605041007.html"), "202605041007")["average"]
        self.assertEqual(dict(zip(avg["馬番"], avg["指数"]))["3"], "96*")
        self.assertEqual(len(avg), 11)

    def test_error_drops_only_that_mode(self):
        soup = BeautifulSoup(fixture("submenu_202605041007.html"), "html.parser")
        c = soup.find(class_="sk__average_index")
        for hidden in c.find_all(class_="Sort_Function_Data_Hidden"):
            hidden.decompose()
        c.string = "1083"  # 隠し値と同じ形の値が画面側に出た場合
        res = parse_speed_shutuba_html(str(soup), "202605041007")
        self.assertNotIn("average", res)
        self.assertIn("distance", res)
        self.assertIn("course", res)


if __name__ == "__main__":
    unittest.main()
