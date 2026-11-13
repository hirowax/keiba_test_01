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
import data_quality as DQ  # noqa: E402


FULL = "result_202603030211_20261113.html"          # 11/8 福島11R（16頭）の実ページ全体（2026-11-13 取得）
CHUSHI = "result_202608050103_chushi_20261112.html"  # 11/7 京都3R（中止1頭・13頭）の実ページ全体（2026-11-12 取得）


class ParseTest(unittest.TestCase):
    def test_cut_down_fixture_rows(self):
        """成績の表だけを切り出した保存ページ（「N頭」が無い）。通過順と取消の行の読み取りだけを確かめる"""
        r = SR.parse_result_html(read_fixture("result_202605041106_torikeshi.html"), "東京6R")
        self.assertEqual(r["problems"], ["ページの「N頭」の表示が無い"])
        first = r["horses"][0]
        self.assertEqual((first["num"], first["corners"], first["corners_text"]), ("12", [13, 13], "13-13"))
        self.assertEqual([(o["status"], o["corners"], o["corners_text"]) for o in r["others"]], [("取消", [], "")])

    def test_full_real_pages_have_no_problem(self):
        r = SR.parse_result_html(read_fixture(FULL), "福島11R")
        self.assertEqual(r["problems"], [])
        self.assertEqual(len(r["horses"]), 16)
        self.assertEqual((r["horses"][0]["num"], r["horses"][0]["gate"], r["horses"][0]["corners_text"]), ("8", "4", "8-9"))
        c = SR.parse_result_html(read_fixture(CHUSHI), "京都3R")
        self.assertEqual(c["problems"], [])
        self.assertEqual([(o["status"], o["num"], o["corners_text"]) for o in c["others"]], [("中止", "3", "11-11-13-13")])

    def _break_row(self, fn):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(read_fixture(FULL), "html.parser")
        rows = soup.find("table", id="All_Result_Table").find_all("tr", class_="HorseList")
        fn(rows[5])
        return SR.parse_result_html(str(soup), "福島11R")

    def test_broken_horse_row_is_a_problem(self):
        """16頭のうち1頭の行が壊れていたら、15頭の正常な結果として読まない（2026-11-13 外部レビュー指摘1）"""
        def cut_cells(tr):
            for td in tr.find_all("td")[3:]:
                td.decompose()
        r = self._break_row(cut_cells)
        self.assertTrue(r["problems"])
        self.assertTrue(any("馬名のリンク" in p or "馬番" in p or "通過順" in p for p in r["problems"]), r["problems"])

    def test_missing_row_count_mismatch(self):
        r = self._break_row(lambda tr: tr.decompose())
        self.assertIn("馬の行の数 15 がページの「16頭」と違う", r["problems"])

    def test_unknown_row_and_duplicates(self):
        r = self._break_row(lambda tr: tr.__setitem__("class", ["Other"]))
        self.assertTrue(any("知らない形の行" in p for p in r["problems"]))

        def dup_num(tr):
            tr.select_one("td.Num.Txt_C").string = "8"
        self.assertTrue(any("馬番が重複" in p for p in self._break_row(dup_num)["problems"]))

    def test_missing_passage_cell_is_a_problem(self):
        html = read_fixture(FULL).replace('class="PassageRate"', 'class="X"', 1)
        r = SR.parse_result_html(html, "福島11R")
        self.assertTrue(any("td.PassageRate" in p for p in r["problems"]))

    def test_duplicated_passage_cell_is_a_problem(self):
        """通過順のセルが2つある行は、最初の1つを読まずに形の異常にする（2026-11-13 外部レビュー 2回目の指摘2）"""
        import copy

        def dup_passage(tr):
            cell = tr.select_one("td.PassageRate")
            cell.insert_after(copy.copy(cell))
        r = self._break_row(dup_passage)
        self.assertTrue(any("通過順（td.PassageRate）が 2 個" in p for p in r["problems"]), r["problems"])

        from bs4 import BeautifulSoup   # 中止の馬の行（着順が数字でない行）でも同じ
        soup = BeautifulSoup(read_fixture(CHUSHI), "html.parser")
        tr = [t for t in soup.find("table", id="All_Result_Table").find_all("tr", class_="HorseList")
              if "中止" in t.select_one("td.Result_Num").get_text()][0]
        dup_passage(tr)
        self.assertTrue(any("通過順（td.PassageRate）が 2 個" in p
                            for p in SR.parse_result_html(str(soup), "京都3R")["problems"]))

    def test_no_table_is_a_problem(self):
        r = SR.parse_result_html("<html><body>メンテナンス中</body></html>", "東京6R")
        self.assertIn("成績の表が無い", r["problems"])
        self.assertIn("着順のある馬が0頭", r["problems"])


