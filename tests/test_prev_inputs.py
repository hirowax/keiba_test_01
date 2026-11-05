"""
前走データの取得・記録・採点への受け渡し（prev_inputs.py）と、再実行の対象（rerun_failed_pickup.needs_rerun）の回帰テスト。
設計: docs/prev_cache_fix_plan_202611.md（2026-11-05 決定）

表の読み取りは、見出しの文字を仮に入れた作り物の表で、境界や異常の形を試す。
実ページの保存（tests/fixtures/horse_*_results_20261105.html）を使うテストは tests/test_prev_inputs_real.py。

実行: python3 -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import prev_inputs as pi  # noqa: E402

FAKE_HEADERS = {"date": "日付", "race_name": "レース名", "pop": "人気", "rank": "着順", "dist": "距離", "idx": "指数"}
COLS = ["日付", "開催", "レース名", "人気", "着順", "距離", "指数"]


def table(rows, cols=COLS, cls="db_h_race_results"):
    """rows: [(日付, レースID, 人気, 着順, 距離, 指数)]"""
    head = "<tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>"
    body = ""
    for d, rid, pop, rank, dist, idx in rows:
        body += (f"<tr><td>{d}</td><td>5東京2</td><td><a href=\"/race/{rid}/\">テストS</a></td>"
                 f"<td>{pop}</td><td>{rank}</td><td>{dist}</td><td class=\"{pi.IDX_CELL_CLASS}\">{idx}</td></tr>")
    return f"<html><body><table class=\"{cls}\"><thead>{head}</thead><tbody>{body}</tbody></table></body></html>"


ROWS = [("2026/11/07", "202605050211", "1", "1", "芝1600", "90"),   # 対象日当日の行（選ばない）
        ("2026/10/12", "202605040411", "3", "2", "芝1800", "85"),   # 対象日より前の最新
        ("2026/09/14", "202606040511", "5", "6", "芝2000", "77")]


@mock.patch.dict(pi.HEADERS, FAKE_HEADERS)
class ParseTest(unittest.TestCase):
    def test_selects_latest_row_before_target(self):
        e = pi.parse_results_table(table(ROWS), "20261107")
        self.assertEqual(e["status"], pi.OK)
        self.assertEqual(e["values"], {"date": "20261012", "race_id": "202605040411", "pop": 3, "rank": 2,
                                       "dist": 1800, "surface_dist": "芝1800", "idx": 85})

    def test_row_order_does_not_matter(self):
        e = pi.parse_results_table(table(list(reversed(ROWS))), "20261107")
        self.assertEqual(e["values"]["date"], "20261012")

    def test_zero_and_negative_idx_are_valid(self):
        for v in ("0", "-12"):
            rows = [("2026/10/12", "202605040411", "3", "2", "芝1800", v)]
            e = pi.parse_results_table(table(rows), "20261107")
            self.assertEqual((e["status"], e["values"]["idx"]), (pi.OK, int(v)))

    def test_idx_200_or_more_is_failed(self):
        rows = [("2026/10/12", "202605040411", "3", "2", "芝1800", "1085")]
        self.assertEqual(pi.parse_results_table(table(rows), "20261107")["status"], pi.FAILED)

    def test_idx_stars_is_failed(self):
        """「**」はログインしていない状態の表示なので取得失敗（2026-11-05 決定）。値は使わない"""
        rows = [("2026/10/12", "202605040411", "3", "2", "芝1800", "**")]
        e = pi.parse_results_table(table(rows), "20261107")
        self.assertEqual(e["status"], pi.FAILED)
        self.assertIsNone(e["values"])
        self.assertIn("ログインしていない", e["reason"])

    def test_idx_blank_or_other_is_failed(self):
        for v in ("", "-", "未"):
            rows = [("2026/10/12", "202605040411", "3", "2", "芝1800", v)]
            self.assertEqual(pi.parse_results_table(table(rows), "20261107")["status"], pi.FAILED, v)

    def test_idx_blank_with_nonnumeric_rank_is_idx_empty(self):
        """競走中止などで指数が空（2026-11-05 の実ページ確認で足した状態）。着順が数字なら取得失敗のまま"""
        rows = [("2026/10/12", "202605040411", "9", "中", "障2910", "")]
        e = pi.parse_results_table(table(rows), "20261107")
        self.assertEqual((e["status"], e["values"]["rank"], e["values"]["pop"], e["values"]["idx"]),
                         (pi.IDX_EMPTY, None, 9, None))

    def test_idx_cell_without_class_is_failed(self):
        html = table(ROWS).replace(f' class="{pi.IDX_CELL_CLASS}"', "", 1)
        self.assertEqual(pi.parse_results_table(html, "20261107")["status"], pi.FAILED)

    def test_cancelled_rank_is_kept_as_none(self):
        """前走が中止など（着順が数字でない）は正当な値。着順・人気は None"""
        rows = [("2026/10/12", "202605040411", "", "中止", "芝1800", "50")]
        e = pi.parse_results_table(table(rows), "20261107")
        self.assertEqual((e["status"], e["values"]["rank"], e["values"]["pop"]), (pi.OK, None, None))

    # ── 前走なしの条件（すべてを満たすときだけ。満たさなければ取得失敗）──
    def test_no_prev_when_no_rows(self):
        self.assertEqual(pi.parse_results_table(table([]), "20261107")["status"], pi.NO_PREV)

    def test_no_prev_when_all_rows_on_or_after_target(self):
        self.assertEqual(pi.parse_results_table(table(ROWS[:1]), "20261107")["status"], pi.NO_PREV)

    def test_unreadable_date_is_failed_not_no_prev(self):
        rows = [("2026/11/07", "202605050211", "1", "1", "芝1600", "90"), ("不明", "x", "", "", "", "")]
        self.assertEqual(pi.parse_results_table(table(rows), "20261107")["status"], pi.FAILED)

    def test_no_table_is_failed(self):
        self.assertEqual(pi.parse_results_table("<html><body>ログインしてください</body></html>", "20261107")["status"],
                         pi.FAILED)

    def test_missing_header_is_failed(self):
        cols = [c for c in COLS if c != "指数"]
        html = table([], cols=cols)
        self.assertEqual(pi.parse_results_table(html, "20261107")["status"], pi.FAILED)

    def test_row_cell_count_mismatch_is_failed(self):
        html = table(ROWS).replace("<td>5東京2</td>", "", 1)   # 途中で切れた行
        self.assertEqual(pi.parse_results_table(html, "20261107")["status"], pi.FAILED)

    def test_duplicate_date_before_target_is_failed(self):
        rows = ROWS + [("2026/10/12", "202605040412", "1", "1", "芝1800", "80")]
        self.assertEqual(pi.parse_results_table(table(rows), "20261107")["status"], pi.FAILED)

    def test_bad_distance_is_failed(self):
        rows = [("2026/10/12", "202605040411", "3", "2", "芝", "85")]
        self.assertEqual(pi.parse_results_table(table(rows), "20261107")["status"], pi.FAILED)


class HeadersNotSetTest(unittest.TestCase):
    def test_unset_headers_fail(self):
        """見出しの文字を実ページで確かめるまでは、全部取得失敗にする（位置で読まない）"""
        with mock.patch.dict(pi.HEADERS, {k: None for k in pi.HEADERS}):
            self.assertEqual(pi.parse_results_table(table(ROWS), "20261107")["status"], pi.FAILED)


def entry(status, target="20261107", fetched="2026-11-06T17:30:00+09:00", idx=85, version=None):
    v = None
    if status in (pi.OK, pi.IDX_EMPTY):
        v = {"date": "20261012", "race_id": "202605040411", "pop": 3,
             "rank": None if status == pi.IDX_EMPTY else 2, "dist": 1800,
             "surface_dist": "芝1800", "idx": idx if status == pi.OK else None}
    return {"status": status, "reason": "", "values": v, "target_date": target, "fetched_at": fetched,
            "fetch_version": version or pi.PREV_FETCH_VERSION, "source": "取得", "horse_id": "H"}


class DeadlineReuseTest(unittest.TestCase):
    def test_deadline_boundary(self):
        self.assertTrue(pi.within_deadline("2026-11-06T23:59:59+09:00", "20261107"))
        self.assertFalse(pi.within_deadline("2026-11-07T00:00:00+09:00", "20261107"))
        self.assertTrue(pi.within_deadline("2026-11-06T14:59:59+00:00", "20261107"))   # = 23:59:59 JST
        self.assertFalse(pi.within_deadline("2026-11-06T15:00:00+00:00", "20261107"))  # = 00:00:00 JST
        self.assertFalse(pi.within_deadline("2026-11-06T17:00:00", "20261107"))        # 時差の無い時刻は使わない
        self.assertFalse(pi.within_deadline(None, "20261107"))

    def test_mark_deadline(self):
        e = pi.mark_deadline(entry(pi.OK, fetched="2026-11-07T08:00:00+09:00"))
        self.assertEqual((e["status"], e["status_observed"]), (pi.AFTER_DEADLINE, pi.OK))
        self.assertIsNone(pi.prev_db_entry(e))   # 採点に使わない
        e = pi.mark_deadline(entry(pi.OK))
        self.assertEqual(e["status"], pi.OK)

    def test_reusable(self):
        for st in (pi.OK, pi.NO_PREV, pi.IDX_EMPTY):
            self.assertTrue(pi.is_reusable(entry(st), "20261107"), st)
        self.assertFalse(pi.is_reusable(entry(pi.FAILED), "20261107"))
        self.assertFalse(pi.is_reusable(entry(pi.AFTER_DEADLINE), "20261107"))
        self.assertFalse(pi.is_reusable(entry(pi.OK, target="20261108"), "20261107"))     # 別の対象日
        self.assertFalse(pi.is_reusable(entry(pi.OK, version="古い版"), "20261107"))      # 別の取得の版
        self.assertFalse(pi.is_reusable(entry(pi.OK, fetched="2026-11-07T00:00:00+09:00"), "20261107"))
        old_cache = {"prev_date": "2026/10/12", "prev_idx": "85", "scraped_at": "20261106"}  # 今までの horse_db の形
        self.assertFalse(pi.is_reusable(old_cache, "20261107"))


class Top1Test(unittest.TestCase):
    def test_all_known(self):
        ents = {"A": entry(pi.OK, idx=80), "B": entry(pi.OK, idx=92), "C": entry(pi.NO_PREV)}
        t = pi.race_top1({"1": "A", "2": "B", "3": "C"}, ents)
        self.assertEqual((t["decision"], t["race_max"]), ("判定", 92))
        self.assertEqual(t["excluded"], {"3": pi.NO_PREV})   # 前走なしは対象外

    def test_idx_empty_is_excluded_not_unknown(self):
        """前走が中止など（指数が空・着順が数字でない）は、値が無いと分かっているので比較の対象外（保留にしない）"""
        ents = {"A": entry(pi.OK, idx=80), "B": entry(pi.IDX_EMPTY)}
        t = pi.race_top1({"1": "A", "2": "B"}, ents)
        self.assertEqual((t["decision"], t["race_max"], t["excluded"]), ("判定", 80, {"2": pi.IDX_EMPTY}))

    def test_unknown_holds(self):
        for st in (pi.FAILED, pi.AFTER_DEADLINE):
            ents = {"A": entry(pi.OK, idx=80), "B": entry(st)}
            t = pi.race_top1({"1": "A", "2": "B"}, ents)
            self.assertEqual((t["decision"], t["race_max"]), ("保留", None), st)

    def test_missing_horse_id_or_input_holds(self):
        ents = {"A": entry(pi.OK, idx=80)}
        self.assertEqual(pi.race_top1({"1": "A", "2": ""}, ents)["decision"], "保留")
        self.assertEqual(pi.race_top1({"1": "A", "2": "B"}, ents)["decision"], "保留")

    def test_only_no_prev(self):
        t = pi.race_top1({"1": "A"}, {"A": entry(pi.NO_PREV)})
        self.assertEqual((t["decision"], t["race_max"]), ("判定", None))

    def test_prev_status_summary(self):
        ents = {"A": entry(pi.OK), "B": entry(pi.FAILED), "C": entry(pi.IDX_EMPTY)}
        active = {"1": "A", "2": "B", "3": "C", "4": "D"}
        t = pi.race_top1(active, ents)
        st = pi.prev_status_summary(["1", "4"], active, ents, t)
        self.assertEqual(st, {"candidates": {"1": pi.OK, "4": "入力が無い"},
                              "others": {pi.FAILED: 1, pi.IDX_EMPTY: 1}, "top1": "保留"})


class ScoreHandoffTest(unittest.TestCase):
    def test_prev_db_entry_shapes(self):
        self.assertEqual(pi.prev_db_entry(entry(pi.OK)),
                         {"prev_date": "2026/10/12", "prev_pop": "3", "prev_rank": "2", "prev_idx": "85",
                          "prev_dist": "芝1800"})
        e = pi.prev_db_entry(entry(pi.IDX_EMPTY))   # 中止など: 日付・人気・距離は使う。指数・着順は空
        self.assertEqual((e["prev_idx"], e["prev_rank"], e["prev_pop"], e["prev_date"]), ("", "", "3", "2026/10/12"))
        for st in (pi.NO_PREV, pi.FAILED, pi.AFTER_DEADLINE):
            self.assertIsNone(pi.prev_db_entry(entry(st)), st)

    def test_score_with_held_top1(self):
        """1位が保留（race_max=None）なら、指数が最大でも1位の加点が付かない"""
        from race_pickup import score_horses
        shutuba = dict(map_issues=None, scratched_nums={}, position_nums=set(), top3_hits={},
                       horse_id_map={"1": "A"}, pop_map={}, odds_map={})
        dt = dict(pickup_nums=set(), analysis_hits={})
        db = {"A": pi.prev_db_entry(entry(pi.OK, idx=92))}
        held = score_horses([{"馬番": "1", "馬名": ""}], shutuba, dt, prev_db=db, race_max_prev_idx=None,
                            race_date="20261107", race_dist=1800)[0]
        top = score_horses([{"馬番": "1", "馬名": ""}], shutuba, dt, prev_db=db, race_max_prev_idx=92,
                           race_date="20261107", race_dist=1800)[0]
        self.assertFalse(any("レース内1位" in b["label"] for b in held["breakdown"]))
        self.assertTrue(any("レース内1位" in b["label"] for b in top["breakdown"]))


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "20261107"
        self.out.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_lock_blocks_second_run_and_close_releases(self):
        r = pi.RunRecord(self.out, "20261107", "test", "v")
        with self.assertRaises(RuntimeError):
            pi.RunRecord(self.out, "20261107", "test", "v")
        r.close()
        r2 = pi.RunRecord(self.out, "20261107", "test", "v")
        r2.close()

    def test_runs_are_separate_files_and_not_overwritten(self):
        r1 = pi.RunRecord(self.out, "20261107", "test", "v")
        r1.add_race("東京1R", "1", {"A": entry(pi.OK)}, {"decision": "判定"})
        r1.close()
        first = json.loads(r1.path.read_text())
        with mock.patch("prev_inputs.now_jst", return_value=pi.now_jst().replace(year=2030)):
            r2 = pi.RunRecord(self.out, "20261107", "test", "v")
        r2.add_race("東京1R", "1", {"A": entry(pi.FAILED)}, {"decision": "保留"})
        r2.close()
        self.assertNotEqual(r1.path, r2.path)
        self.assertEqual(json.loads(r1.path.read_text()), first)   # 前の記録は変わらない
        self.assertFalse(list((self.out / "prev_inputs").glob("*.tmp")))

    def test_reusable_entries_skip_failed_and_keep_origin(self):
        r1 = pi.RunRecord(self.out, "20261107", "test", "v")
        r1.add_race("東京1R", "1", {"A": entry(pi.OK), "B": entry(pi.FAILED)}, {})
        r1.close()
        reuse = pi.reusable_entries(self.out, "20261107")
        self.assertEqual(set(reuse), {"A"})
        self.assertEqual(reuse["A"]["origin_run"], r1.run_id)
        # 再利用した値（source=再利用）を、次の実行が再利用の元にしない（元の実行だけを元にする）
        with mock.patch("prev_inputs.now_jst", return_value=pi.now_jst().replace(year=2030)):
            r2 = pi.RunRecord(self.out, "20261107", "test", "v")
        r2.add_race("東京1R", "1", {"A": dict(reuse["A"], source="再利用")}, {})
        r2.close()
        self.assertEqual(pi.reusable_entries(self.out, "20261107")["A"]["origin_run"], r1.run_id)


class RerunTest(unittest.TestCase):
    def setUp(self):
        from rerun_failed_pickup import needs_rerun
        self.needs = needs_rerun

    def rd(self, cands, others=None):
        return {"pop_map": {"1": "3"}, "scored": [{"score": 3}],
                "quality": {"prev_status": {"candidates": cands, "others": others or {}, "top1": "判定"}}}

    def test_prev_failure_is_rerun_even_with_other_points(self):
        self.assertTrue(self.needs(self.rd({"1": pi.FAILED})))
        self.assertTrue(self.needs(self.rd({"1": "入力が無い"})))
        self.assertTrue(self.needs(self.rd({"1": pi.OK}, {pi.FAILED: 1})))

    def test_normal_statuses_are_not_rerun(self):
        self.assertEqual(self.needs(self.rd({"1": pi.OK, "2": pi.NO_PREV}, {pi.IDX_EMPTY: 2, pi.OK: 5})), [])
        self.assertEqual(self.needs(self.rd({"1": pi.IDX_EMPTY})), [])

    def test_existing_rules(self):
        self.assertTrue(self.needs({"error": "x"}))
        self.assertTrue(self.needs({"pop_map": {}, "scored": [{"score": 0}]}))
        self.assertEqual(self.needs({"pop_map": {"1": "2"}, "scored": [{"score": 0}]}), [])


class SnapshotTest(unittest.TestCase):
    def test_snapshot_only_from_referenced_run(self):
        from run_pickup_all import write_prev_data_snapshot
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "20261107"
            out.mkdir()
            r1 = pi.RunRecord(out, "20261107", "test", "v")
            r1.add_race("東京1R", "1", {"A": entry(pi.OK, idx=70)}, {})
            r1.close()
            with mock.patch("prev_inputs.now_jst", return_value=pi.now_jst().replace(year=2030)):
                r2 = pi.RunRecord(out, "20261107", "test", "v")
            r2.add_race("東京1R", "1", {"A": entry(pi.OK, idx=99), "B": entry(pi.FAILED)}, {})
            r2.close()
            n = write_prev_data_snapshot(out, {"東京1R": {"prev_inputs_run": r1.run_id}})
            snap = json.loads((out / "prev_data.json").read_text())
            self.assertEqual((n, snap["A"]["prev_idx"]), (1, "70"))   # 採点が参照した r1 の値だけ
            write_prev_data_snapshot(out, {"東京1R": {"prev_inputs_run": r2.run_id}})
            snap = json.loads((out / "prev_data.json").read_text())
            self.assertEqual(set(snap), {"A"})   # 取得失敗は載せない


if __name__ == "__main__":
    unittest.main()
