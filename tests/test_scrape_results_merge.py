"""
結果の取得（scrape_results.py。2026-11-12〜）の、通過順の読み取り・形の異常・レースごとの置き換え・取得の記録・
予定を確定できなかった日の取り直しの回帰テスト。設計: docs/horse_style_design_202611.md 2章・7章。

ネットワークには出ない（JRA 公式の取り直しは差し替える）。本番の夜の流れ（取得・通知・commit）は、本番の最初の夜に確かめる（設計 5章の3b）。

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
import scrape_results as SR  # noqa: E402
import schedule_check as S  # noqa: E402


class ParseTest(unittest.TestCase):
    def test_passage_rate_and_scratched_row(self):
        r = SR.parse_result_html(read_fixture("result_202605041106_torikeshi.html"), "東京6R")
        self.assertEqual(r["problems"], [])
        first = r["horses"][0]
        self.assertEqual((first["num"], first["corners"], first["corners_text"]), ("12", [13, 13], "13-13"))
        self.assertEqual([(o["status"], o["corners"], o["corners_text"]) for o in r["others"]], [("取消", [], "")])

    def test_missing_passage_cell_is_a_problem(self):
        html = read_fixture("result_202605041106_torikeshi.html").replace('class="PassageRate"', 'class="X"', 1)
        r = SR.parse_result_html(html, "東京6R")
        self.assertTrue(any("td.PassageRate" in p for p in r["problems"]))

    def test_no_table_is_a_problem(self):
        r = SR.parse_result_html("<html><body>メンテナンス中</body></html>", "東京6R")
        self.assertIn("成績の表が無い", r["problems"])
        self.assertIn("着順のある馬が0頭", r["problems"])


def race(t, rank_text="1"):
    return {"race_id": "x", "condition": {"race_id": "x"}, "horses": [{"rank": 1, "corners": [1]}], "others": [],
            "fetched_at": t, "scrape_version": SR.SCRAPE_VERSION}


class MergeTest(unittest.TestCase):
    IDS = {"東京1R": "202605010101", "東京2R": "202605010102"}

    def test_failed_race_keeps_old_and_complete_means_saved_set(self):
        old = {"date": "d", "complete": True, "race_ids": self.IDS,
               "races": {"東京1R": race("old1"), "東京2R": race("old2")}}
        data, kept = SR.merge_all_results(old, "d", self.IDS, {"東京1R": race("new1")})
        self.assertEqual(data["races"]["東京1R"]["fetched_at"], "new1")
        self.assertEqual(data["races"]["東京2R"]["fetched_at"], "old2")   # 消さない・空にしない
        self.assertEqual(kept, {"東京2R": "old2"})
        self.assertTrue(data["complete"])   # 今回の失敗があっても、保存済みの集合はそろっている

    def test_first_time_partial_is_not_complete(self):
        data, kept = SR.merge_all_results(None, "d", self.IDS, {"東京1R": race("new1")})
        self.assertFalse(data["complete"])
        self.assertEqual(kept, {})

    def test_one_race_replaced_as_a_set(self):
        old = {"races": {"東京1R": {**race("old"), "others": [{"status": "中止"}], "condition": {"pace": "S"}}}}
        new = race("new")
        data, _ = SR.merge_all_results(old, "d", {"東京1R": "202605010101"}, {"東京1R": new})
        self.assertEqual(data["races"]["東京1R"], new)   # 着順・着順の無い馬・条件を一組で置き換える


class FetchLogAndRetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_fetch_log_appends(self):
        SR.append_fetch_log(self.tmp, {"at": "1", "failed": {"東京2R": "x"}})
        SR.append_fetch_log(self.tmp, {"at": "2", "failed": {}})
        log = json.loads((self.tmp / SR.FETCH_LOG_NAME).read_text())
        self.assertEqual([a["at"] for a in log["attempts"]], ["1", "2"])

    def test_retry_only_unconfirmed_days_with_check_and_returns_changed_files(self):
        ids = {f"東京{n}R": f"2026050101{n:02d}" for n in range(1, 13)}
        for d, status in (("20261107", S.UNCONFIRMED), ("20261108", S.CONFIRMED), ("20261101", None)):
            p = self.tmp / d / SR.RESULTS_ALL_NAME
            p.parent.mkdir(parents=True)
            body = {"date": d, "complete": True, "race_ids": ids, "races": {}}
            if status:
                body["schedule_check"] = {"status": status}
            p.write_text(json.dumps(body, ensure_ascii=False))
        cache = {"srl": {"20261107_05": {"cname": "x"}},
                 "sde": {r: {"cname": "x", "date": "20261107"} for r in ids.values()}}
        with mock.patch.object(SR, "refresh_jra_lists") as refresh:
            changed = SR.retry_unconfirmed_schedules("20261114", cache, self.tmp)
        refresh.assert_called_once_with("20261107", cache)   # 確定の日・schedule_check の無い日は取り直さない
        self.assertEqual(sorted(Path(c).name for c in changed), sorted([SR.FETCH_LOG_NAME, SR.RESULTS_ALL_NAME]))
        data = json.loads((self.tmp / "20261107" / SR.RESULTS_ALL_NAME).read_text())
        self.assertEqual(data["schedule_check"]["status"], S.CONFIRMED)

    def test_retry_failure_is_recorded(self):
        p = self.tmp / "20261107" / SR.RESULTS_ALL_NAME
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"race_ids": {"東京1R": "202605010101"}, "schedule_check": {"status": S.UNCONFIRMED}}))
        with mock.patch.object(SR, "refresh_jra_lists", side_effect=RuntimeError("つながらない")):
            SR.retry_unconfirmed_schedules("20261114", {"srl": {}, "sde": {}}, self.tmp)
        data = json.loads(p.read_text())
        self.assertEqual(data["schedule_check"]["status"], S.UNCONFIRMED)
        self.assertIn("つながらない", data["schedule_check"]["jra_error"])

    def test_schedule_check_for_marks_missing_scored_race(self):
        ids = {f"東京{n}R": f"2026050101{n:02d}" for n in range(1, 13)}
        cache = {"srl": {"20261101_05": {}}, "sde": {r: {"date": "20261101"} for r in ids.values()}}
        self.assertEqual(SR.schedule_check_for("20261101", ids, cache, None)["status"], S.CONFIRMED)
        sc = SR.schedule_check_for("20261101", ids, cache, None, "採点したレースがレース一覧に無い: ['東京13R']")
        self.assertEqual(sc["status"], S.UNCONFIRMED)


if __name__ == "__main__":
    unittest.main()