class FakePage:
    """結果ページの移動と中身を差し替える（ネットワークに出ない）。pages: {race_id: HTML | 例外}"""

    def __init__(self, pages):
        self.pages = pages
        self.url = ""
        self.visits = []

    def goto(self, url, **k):
        self.visits.append(url)
        self.url = url
        m = [rid for rid in self.pages if rid in url]
        if m and isinstance(self.pages[m[0]], Exception):
            raise self.pages[m[0]]

    def content(self):
        m = [rid for rid in self.pages if rid in self.url]
        return self.pages[m[0]] if m else "<html></html>"

    def evaluate(self, *a, **k):
        pass


class FetchRacesTest(unittest.TestCase):
    """取得の繰り返し（fetch_races）と保存（save_results）を模擬のページで確かめる（外部レビュー指摘2〜4）"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.good = read_fixture(FULL)
        self.ids = {"福島1R": "202603030201", "福島2R": "202603030202", "福島3R": "202603030203"}
        self.patches = [mock.patch.object(SR, "human_sleep"), mock.patch.object(SR.time, "sleep"),
                        mock.patch("scraper.random.random", return_value=0.99)]   # 中間ページを経由しない
        for pt in self.patches:
            pt.start()

    def tearDown(self):
        for pt in self.patches:
            pt.stop()
        shutil.rmtree(self.tmp)

    def attempt(self):
        return {"at": "t", "scrape_version": SR.SCRAPE_VERSION, "fetched": [], "failed": {}, "kept_old": {}, "stopped": None}

    def test_one_navigation_per_race(self):
        page = FakePage({rid: self.good for rid in self.ids.values()})
        att = self.attempt()
        fetched = SR.fetch_races(page, self.ids, att)
        self.assertEqual(sorted(fetched), sorted(self.ids))
        result_visits = [u for u in page.visits if "result.html" in u]
        self.assertEqual(len(result_visits), 3)   # 1レース1回（前は2回）

    def test_navigation_timeout_is_a_race_failure_and_results_are_saved(self):
        page = FakePage({"202603030201": self.good, "202603030202": TimeoutError("Timeout 30000ms"),
                         "202603030203": self.good})
        att = self.attempt()
        fetched = SR.fetch_races(page, self.ids, att)
        self.assertEqual(sorted(fetched), ["福島1R", "福島3R"])
        self.assertIn("Timeout", att["failed"]["福島2R"])
        out = self.tmp / "20261108"
        old = {"date": "20261108", "complete": True, "race_ids": self.ids,
               "races": {l: {"race_id": r, "fetched_at": "前"} for l, r in self.ids.items()}}
        out.mkdir()
        (out / SR.RESULTS_ALL_NAME).write_text(json.dumps(old, ensure_ascii=False))
        data = SR.save_results("20261108", out, self.ids, fetched, att, None, {}, {"srl": {}, "sde": {}}, None)
        self.assertEqual(data["races"]["福島2R"]["fetched_at"], "前")   # 古い値を残す
        self.assertTrue(data["complete"])
        log = json.loads((out / SR.FETCH_LOG_NAME).read_text())["attempts"][-1]
        self.assertEqual(log["kept_old"], {"福島2R": "前"})
        self.assertIn("福島2R", log["failed"])

    def test_block_stops_immediately_and_saves_what_was_fetched(self):
        blocked = "<html><body>Too Many Requests</body></html>"
        page = FakePage({"202603030201": self.good, "202603030202": blocked, "202603030203": self.good})
        att = self.attempt()
        fetched = SR.fetch_races(page, self.ids, att)
        self.assertEqual(sorted(fetched), ["福島1R"])
        self.assertIn("IPブロック", att["stopped"])
        self.assertEqual(att["failed"]["福島3R"], "止めたので取っていない")
        self.assertFalse(any("202603030203" in u for u in page.visits))   # 検知の後は取らない
        out = self.tmp / "20261108"
        out.mkdir()
        data = SR.save_results("20261108", out, self.ids, fetched, att, None, {}, {"srl": {}, "sde": {}}, None)
        self.assertFalse(data["complete"])
        self.assertEqual(list(data["races"]), ["福島1R"])
        self.assertIn("IPブロック", json.loads((out / SR.FETCH_LOG_NAME).read_text())["attempts"][-1]["stopped"])

    def test_broken_page_does_not_overwrite_good_old_value(self):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(self.good, "html.parser")
        soup.find("table", id="All_Result_Table").find_all("tr", class_="HorseList")[3].decompose()
        page = FakePage({"202603030201": str(soup)})
        att = self.attempt()
        ids = {"福島1R": "202603030201"}
        fetched = SR.fetch_races(page, ids, att)
        self.assertEqual(fetched, {})
        self.assertIn("形の異常", att["failed"]["福島1R"])
        out = self.tmp / "d"
        out.mkdir()
        (out / SR.RESULTS_ALL_NAME).write_text(json.dumps({"races": {"福島1R": {"fetched_at": "前", "horses": [1] * 16}}}))
        data = SR.save_results("d", out, ids, fetched, att, None, {}, {"srl": {}, "sde": {}}, None)
        self.assertEqual(data["races"]["福島1R"]["fetched_at"], "前")

    def test_unexpected_exception_keeps_fetched_races_for_caller(self):
        """1レース取った後の待機で予期しない例外が出ても、呼び出し元の辞書に取れた分が残り、保存される
        （2026-11-13 外部レビュー 2回目の指摘1。それまでは戻り値の代入が終わらず、0件になっていた）"""
        page = FakePage({rid: self.good for rid in self.ids.values()})
        att = self.attempt()
        fetched = {}
        with mock.patch.object(SR, "human_sleep", side_effect=RuntimeError("待機の例外")):
            with self.assertRaises(RuntimeError):
                SR.fetch_races(page, self.ids, att, fetched)
        self.assertEqual(sorted(fetched), ["福島1R"])
        self.assertEqual(att["fetched"], ["福島1R"])
        out = self.tmp / "20261108"
        out.mkdir()
        data = SR.save_results("20261108", out, self.ids, fetched, att, None, {}, {"srl": {}, "sde": {}}, None)
        self.assertEqual(list(data["races"]), ["福島1R"])

    def run_main(self, sleep_effect=None, retry=None):
        """main を模擬のページで動かす。戻り値は標準出力"""
        import contextlib
        import io
        out_root = self.tmp / "output"
        page = FakePage({rid: self.good for rid in self.ids.values()})

        class PW:
            def __enter__(s):
                br = mock.MagicMock()
                br.new_context.return_value.new_page.return_value = page
                pw = mock.MagicMock()
                pw.chromium.launch.return_value = br
                return pw

            def __exit__(s, *a):
                return False
        races = [{"label": l, "race_id": r} for l, r in self.ids.items()]
        import scrape_jra_payouts as J
        with mock.patch.object(SR, "sync_playwright", PW), mock.patch.object(SR, "load_cookies", return_value=True), \
                mock.patch.object(SR, "get_race_ids", return_value=races), \
                mock.patch.object(SR, "human_sleep", side_effect=sleep_effect), \
                mock.patch.object(SR, "OUTPUT_DIR", out_root), mock.patch.object(SR, "PEDIGREE_FILE", self.tmp / "ped.json"), \
                mock.patch.object(SR, "BASE_DIR", self.tmp), mock.patch.object(SR, "refresh_jra_lists"), \
                mock.patch.object(SR, "retry_unconfirmed_schedules", **(retry or {"return_value": []})), \
                mock.patch.object(J, "load_cache", return_value={"srl": {}, "sde": {}}), \
                mock.patch.object(J, "CACHE_PATH", self.tmp / "cache.json"), \
                mock.patch.object(SR, "load_env"), mock.patch.object(SR.sys, "argv", ["x", "20261108"]):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                SR.main()   # 保存の後は 0 で終わる（SystemExit を出さない）
        return buf.getvalue()

    def test_main_saves_races_fetched_before_unexpected_exception(self):
        """main の流れ全体: 1レース取った後に予期しない例外 → 取れた1レースが race_results_all.json に入り、止めた理由が残る"""
        out_root = self.tmp / "output"
        self.run_main(sleep_effect=[None, RuntimeError("待機の例外")])
        data = json.loads((out_root / "20261108" / SR.RESULTS_ALL_NAME).read_text())
        self.assertEqual(list(data["races"]), ["福島1R"])
        log = json.loads((out_root / "20261108" / SR.FETCH_LOG_NAME).read_text())["attempts"][-1]
        self.assertIn("予期しない例外", log["stopped"])
        self.assertEqual(log["failed"]["福島2R"], "止めたので取っていない")

    def test_main_exception_after_save_still_prints_changed_files(self):
        """保存の後の、前の開催日の JRA 公式の一覧の取り直しで例外 → 止まらず、保存したファイルの CHANGED_FILE: と知らせの印を出す
        （2026-11-13〜。それまでは 0 以外で終わり、cron が commit しなかった）"""
        out = self.run_main(retry={"side_effect": RuntimeError("取り直しの例外")})
        self.assertIn("CHANGED_FILE: output/20261108", out)
        self.assertIn("CHANGED_FILE: cache.json", out)
        self.assertIn("保存とその後の失敗: 前の開催日の JRA 公式の一覧の取り直し RuntimeError: 取り直しの例外", out)
        self.assertEqual(len(json.loads((self.tmp / "output" / "20261108" / SR.RESULTS_ALL_NAME).read_text())["races"]), 3)

    def test_scored_files_failure_keeps_all_results_and_fetch_log(self):
        """race_results_all.json を書いた後に race_results.json などを書けない → 例外で止めず、取得の記録に理由を残す"""
        page = FakePage({rid: self.good for rid in self.ids.values()})
        att = self.attempt()
        fetched = SR.fetch_races(page, self.ids, att)
        out = self.tmp / "20261108"
        out.mkdir()
        real = SR._write_json_atomic

        def flaky(path, data, indent=2):
            if Path(path).name == "race_results.json":
                raise OSError("No space left on device")
            return real(path, data, indent)
        with mock.patch.object(SR, "_write_json_atomic", side_effect=flaky):
            data = SR.save_results("20261108", out, self.ids, fetched, att, None, {}, {"srl": {}, "sde": {}}, None)
        self.assertEqual(len(data["races"]), 3)
        self.assertEqual(len(json.loads((out / SR.RESULTS_ALL_NAME).read_text())["races"]), 3)
        log = json.loads((out / SR.FETCH_LOG_NAME).read_text())["attempts"][-1]
        self.assertIn("race_results.json を書けない（書けたもの: なし）: OSError", log["save_errors"][0])
        self.assertIsNotNone(DQ.results_save_issue(out))   # 印が残る

    def test_main_all_results_write_failure_still_ends_normally_and_notifies(self):
        """race_results_all.json を書けない → 前のファイルのまま、一時ファイルを残さず、0 で終わって知らせの印を出す"""
        real_replace = SR.os.replace

        def failing_replace(src, dst):
            if Path(dst).name == SR.RESULTS_ALL_NAME:
                raise OSError("No space left on device")
            return real_replace(src, dst)
        with mock.patch.object(SR.os, "replace", side_effect=failing_replace):
            out = self.run_main()
        day = self.tmp / "output" / "20261108"
        self.assertFalse((day / SR.RESULTS_ALL_NAME).exists())
        self.assertEqual(list(day.glob(".*.tmp")), [])
        self.assertIn("CHANGED_FILE: output/20261108", out)
        self.assertIn(f"保存とその後の失敗: {SR.RESULTS_ALL_NAME} を保存できない（前のファイルのまま）: OSError", out)
        self.assertIn("予定 保存できないので照合の結果なし", out)

    def test_consecutive_failures_stop(self):
        page = FakePage({rid: TimeoutError("x") for rid in self.ids.values()})
        with mock.patch.object(SR, "MAX_CONSECUTIVE_FAIL", 2):
            att = self.attempt()
            SR.fetch_races(page, self.ids, att)
        self.assertIn("連続 2 回", att["stopped"])
        self.assertEqual(att["failed"]["福島3R"], "止めたので取っていない")


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


class SaveMarkerTest(unittest.TestCase):
    """保存が不完全の印（data_quality.RESULTS_SAVE_MARKER）と、読み手がその日の結果を使わないこと、作り直し
    （2026-11-13 外部レビュー 実装の3回目の指摘1）"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "20261108"
        self.out.mkdir()
        self.ids = {"福島1R": "202603030201", "福島2R": "202603030202"}
        self.patches = [mock.patch.object(SR, "human_sleep"), mock.patch.object(SR.time, "sleep"),
                        mock.patch("scraper.random.random", return_value=0.99)]
        for pt in self.patches:
            pt.start()
        page = FakePage({rid: read_fixture(FULL) for rid in self.ids.values()})
        self.att = {"at": "t", "scrape_version": SR.SCRAPE_VERSION, "fetched": [], "failed": {}, "kept_old": {}, "stopped": None}
        self.fetched = SR.fetch_races(page, self.ids, self.att)
        # 前の回の一式（古い値）
        old = {l: [{"num": "1", "name": "古い馬"}] for l in self.ids}
        for name in ("race_results.json", "race_conditions.json", "race_nonfinishers.json"):
            (self.out / name).write_text(json.dumps(old if name == "race_results.json" else {l: {"old": True} for l in self.ids},
                                                    ensure_ascii=False))

    def tearDown(self):
        for pt in self.patches:
            pt.stop()
        shutil.rmtree(self.tmp)

    def save(self, fail_name=None, fail_marker=False):
        real = SR._write_json_atomic

        def flaky(path, data, indent=2):
            name = Path(path).name
            if name == fail_name or (fail_marker and name == DQ.RESULTS_SAVE_MARKER):
                raise OSError("No space left on device")
            return real(path, data, indent)
        with mock.patch.object(SR, "_write_json_atomic", side_effect=flaky):
            return SR.save_results("20261108", self.out, self.ids, self.fetched, self.att, None, {},
                                   {"srl": {}, "sde": {}}, None)

    def test_success_leaves_no_marker(self):
        self.save()
        self.assertIsNone(DQ.results_save_issue(self.out))
        self.assertFalse((self.out / DQ.RESULTS_SAVE_MARKER).exists())

    def test_conditions_failure_marks_day_and_readers_do_not_use_it(self):
        """レビューの再現: 条件のファイルを書けない → 結果だけ新しい。印が残り、照合・軸の記録・精算・閾値は使わない"""
        self.save(fail_name="race_conditions.json")
        results = json.loads((self.out / "race_results.json").read_text())
        conds = json.loads((self.out / "race_conditions.json").read_text())
        self.assertEqual(len(results["福島1R"]), 16)          # 新しい
        self.assertEqual(conds["福島1R"], {"old": True})       # 古い（混ざっている）
        issue = DQ.results_save_issue(self.out)
        self.assertIn("採点したレースのファイルの一部だけが新しい可能性", issue)
        self.assertIn("race_conditions", issue)

        import verify_results, axis_record, paper_trade, calibrate_threshold
        v = verify_results.check_day("20261108", self.tmp)
        self.assertEqual(v["save_incomplete"], issue)
        self.assertEqual(v["mismatch"], [f"その日の結果を照合しない: {issue}"])
        self.assertEqual(axis_record.purchase_gap(self.out, "福島1R", "8", "x", {}), issue)
        (self.out / "paper_bets.json").write_text(json.dumps({"races": {}}))
        (self.out / "payouts_jra.json").write_text(json.dumps({}))
        with mock.patch.object(paper_trade, "OUTPUT_DIR", self.tmp):
            self.assertFalse(paper_trade.settle_for_date("20261108"))
        (self.out / "pickup_scores.json").write_text(json.dumps({"races": {}}))
        with mock.patch.object(calibrate_threshold, "OUTPUT_DIR", self.tmp), \
                mock.patch.object(calibrate_threshold, "CLEAN_START", "20200101"):
            self.assertEqual(calibrate_threshold.load_pairs(), [])
        import scrape_jra_payouts
        with mock.patch.object(scrape_jra_payouts, "OUTPUT_DIR", self.tmp):
            with self.assertRaises(scrape_jra_payouts.ResultsSaveIncomplete):
                scrape_jra_payouts.load_known_races("20261108")

    def test_rebuild_from_all_results_makes_a_consistent_set_and_clears_marker(self):
        self.save(fail_name="race_conditions.json")
        with mock.patch.object(SR, "PEDIGREE_FILE", self.tmp / "ped.json"):
            SR.rebuild_scored_files("20261108", self.tmp)
        self.assertIsNone(DQ.results_save_issue(self.out))
        conds = json.loads((self.out / "race_conditions.json").read_text())
        self.assertEqual(conds["福島1R"]["race_id"], "202603030201")
        self.assertEqual(len(json.loads((self.out / "race_results.json").read_text())["福島1R"]), 16)
        log = json.loads((self.out / SR.FETCH_LOG_NAME).read_text())["attempts"][-1]
        self.assertIn("作り直し", log["kind"])

    def test_next_run_with_nothing_fetched_rebuilds_from_all_results_before_clearing(self):
        """レビュー（実装の4回目）の再現: 条件のファイルを書けず結果だけ新しい → 次の回は1件も取れない。
        前は混在したまま印だけ消えた。今は保存済みの全レースの結果から3ファイルを作り直してから消す"""
        self.save(fail_name="race_conditions.json")
        self.assertEqual(json.loads((self.out / "race_conditions.json").read_text())["福島1R"], {"old": True})
        self.fetched = {}
        self.att = {"at": "t2", "scrape_version": SR.SCRAPE_VERSION, "fetched": [], "failed": {}, "kept_old": {}, "stopped": None}
        self.save()
        self.assertIsNone(DQ.results_save_issue(self.out))
        conds = json.loads((self.out / "race_conditions.json").read_text())
        self.assertEqual(conds["福島1R"]["race_id"], "202603030201")   # 古い値が残っていない
        self.assertEqual(sorted(self.att["scored_rebuilt_from_all_results"]), sorted(self.ids))

    def test_prior_marker_with_races_that_cannot_be_rebuilt_keeps_marker(self):
        """前の回の印があり、race_results_all.json から作り直せない採点したレースがファイルに残る → 印を残す"""
        for name in ("race_results.json", "race_conditions.json", "race_nonfinishers.json"):
            body = json.loads((self.out / name).read_text())
            body["福島9R"] = [{"num": "1"}] if name == "race_results.json" else {"x": 1}
            (self.out / name).write_text(json.dumps(body, ensure_ascii=False))
        labels = set(self.ids) | {"福島9R"}
        SR._write_save_marker(self.out, "前の回の失敗")
        att = {"save_errors": []}
        data = {"race_ids": self.ids, "races": dict(self.fetched)}
        self.assertFalse(SR.save_scored_set(self.out, data, labels, {}, att, write_all=False))
        self.assertIn("作り直せないレースが残る", DQ.results_save_issue(self.out))
        self.assertEqual(att["kept_old_scored_files"], ["福島9R"])
        # 前の回の印が無ければ、作り直せないレース（前の取り方の値）は前の値のまま、印は残らない
        (self.out / DQ.RESULTS_SAVE_MARKER).unlink()
        self.assertTrue(SR.save_scored_set(self.out, data, labels, {}, {"save_errors": []}, write_all=False))
        self.assertIsNone(DQ.results_save_issue(self.out))

    def test_read_back_mismatch_keeps_marker(self):
        real = SR._write_json_atomic

        def corrupt(path, data, indent=2):
            if Path(path).name == "race_nonfinishers.json":
                data = {}
            return real(path, data, indent)
        with mock.patch.object(SR, "_write_json_atomic", side_effect=corrupt):
            self.assertFalse(SR.save_scored_set(self.out, {"race_ids": self.ids, "races": dict(self.fetched)}, None, {},
                                                {"save_errors": []}, write_all=False))
        self.assertIn("読み直すと中身が違う", DQ.results_save_issue(self.out))

    def test_rebuild_places_marker_first_and_keeps_it_on_failure(self):
        """レビュー（実装の4回目）の再現: 印の無い日に作り直して条件のファイルで失敗 → 前は印なしの不完全な状態になった。今は印が残る"""
        self.save()
        self.assertIsNone(DQ.results_save_issue(self.out))
        real = SR._write_json_atomic

        def flaky(path, data, indent=2):
            if Path(path).name == "race_conditions.json":
                raise OSError("No space left on device")
            return real(path, data, indent)
        with mock.patch.object(SR, "PEDIGREE_FILE", self.tmp / "ped.json"), \
                mock.patch.object(SR, "_write_json_atomic", side_effect=flaky):
            att = SR.rebuild_scored_files("20261108", self.tmp)
        self.assertFalse(att["marker_cleared"])
        self.assertIn("race_conditions.json を書けない", DQ.results_save_issue(self.out))

    def test_all_results_failure_without_prior_marker_leaves_old_set_unmarked(self):
        with self.assertRaises(OSError):
            self.save(fail_name=SR.RESULTS_ALL_NAME)
        self.assertIsNone(DQ.results_save_issue(self.out))   # 何も書き換えていない
        self.assertEqual(json.loads((self.out / "race_conditions.json").read_text())["福島1R"], {"old": True})

    def test_all_results_failure_keeps_prior_marker(self):
        self.save(fail_name="race_conditions.json")
        with self.assertRaises(OSError):
            self.save(fail_name=SR.RESULTS_ALL_NAME)
        self.assertIsNotNone(DQ.results_save_issue(self.out))   # 前の回の混在が残っている

    def test_marker_write_failure_writes_nothing(self):
        with self.assertRaises(OSError):
            self.save(fail_marker=True)
        self.assertFalse((self.out / SR.RESULTS_ALL_NAME).exists())
        self.assertEqual(json.loads((self.out / "race_conditions.json").read_text())["福島1R"], {"old": True})

    def test_fetch_log_only_failure_is_not_marked(self):
        self.save(fail_name=SR.FETCH_LOG_NAME)
        self.assertIsNone(DQ.results_save_issue(self.out))
        self.assertTrue(self.att["save_errors"][0].startswith("取得の記録"))


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

    def test_retry_exception_on_one_day_continues_and_keeps_written_file(self):
        """1日目で書き換えた後に例外 → その日のファイルは戻り値に入り、理由を errors に残して2日目へ進む"""
        for d in ("20261107", "20261108"):
            p = self.tmp / d / SR.RESULTS_ALL_NAME
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({"race_ids": {"東京1R": "202605010101"}, "schedule_check": {"status": S.UNCONFIRMED}}))
        real = SR.append_fetch_log

        def flaky(out_dir, attempt):
            if Path(out_dir).name == "20261107":
                raise OSError("書けない")
            return real(out_dir, attempt)
        errors = []
        with mock.patch.object(SR, "refresh_jra_lists"), mock.patch.object(SR, "append_fetch_log", side_effect=flaky):
            changed = SR.retry_unconfirmed_schedules("20261114", {"srl": {}, "sde": {}}, self.tmp, errors=errors)
        names = sorted(f"{Path(c).parent.name}/{Path(c).name}" for c in changed)
        self.assertEqual(names, sorted([f"20261107/{SR.RESULTS_ALL_NAME}", f"20261108/{SR.FETCH_LOG_NAME}",
                                        f"20261108/{SR.RESULTS_ALL_NAME}"]))
        self.assertEqual(errors, ["20261107: OSError: 書けない"])

    def test_schedule_check_for_marks_missing_scored_race(self):
        ids = {f"東京{n}R": f"2026050101{n:02d}" for n in range(1, 13)}
        cache = {"srl": {"20261101_05": {}}, "sde": {r: {"date": "20261101"} for r in ids.values()}}
        self.assertEqual(SR.schedule_check_for("20261101", ids, cache, None)["status"], S.CONFIRMED)
        sc = SR.schedule_check_for("20261101", ids, cache, None, "採点したレースがレース一覧に無い: ['東京13R']")
        self.assertEqual(sc["status"], S.UNCONFIRMED)


if __name__ == "__main__":
    unittest.main()
