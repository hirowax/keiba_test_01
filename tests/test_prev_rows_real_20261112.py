"""
前走が障害戦の馬・前走の着順「取」・初出走の馬の扱い（2026-11-12）を、実ページの保存で確かめる回帰テスト。

fixtures: tests/fixtures/horse_{馬ID}_20261112.html（手元だけ。公開しないので git に入れない。無ければ飛ばす）
- 2026-11-12 09:29〜09:41 に db.netkeiba.com/horse/{馬ID}/ を、本番と同じ cookies・設定で開いて丸ごと保存したもの。
  取得日時・URL・SHA-256 は research/prev_rows_check_20261112/fetch_log.jsonl（同じページの写し）。
- 馬名は本番の出馬表の馬名（output/2026110{7,8}/pickup_scores.json の horse_name_full）、初出走の2頭は 11/14 東京5R の
  出馬表の馬へのリンクの文字（race_pickup.parse_shutuba_rows と同じ取り方）、取消の4頭は結果の記録（race_results_all.json）の馬名。
- 期待値の照らし合わせ:
  - 障害戦の3頭: 本番の前走データの記録（output/2026110{7,8}/prev_inputs/）の元の文字（日付・レースID・着順・人気・距離）と同じ。
  - 取消の後の行（2023100582 の 9/20 中山7R・2022106199 の 10/12 東京7R）: race_results_all.json の着順・人気と一致。
  - 取消の行のレースID: race_results_all.json で、その馬が「取消」のレース。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_pages import read_fixture  # noqa: E402
import prev_inputs as pi  # noqa: E402


def parse(hid, target, name):
    return pi.parse_results_table(read_fixture(f"horse_{hid}_20261112.html"), target, hid, name)


class JumpRealTest(unittest.TestCase):
    def test_three_horses(self):
        cases = [("2020100233", "20261107", "セイウンパシュート", ("20261003", "202605040104", 8, 2, "障3100")),
                 ("2019101351", "20261108", "セキテイオー", ("20260627", "202603020101", 7, 5, "障2750")),
                 ("2022106999", "20261108", "ニシノクードクール", ("20260830", "202604030401", 12, 9, "障2850"))]
        for hid, target, name, (d, rid, rank, pop, dist) in cases:
            e = parse(hid, target, name)
            self.assertEqual(e["status"], pi.JUMP_IDX_EMPTY, hid)
            v = e["values"]
            self.assertEqual((v["date"], v["race_id"], v["rank"], v["pop"], v["surface_dist"], v["idx"]),
                             (d, rid, rank, pop, dist, None), hid)

    def test_wrong_name_is_failed(self):
        self.assertEqual(parse("2020100233", "20261107", "セキテイオー")["status"], pi.FAILED)


class DebutRealTest(unittest.TestCase):
    def test_debut_horses(self):
        for hid, name in (("2024107091", "アンジュオレンジ"), ("2024105797", "チャモクラウン")):
            self.assertEqual(parse(hid, "20261114", name)["status"], pi.NO_PREV, hid)

    def test_wrong_name_or_nonexistent_id_is_failed(self):
        self.assertEqual(parse("2024107091", "20261114", "チャモクラウン")["status"], pi.FAILED)
        e = parse("0000000000", "20261114", "アンジュオレンジ")
        self.assertEqual(e["status"], pi.FAILED)
        self.assertIn("成績の表が無い", e["reason"])


class ScratchRealTest(unittest.TestCase):
    def test_scratch_row_skipped_and_previous_run_used(self):
        cases = [("2023100582", "20261025", "ソロンゴ", "202604040304", ("20260920", "202606040607", 15, 13)),
                 ("2022106199", "20261101", "タマモトリノ", "202605041006", ("20261012", "202605040507", 13, 2))]
        for hid, target, name, scratched_rid, (d, rid, rank, pop) in cases:
            e = parse(hid, target, name)
            self.assertEqual(e["status"], pi.OK, hid)
            self.assertEqual([(x["rank"], x["race_id"]) for x in e["skipped_rows"]], [("取", scratched_rid)])
            v = e["values"]
            self.assertEqual((v["date"], v["race_id"], v["rank"], v["pop"]), (d, rid, rank, pop), hid)

    def test_only_scratch_row_is_no_prev(self):
        for hid, target, name in (("2024102799", "20261102", "サルサベガ"), ("2024106490", "20261018", "ジェットシェヴロン")):
            e = parse(hid, target, name)
            self.assertEqual(e["status"], pi.NO_PREV, hid)
            self.assertEqual(e["selected_reason"], "対象日より前に実際に出走した行が無い")
            self.assertEqual([x["rank"] for x in e["skipped_rows"]], ["取"])


if __name__ == "__main__":
    unittest.main()
