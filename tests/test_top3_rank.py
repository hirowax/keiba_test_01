"""
3指数のトップ3・サマリー・単指数1位を「ページの順位」で決める処理の回帰テスト
（scraper.ranked_horses / top_rank_horses / find_triple_top5_rows / build_summary、export_json.index_number）。

経緯: docs/index_top3_definition_202611.md（残りのタスク7）。
2026-11-02 まで、近走平均の「96*」（5走分のデータが無い馬）は数字として読めずに候補から外れ、
同じ値の馬は並べた順で3頭に切られていた。

実ページの例は tests/fixtures/ の東京7R（10/31）だけ。「3位に複数頭」「1位に複数頭」「順位の食い違い」は
手元に実ページが無いので自作の表で確かめる。サイトの表示（index.html）はテストが無い。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_pages import read_fixture  # noqa: E402
from export_json import index_number  # noqa: E402
from scraper import (  # noqa: E402
    build_summary, find_triple_top5_rows, index_read_errors, parse_rank_html,
    parse_speed_shutuba_html, rank_notes, ranked_horses, top_rank_horses,
)

COLS = ["順位", "枠", "馬番", "印", "馬名", "テスト最高指数"]


def table(rows, subheader=("着度数", "勝率", "連対率", "複勝率", "", "")):
    """rows: [(順位, 馬番, 指数)]。type=rank と同じ形（2段目の見出し行つき）"""
    data = [list(subheader)] if subheader else []
    data += [[r, "", n, "", f"馬{n}", v] for r, n, v in rows]
    return pd.DataFrame(data, columns=COLS)


def nums(horses):
    return [h["馬番"] for h in horses]


class TopRankTest(unittest.TestCase):
    def test_real_page_starred_horse_is_first(self):
        """10/31 東京7R 近走平均: 96* の馬が1位で入り、同じ83の2位2頭も入る"""
        df = parse_rank_html(read_fixture("rank_202605041007_average.html"))
        top = top_rank_horses(df, 3)
        self.assertEqual([(h["順位"], h["馬番"], h["指数"]) for h in top],
                         [(1, "3", "96*"), (2, "2", "83"), (2, "4", "83")])

    def test_tie_at_first(self):
        """100・100・90・80 → 1・1・3・4。トップ3は最初の3頭"""
        df = table([("1位", "1", "100"), ("1位", "2", "100"), ("3位", "3", "90"), ("4位", "4", "80")])
        self.assertEqual(nums(top_rank_horses(df, 3)), ["1", "2", "3"])

    def test_three_way_tie_excludes_fourth(self):
        """100・100・100・90 → 1・1・1・4。90 の馬は入らない"""
        df = table([("1位", "1", "100"), ("1位", "2", "100"), ("1位", "3", "100"), ("4位", "4", "90")])
        self.assertEqual(nums(top_rank_horses(df, 3)), ["1", "2", "3"])

    def test_tie_at_third_includes_all(self):
        """100・90・80・80・70 → 1・2・3・3・5。トップ3は4頭"""
        df = table([("1位", "1", "100"), ("2位", "2", "90"), ("3位", "3", "80"),
                    ("3位", "4", "80*"), ("5位", "5", "70")])
        self.assertEqual(nums(top_rank_horses(df, 3)), ["1", "2", "3", "4"])

    def test_zero_and_negative(self):
        df = table([("1位", "1", "5"), ("2位", "2", "0"), ("3位", "3", "-4"), ("4位", "4", "-720")])
        self.assertEqual(nums(top_rank_horses(df, 3)), ["1", "2", "3"])
        self.assertEqual(index_read_errors(df), [])

    def test_no_data_rows_excluded(self):
        df = table([("1位", "1", "80"), ("-", "2", "未経験"), ("-", "3", "-")])
        self.assertEqual(nums(top_rank_horses(df, 3)), ["1"])
        self.assertEqual(index_read_errors(df), [])
        self.assertEqual(rank_notes(df), [])

    def test_only_fixed_subheaders_are_skipped(self):
        """決まった形の見出し行（5走前・着度数）だけ読み飛ばす。それ以外の順位は取得異常"""
        ok = table([("1位", "1", "80")], subheader=("5走前", "4走前", "3走前", "2走前", "前走", ""))
        self.assertEqual(index_read_errors(ok), [])
        bad = table([("1位", "1", "80"), ("abc", "2", "70")])
        self.assertEqual(index_read_errors(bad), ["順位が不正: 'abc'"])
        with self.assertRaises(ValueError):
            top_rank_horses(bad, 3)

    def test_duplicate_umaban_is_error(self):
        df = table([("1位", "1", "80"), ("2位", "1", "70")])
        self.assertEqual(index_read_errors(df), ["馬番の重複: ['1']"])

    def test_hidden_value_is_error(self):
        df = table([("1位", "1", "1080"), ("2位", "2", "70")])
        self.assertEqual(index_read_errors(df), ["1080"])

    def test_page_rank_mismatch_below_top3_is_note_only(self):
        """ページ自体の食い違い（保存済みで6表。指数0の馬の順位など）が下位なら、表は使う"""
        df = table([("1位", "1", "80"), ("2位", "2", "70"), ("3位", "3", "60"), ("4位", "4", "21"),
                    ("4位", "5", "0"), ("6位", "6", "未経験")])
        self.assertEqual(index_read_errors(df), [])
        self.assertEqual(len(rank_notes(df)), 2)
        self.assertEqual(nums(top_rank_horses(df, 3)), ["1", "2", "3"])

    def test_page_rank_mismatch_in_top3_raises(self):
        df = table([("1位", "1", "80"), ("1位", "2", "70"), ("3位", "3", "60")])
        with self.assertRaises(ValueError):
            top_rank_horses(df, 3)
        unranked = table([("1位", "1", "80"), ("-", "2", "90")])  # 順位「-」なのに値は1位
        with self.assertRaises(ValueError):
            top_rank_horses(unranked, 3)

    def test_submenu_matches_rank_page_average(self):
        """代わりの取り方（shutuba_submenu）の順位が、ランキングページ（近走平均）と同じ"""
        rank = ranked_horses(parse_rank_html(read_fixture("rank_202605041007_average.html")))[0]
        sub = parse_speed_shutuba_html(read_fixture("submenu_202605041007.html"),
                                       "202605041007")["average"]
        key = lambda hs: [(h["順位"], h["馬番"], h["指数"]) for h in hs]
        self.assertEqual(key(ranked_horses(sub)[0]), key(rank))


class TripleAndSummaryTest(unittest.TestCase):
    def dfs(self):
        avg = table([("1位", "1", "96*"), ("2位", "2", "83"), ("2位", "3", "83"), ("4位", "4", "80")],
                    subheader=("5走前", "4走前", "3走前", "2走前", "前走", ""))
        dist = table([("1位", "3", "102"), ("2位", "1", "98"), ("3位", "4", "91"), ("3位", "2", "91")])
        crs = table([("1位", "2", "96"), ("2位", "3", "93"), ("3位", "1", "89"), ("4位", "4", "87")])
        return {"average": avg, "distance": dist, "course": crs}

    def test_triple_by_page_rank(self):
        """1番（96*で近走平均1位）と、3位タイで当該距離に入る2番も候補になる。4番は近走平均4位で入らない"""
        rows = find_triple_top5_rows("東京7R", self.dfs())
        self.assertEqual([r["馬番"] for r in rows], ["1", "2", "3"])
        r1 = rows[0]
        self.assertEqual((r1["近走平均指数"], r1["近走平均順位"], r1["当該距離順位"]), ("96*", "1位", "2位"))
        self.assertEqual(rows[1]["当該距離順位"], "3位")

    def test_triple_matches_by_umaban_not_name(self):
        d = self.dfs()
        d["course"].loc[d["course"]["馬番"] == "1", "馬名"] = "馬2"  # 同じ名前の別の馬
        self.assertEqual([r["馬番"] for r in find_triple_top5_rows("東京7R", d)], ["1", "2", "3"])

    def test_missing_mode_gives_no_candidates(self):
        d = self.dfs()
        d["course"] = None
        self.assertEqual(find_triple_top5_rows("東京7R", d), [])

    def test_summary_uses_same_rule(self):
        d = self.dfs()
        s = build_summary(d, "東京7R")
        sections = s["セクション"].tolist()
        i_avg = sections.index("【近走平均 トップ5】")
        i_dist = sections.index("【当該距離 トップ5】")
        avg_rows = s.iloc[i_avg + 1:i_dist]
        self.assertEqual(avg_rows["馬番"].tolist(), nums(top_rank_horses(d["average"], 5)))
        self.assertEqual(avg_rows["順位"].tolist(), ["1位", "2位", "2位", "4位"])
        self.assertEqual(avg_rows["近走平均"].tolist()[0], "96*")
        i_tri = sections.index("【3指数すべてトップ3入り馬】")
        self.assertEqual(s.iloc[i_tri + 1:]["馬番"].tolist(),
                         [r["馬番"] for r in find_triple_top5_rows("東京7R", d)])

    def test_summary_marks_unusable_mode(self):
        d = self.dfs()
        d["course"] = table([("1位", "1", "80"), ("1位", "2", "70")])  # 上位で食い違い
        s = build_summary(d, "東京7R")
        self.assertIn("順位を使えない", s["セクション"].tolist())


class ExportTest(unittest.TestCase):
    def test_index_number(self):
        for v, want in (("96*", 96.0), ("83", 83.0), (83, 83), ("-4", -4.0), ("", ""), ("未経験", "")):
            with self.subTest(v=v):
                self.assertEqual(index_number(v), want)


if __name__ == "__main__":
    unittest.main()
