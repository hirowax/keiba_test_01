"""
馬ページの成績表の読み取り（prev_inputs.parse_results_table）を、実ページの保存で確かめる回帰テスト。

fixtures: tests/fixtures/horse_{馬ID}_results_20261105.html
- 2026-11-05 10:14 に db.netkeiba.com/horse/{馬ID}/ を開き、table.db_h_race_results だけを保存した。
- 保存したときの cookies は期限切れ（プリフライトでログアウト状態と判定）。各馬の最新の行だけ指数が数字で、
  それより前の行は「**」（プレミアム登録の案内へのリンク）だった。
- horse_2022100028_results_20261105_login.html は、10:23 にログインし直して（プリフライト通過）同じ馬を保存したもの。
  「ﾀｲﾑ指数」は全行が数字。「**」は「ﾀｲﾑ指数M」（非表示のタイム指数マスター）の列だけに残る。
- 期待値は output/20261101/race_results_all.json（11/1 の結果の取り直し）と照合した値:
  レースID・着順・人気・距離・競走中止。指数（90・93・69）は結果のファイルに無いので、ページの表示のまま。
- 4頭とも、ページの「戦績」の出走数と表の行数が同じ（10・15・4・1）。表は途中で切れていない。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_pages import read_fixture  # noqa: E402
import prev_inputs as pi  # noqa: E402



def parse(hid, target, suffix=""):
    return pi.parse_results_table(read_fixture(f"horse_{hid}_results_20261105{suffix}.html"), target)


class RealPageTest(unittest.TestCase):
    def test_headers_found_by_visible_text(self):
        e = parse("2022105962", "20261107")
        self.assertEqual(e["headers"][20:22], ["ﾀｲﾑ指数", "ﾀｲﾑ指数M"])   # 切り替えの一覧（非表示）の文字は除く
        self.assertEqual(len(e["headers"]), 33)

    def test_ok_latest_row(self):
        """東京7R 11/1・4番・3着・1番人気・ダ1600（race_results_all と一致）"""
        e = parse("2022105962", "20261107")
        self.assertEqual(e["status"], pi.OK)
        self.assertEqual(e["values"], {"date": "20261101", "race_id": "202605041107", "pop": 1, "rank": 3,
                                       "dist": 1600, "surface_dist": "ダ1600", "idx": 90})

    def test_ok_two_digit_rank(self):
        """東京12R 11/1・11着・8番人気（race_results_all と一致）"""
        e = parse("2022100028", "20261107")
        self.assertEqual(e["status"], pi.OK)
        self.assertEqual((e["values"]["race_id"], e["values"]["rank"], e["values"]["pop"], e["values"]["idx"]),
                         ("202605041112", 11, 8, 93))

    def test_stars_when_logged_out(self):
        """ログアウト状態では、最新より前の行の指数は「**」（プレミアム登録の案内へのリンク）→ 取得失敗"""
        e = parse("2022100028", "20261101")
        self.assertEqual(e["status"], pi.FAILED)
        self.assertIsNone(e["values"])
        self.assertEqual(e["row"]["date"], "2026/07/25")
        self.assertIn("pid=premium", e["row"]["idx_link"])

    def test_logged_in_shows_numbers(self):
        """ログインした状態では、同じ行（7/25 札幌12R）の指数が数字（74）。「**」はタイム指数マスターの列だけ"""
        e = parse("2022100028", "20261101", suffix="_login")
        self.assertEqual(e["status"], pi.OK)
        self.assertEqual((e["values"]["date"], e["values"]["race_id"], e["values"]["idx"]),
                         ("20260725", "202601010112", 74))

    def test_chushi_is_idx_empty(self):
        """京都4R 11/1・競走中止（race_results_all の others に「中止」）。指数は空欄"""
        e = parse("2023105659", "20261107")
        self.assertEqual(e["status"], pi.IDX_EMPTY)
        self.assertEqual(e["values"], {"date": "20261101", "race_id": "202608041104", "pop": 9, "rank": None,
                                       "dist": 2910, "surface_dist": "障2910", "idx": None})

    def test_debut_then_no_prev(self):
        """京都5R 11/1 の新馬戦が初出走。11/7 なら新馬戦の行。
        11/1 を対象日にすると対象日より前の行が無い。2026-11-13 からは、この場合もページ（馬ID・馬名・見出し）を確かめてから
        前走なしにする。この保存ファイルは成績の表だけを切り出したものでページを確かめられないので、取得失敗になる"""
        e = parse("2024107034", "20261101")
        self.assertEqual(e["status"], pi.FAILED)
        self.assertIn("前走なしとは確かめられない", e["reason"])
        e = parse("2024107034", "20261107")
        self.assertEqual((e["status"], e["values"]["race_id"], e["values"]["rank"], e["values"]["idx"]),
                         (pi.OK, "202608041105", 1, 69))

    def test_target_day_row_is_not_selected(self):
        """対象日当日の行は選ばない（11/1 を対象日にすると、11/1 の行ではなくその前の行）"""
        self.assertEqual(parse("2022100028", "20261101", suffix="_login")["values"]["date"], "20260725")
        self.assertEqual(parse("2022105962", "20261101")["row"]["date"], "2026/10/03")


if __name__ == "__main__":
    unittest.main()
