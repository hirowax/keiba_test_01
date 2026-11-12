"""
前走の着順「除」（競走除外）の行を飛ばす扱い（prev_inputs.parse_results_table、2026-11-06 決定・
docs/prev_excluded_rank_plan_202611.md 案1）の回帰テスト。

- 実ページ: tests/fixtures/horse_2020103876_20261106.html（モンドプリューム）、horse_2021104929_20261106.html（ルトゥール）。
  2026-11-06 19:55 にログインした cookies で db.netkeiba.com/horse/{馬ID}/ を開いて保存した。
  どちらも最新の行が「除」（自前の結果の状態は除外）で、その前の行が完走（指数83・79）。
- 自作の表: 「除」が続く、「除」の次が「中」・知らない表記・行なし・古い行、レース内1位の比較相手が直る場合。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_pages import read_fixture  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prev_inputs as pi  # noqa: E402
from test_prev_inputs import FAKE_HEADERS, table  # noqa: E402



def real(hid):
    return pi.parse_results_table(read_fixture(f"horse_{hid}_20261106.html"), "20261107")


class RealPageTest(unittest.TestCase):
    def test_mondo_prieme_skips_excluded_row(self):
        e = real("2020103876")
        self.assertEqual(e["status"], pi.OK, e["reason"])
        self.assertEqual(e["values"], {"date": "20260726", "race_id": e["values"]["race_id"], "rank": 11, "pop": 12,
                                       "dist": 1400, "surface_dist": "ダ1400", "idx": 83})
        self.assertEqual([(x["date"], x["race_id"], x["rank"]) for x in e["skipped_rows"]],
                         [("20260912", "202606040311", "除")])
        self.assertIn("1行飛ばした", e["selected_reason"])

    def test_retour_skips_excluded_row(self):
        e = real("2021104929")
        self.assertEqual(e["status"], pi.OK, e["reason"])
        self.assertEqual((e["values"]["date"], e["values"]["rank"], e["values"]["pop"], e["values"]["dist"],
                          e["values"]["idx"]), ("20260726", 12, 14, 1800, 79))
        self.assertEqual([(x["date"], x["race_id"]) for x in e["skipped_rows"]], [("20261018", "202608040709")])

    def test_target_before_excluded_race_uses_that_day_latest(self):
        """対象日が除外のレースより前なら、除外の行は対象日より後なので見ない（飛ばした行なし）"""
        html = read_fixture("horse_2021104929_20261106.html")
        e = pi.parse_results_table(html, "20261001")
        self.assertEqual((e["status"], e["values"]["date"], e["skipped_rows"]), (pi.OK, "20260726", []))
        self.assertEqual(e["selected_reason"], "対象日より前の最新の行")


@mock.patch.dict(pi.HEADERS, FAKE_HEADERS)
class SkipRuleTest(unittest.TestCase):
    EXCL1 = ("2026/10/18", "202608040709", "", "除", "芝1600", "")
    EXCL2 = ("2026/09/12", "202606040311", "", "除", "ダ1200", "")
    RUN = ("2026/07/26", "202604020311", "12", "11", "ダ1400", "83")

    def parse(self, rows):
        return pi.parse_results_table(table(rows), "20261107")

    def test_consecutive_excluded_rows_are_all_skipped(self):
        e = self.parse([self.EXCL1, self.EXCL2, self.RUN])
        self.assertEqual((e["status"], e["values"]["date"], e["values"]["idx"]), (pi.OK, "20260726", 83))
        self.assertEqual([x["date"] for x in e["skipped_rows"]], ["20261018", "20260912"])

    def test_excluded_then_stopped_race_uses_stopped_race(self):
        """「中」（競走中止）は正式な発走を受けているので前走（指数が空）。さらに古い行へは進まない"""
        e = self.parse([self.EXCL1, ("2026/09/12", "202606040311", "5", "中", "ダ1200", ""), self.RUN])
        self.assertEqual((e["status"], e["values"]["date"], e["values"]["idx"]), (pi.IDX_EMPTY, "20260912", None))

    def test_excluded_then_unknown_spelling_is_failed_without_going_older(self):
        for rank in ("失", "", "不明"):   # 「取」は 2026-11-12 から飛ばす表記（tests/test_prev_rows_20261112.py）
            e = self.parse([self.EXCL1, ("2026/09/12", "202606040311", "", rank, "ダ1200", ""), self.RUN])
            self.assertEqual(e["status"], pi.FAILED, rank)
            self.assertIsNone(e["values"])
            self.assertEqual(len(e["skipped_rows"]), 1)

    def test_excluded_then_unreadable_row_is_failed(self):
        e = self.parse([self.EXCL1, ("2026/09/12", "202606040311", "3", "2", "ダ1200", "**"), self.RUN])
        self.assertEqual(e["status"], pi.FAILED)

    def test_only_excluded_rows_is_failed_not_no_prev(self):
        """飛ばした後に行が無い → ページを確かめられなければ前走なしとは確かめられないので取得失敗（2026-11-06 外部レビュー指摘）。
        ページが正常なら前走なし（2026-11-12。tests/test_prev_rows_20261112.py）。ここは馬ID・馬名を渡さない呼び方"""
        for rows in ([self.EXCL1], [self.EXCL1, self.EXCL2]):
            e = self.parse(rows)
            self.assertEqual(e["status"], pi.FAILED)
            self.assertIn("飛ばした後に行が無く", e["reason"])
            self.assertEqual(len(e["skipped_rows"]), len(rows))

    def test_no_rows_before_target_is_still_no_prev(self):
        e = self.parse([("2026/11/07", "202605050211", "1", "1", "芝1600", "90")])
        self.assertEqual(e["status"], pi.NO_PREV)

    def test_old_previous_run_is_used_as_is(self):
        """除外の前に長い休みがあっても、直近の正式な発走ならそのまま使う（古さの制限は無い）"""
        e = self.parse([self.EXCL1, ("2025/03/01", "202506020111", "2", "1", "ダ1400", "95")])
        self.assertEqual((e["status"], e["values"]["date"], e["values"]["idx"]), (pi.OK, "20250301", 95))

    def test_duplicate_date_after_skip_is_failed(self):
        e = self.parse([self.EXCL1, self.RUN, ("2026/07/26", "202604020312", "3", "1", "ダ1400", "90")])
        self.assertEqual(e["status"], pi.FAILED)
        self.assertIn("同じ日付", e["reason"])

    def test_excluded_row_with_index_is_still_skipped(self):
        """「除」なのに指数がある形は見ていないが、発走していない行として飛ばす（飛ばした行に元の着順を残す）"""
        e = self.parse([("2026/10/18", "202608040709", "", "除", "芝1600", "50"), self.RUN])
        self.assertEqual((e["status"], e["values"]["idx"], e["skipped_rows"][0]["rank"]), (pi.OK, 83, "除"))


@mock.patch.dict(pi.HEADERS, FAKE_HEADERS)
class Top1Test(unittest.TestCase):
    """比較相手の「除」が直ると、レース内1位の判定が保留から判定に変わり、候補の加点が変わる"""

    def entries(self, rival_rows):
        cand = pi.parse_results_table(table([("2026/10/12", "202605040411", "3", "2", "芝1800", "90")]), "20261107")
        rival = pi.parse_results_table(table(rival_rows), "20261107")
        return {"C": cand, "R": rival}

    def test_rival_fixed_lower_gives_candidate_top1(self):
        ents = self.entries([SkipRuleTest.EXCL1, ("2026/07/26", "202604020311", "12", "11", "ダ1400", "83")])
        t = pi.race_top1({"1": "C", "2": "R"}, ents)
        self.assertEqual((t["decision"], t["race_max"]), ("判定", 90))   # 候補（90）が1位 → +2 が付く

    def test_rival_fixed_higher_takes_top1(self):
        ents = self.entries([SkipRuleTest.EXCL1, ("2026/07/26", "202604020311", "1", "1", "ダ1400", "99")])
        t = pi.race_top1({"1": "C", "2": "R"}, ents)
        self.assertEqual((t["decision"], t["race_max"]), ("判定", 99))   # 候補は1位でない

    def test_rival_only_excluded_still_holds(self):
        ents = self.entries([SkipRuleTest.EXCL1])
        t = pi.race_top1({"1": "C", "2": "R"}, ents)
        self.assertEqual((t["decision"], t["unknown"]), ("保留", {"2": pi.FAILED}))


if __name__ == "__main__":
    unittest.main()
