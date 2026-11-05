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

    def test_unconfirmed_nonnumeric_rank_is_failed(self):
        """空・未知の着順は競走中止と確かめた状態ではないので取得失敗（2026-11-05 外部レビュー指摘）"""
        for rank in ("", "不明", "取", "除", "失", "4(降)"):
            for idx in ("", "85"):
                rows = [("2026/10/12", "202605040411", "9", rank, "芝1800", idx)]
                e = pi.parse_results_table(table(rows), "20261107")
                self.assertEqual(e["status"], pi.FAILED, (rank, idx))

    def test_confirmed_rank_with_idx_is_failed(self):
        """「中」なのに指数がある行は、想定と違うので取得失敗"""
        rows = [("2026/10/12", "202605040411", "9", "中", "芝1800", "85")]
        self.assertEqual(pi.parse_results_table(table(rows), "20261107")["status"], pi.FAILED)

    def test_finisher_without_popularity_is_failed(self):
        """着順が数字（完走）なのに人気が空・数字でない → 取得失敗（2026-11-05 外部レビュー指摘）"""
        for pop in ("", "-", "取"):
            rows = [("2026/10/12", "202605040411", pop, "2", "芝1800", "85")]
            self.assertEqual(pi.parse_results_table(table(rows), "20261107")["status"], pi.FAILED, pop)

    def test_idx_cell_without_class_is_failed(self):
        html = table(ROWS).replace(f' class="{pi.IDX_CELL_CLASS}"', "", 1)
        self.assertEqual(pi.parse_results_table(html, "20261107")["status"], pi.FAILED)

    def test_unconfirmed_cancel_spelling_with_idx_is_failed(self):
        """以前は「中止」・人気空・指数あり を「取れた」にしていた（2026-11-05 外部レビュー指摘で取得失敗に変えた）。
        馬ページの競走中止の表記は「中」（実ページで確認）。「中止」は確かめていない表記"""
        rows = [("2026/10/12", "202605040411", "", "中止", "芝1800", "50")]
        self.assertEqual(pi.parse_results_table(table(rows), "20261107")["status"], pi.FAILED)

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

    def test_prev_status_summary_scratched_candidate_is_not_issue(self):
        """出馬表で取消・除外の候補は「取消・除外（出馬表）」。品質の判定の対象外（2026-11-05 外部レビュー指摘）。
        取消の印が無く馬IDも無い候補は「馬IDが無い」（問題あり）"""
        from data_quality import USABLE_PREV_STATUSES
        ents = {"A": entry(pi.OK)}
        active = {"1": "A"}   # 2番は取消（active に無い）、3番は馬IDが無い
        st = pi.prev_status_summary(["1", "2", "3"], active, ents, pi.race_top1(active, ents), scratched={"2": "取消"})
        self.assertEqual(st["candidates"], {"1": pi.OK, "2": pi.SCRATCHED, "3": "馬IDが無い"})
        self.assertIn(pi.SCRATCHED, USABLE_PREV_STATUSES)
        self.assertNotIn("馬IDが無い", USABLE_PREV_STATUSES)

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


def new_run(out, program="test"):
    """ロックを取って記録を作り、記録を閉じてロックを外す（1回の実行の形）"""
    lock = pi.DateLock(out)
    r = pi.RunRecord(out, "20261107", program, "v", lock)
    return lock, r


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "20261107"
        self.out.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_lock_blocks_second_run_until_release(self):
        lock, r = new_run(self.out)
        with self.assertRaises(RuntimeError):
            pi.DateLock(self.out)
        r.finish()   # 記録を閉じてもロックは外れない（結果の保存が終わるまで持つ）
        with self.assertRaises(RuntimeError):
            pi.DateLock(self.out)
        lock.release()
        lock2, r2 = new_run(self.out)
        r2.finish()
        lock2.release()

    def test_record_requires_lock(self):
        with self.assertRaises(RuntimeError):
            pi.RunRecord(self.out, "20261107", "test", "v", None)

    def _hold_lock_in_child(self):
        """別のプロセスでロックを取り、取れたら標準出力に1行出して待ち続ける"""
        import subprocess
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); import prev_inputs as pi; "
                "from pathlib import Path; l = pi.DateLock(Path(sys.argv[2])); print('ok', flush=True); time.sleep(60)")
        child = subprocess.Popen([sys.executable, "-c", code, str(Path(pi.__file__).parent), str(self.out)],
                                 stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), "ok")
        return child

    def test_lock_held_by_other_process_blocks_and_is_freed_by_kill(self):
        """別のプロセスが持つロックでは止まる。そのプロセスを強制終了（SIGKILL）すると OS が外し、次の実行が取れる。
        .lock のファイルは残るが、ロックの判定には使わない（2026-11-06 外部レビュー指摘）"""
        import signal
        child = self._hold_lock_in_child()
        try:
            with self.assertRaises(RuntimeError):
                pi.DateLock(self.out)
        finally:
            child.send_signal(signal.SIGKILL)
            child.wait()
            child.stdout.close()
        self.assertTrue((self.out / "prev_inputs" / ".lock").exists())
        lock = pi.DateLock(self.out)
        lock.release()

    def test_two_processes_cannot_both_lock(self):
        """4つのプロセスが同時にロックを取りに行っても、取れるのは1つだけ"""
        import subprocess
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); import prev_inputs as pi; "
                "from pathlib import Path\n"
                "try:\n l = pi.DateLock(Path(sys.argv[2])); print('ok', flush=True); time.sleep(2)\n"
                "except RuntimeError:\n print('blocked', flush=True)")
        args = [sys.executable, "-c", code, str(Path(pi.__file__).parent), str(self.out)]
        procs = [subprocess.Popen(args, stdout=subprocess.PIPE, text=True) for _ in range(4)]
        outs = sorted(pr.communicate(timeout=30)[0].strip() for pr in procs)
        self.assertEqual(outs.count("ok"), 1, outs)

    def test_crash_while_creating_record_leaves_no_json(self):
        """記録の最初の保存の途中で止まっても、正式な .json は置かれず、次の実行の load_runs が止まらない
        （2026-11-06 外部レビュー指摘: 空の .json で JSONDecodeError になった）"""
        lock = pi.DateLock(self.out)
        with mock.patch("prev_inputs.json.dump", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                pi.RunRecord(self.out, "20261107", "test", "v", lock)
        self.assertEqual(list((self.out / "prev_inputs").glob("*.json")), [])
        self.assertEqual(pi.load_runs(self.out), [])
        r = pi.RunRecord(self.out, "20261107", "test", "v", lock)   # その後の記録は作れる
        r.finish()
        lock.release()
        self.assertEqual(len(pi.load_runs(self.out)), 1)

    def test_broken_record_is_not_skipped(self):
        """壊れた記録は黙って読み飛ばさない（採点結果が参照する証拠を失うため）"""
        d = self.out / "prev_inputs"
        d.mkdir()
        (d / "20261106T170000000000_abcd1234.json").write_text("")
        with self.assertRaises(ValueError):
            pi.load_runs(self.out)

    def test_same_second_runs_do_not_collide(self):
        """同じプロセス・同じ時刻でも実行IDが別になり、前の記録を上書きしない（2026-11-05 外部レビュー指摘）"""
        fixed = pi.now_jst()
        with mock.patch("prev_inputs.now_jst", return_value=fixed):
            lock, r1 = new_run(self.out)
            r1.add_race("東京1R", "1", {"A": entry(pi.OK)}, {})
            r1.finish()
            first = json.loads(r1.path.read_text())
            r2 = pi.RunRecord(self.out, "20261107", "test", "v", lock)
            r2.finish()
            lock.release()
        self.assertNotEqual(r1.run_id, r2.run_id)
        self.assertEqual(json.loads(r1.path.read_text()), first)

    def test_runs_are_separate_files_and_not_overwritten(self):
        lock, r1 = new_run(self.out)
        r1.add_race("東京1R", "1", {"A": entry(pi.OK)}, {"decision": "判定"})
        r1.finish()
        first = json.loads(r1.path.read_text())
        r2 = pi.RunRecord(self.out, "20261107", "test", "v", lock)
        r2.add_race("東京1R", "1", {"A": entry(pi.FAILED)}, {"decision": "保留"})
        r2.finish()
        lock.release()
        self.assertNotEqual(r1.path, r2.path)
        self.assertEqual(json.loads(r1.path.read_text()), first)   # 前の記録は変わらない
        self.assertFalse(list((self.out / "prev_inputs").glob("*.tmp")))

    def test_reusable_entries_skip_failed_and_keep_origin(self):
        lock, r1 = new_run(self.out)
        r1.add_race("東京1R", "1", {"A": entry(pi.OK), "B": entry(pi.FAILED)}, {})
        r1.finish()
        reuse = pi.reusable_entries(self.out, "20261107")
        self.assertEqual(set(reuse), {"A"})
        self.assertEqual(reuse["A"]["origin_run"], r1.run_id)
        # 再利用した値（source=再利用）を、次の実行が再利用の元にしない（元の実行だけを元にする）
        r2 = pi.RunRecord(self.out, "20261107", "test", "v", lock)
        r2.add_race("東京1R", "1", {"A": dict(reuse["A"], source="再利用")}, {})
        r2.finish()
        lock.release()
        self.assertEqual(pi.reusable_entries(self.out, "20261107")["A"]["origin_run"], r1.run_id)

    def test_later_failure_disables_earlier_success(self):
        """成功した記録の後に同じ馬の取得が失敗・期限後なら、前の成功値に戻らない（2026-11-05 外部レビュー指摘）"""
        for later in (entry(pi.FAILED), dict(entry(pi.OK), status=pi.AFTER_DEADLINE, status_observed=pi.OK)):
            with tempfile.TemporaryDirectory() as t:
                out = Path(t) / "20261107"
                out.mkdir()
                lock, r1 = new_run(out)
                r1.add_race("東京1R", "1", {"A": entry(pi.OK, idx=80), "B": entry(pi.OK)}, {})
                r1.finish()
                r2 = pi.RunRecord(out, "20261107", "test", "v", lock)
                r2.add_race("東京1R", "1", {"A": later, "B": dict(entry(pi.OK), source="再利用")}, {})
                r2.finish()
                lock.release()
                reuse = pi.reusable_entries(out, "20261107")
                self.assertNotIn("A", reuse, later["status"])
                self.assertEqual(reuse["B"]["origin_run"], r1.run_id)   # 再利用は後の取得とみなさない


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
        self.assertTrue(self.needs(self.rd({"1": "馬IDが無い"})))
        self.assertTrue(self.needs(self.rd({"1": pi.OK}, {pi.FAILED: 1})))

    def test_normal_statuses_are_not_rerun(self):
        self.assertEqual(self.needs(self.rd({"1": pi.OK, "2": pi.NO_PREV}, {pi.IDX_EMPTY: 2, pi.OK: 5})), [])
        self.assertEqual(self.needs(self.rd({"1": pi.IDX_EMPTY})), [])
        self.assertEqual(self.needs(self.rd({"1": pi.OK, "2": pi.SCRATCHED})), [])

    def test_existing_rules(self):
        self.assertTrue(self.needs({"error": "x"}))
        self.assertTrue(self.needs({"pop_map": {}, "scored": [{"score": 0}]}))
        self.assertEqual(self.needs({"pop_map": {"1": "2"}, "scored": [{"score": 0}]}), [])


class CollectFlowTest(unittest.TestCase):
    """1レース分の前走データの処理（run_pickup_all.collect_prev_for_race）から品質の判定まで通す"""
    def test_scratched_candidate_does_not_make_race_bad(self):
        import run_pickup_all as rpa
        from data_quality import build_quality, race_quality_issues
        shutuba = {"horse_id_map": {"1": "A", "2": "B", "3": "C"}, "scratched_nums": {"2": "取消"}}
        fetched = []

        def fake_fetch(page, hid, date, browse, settle):
            fetched.append(hid)
            return entry(pi.OK, idx={"A": 80, "C": 70}[hid])
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "20261107"
            out.mkdir()
            lock, rec = new_run(out)
            with mock.patch.object(rpa, "fetch_entry", fake_fetch), mock.patch.object(rpa, "human_sleep"):
                prev_db, top1, st = rpa.collect_prev_for_race(None, "20261107", "東京1R", "1", shutuba,
                                                              {"1", "2"}, {}, rec)
            rec.finish()
            lock.release()
        self.assertEqual(sorted(fetched), ["A", "C"])   # 取消の馬は取らない
        self.assertEqual(st["candidates"], {"1": pi.OK, "2": pi.SCRATCHED})
        self.assertEqual((top1["decision"], top1["race_max"]), ("判定", 80))
        rd = {"collector_version": "v", "quality": build_quality(
            "v", {"index_reader_version": "r", "candidate_rule_version": "c",
                  "races": {"東京1R": {"modes": {"average": "type=rank"}}}}, "東京1R",
            prev=dict(prev_fetch=pi.PREV_FETCH_VERSION, prev_inputs_run="x", prev_status=st))}
        self.assertEqual(race_quality_issues(rd), [])


class SnapshotTest(unittest.TestCase):
    def test_snapshot_only_from_referenced_run(self):
        from run_pickup_all import write_prev_data_snapshot
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "20261107"
            out.mkdir()
            lock, r1 = new_run(out)
            r1.add_race("東京1R", "1", {"A": entry(pi.OK, idx=70)}, {})
            r1.finish()
            r2 = pi.RunRecord(out, "20261107", "test", "v", lock)
            r2.add_race("東京1R", "1", {"A": entry(pi.OK, idx=99), "B": entry(pi.FAILED)}, {})
            r2.finish()
            lock.release()
            n = write_prev_data_snapshot(out, {"東京1R": {"prev_inputs_run": r1.run_id}})
            snap = json.loads((out / "prev_data.json").read_text())
            self.assertEqual((n, snap["A"]["prev_idx"]), (1, "70"))   # 採点が参照した r1 の値だけ
            write_prev_data_snapshot(out, {"東京1R": {"prev_inputs_run": r2.run_id}})
            snap = json.loads((out / "prev_data.json").read_text())
            self.assertEqual(set(snap), {"A"})   # 取得失敗は載せない


if __name__ == "__main__":
    unittest.main()
