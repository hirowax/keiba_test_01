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

    def test_main_saves_races_fetched_before_unexpected_exception(self):
        """main の流れ全体: 1レース取った後に予期しない例外 → 取れた1レースが race_results_all.json に入り、止めた理由が残る"""
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
                mock.patch.object(SR, "human_sleep", side_effect=[None, RuntimeError("待機の例外")]), \
                mock.patch.object(SR, "OUTPUT_DIR", out_root), mock.patch.object(SR, "PEDIGREE_FILE", self.tmp / "ped.json"), \
                mock.patch.object(SR, "BASE_DIR", self.tmp), mock.patch.object(SR, "refresh_jra_lists"), \
                mock.patch.object(SR, "retry_unconfirmed_schedules", return_value=[]), \
                mock.patch.object(J, "load_cache", return_value={"srl": {}, "sde": {}}), \
                mock.patch.object(J, "CACHE_PATH", self.tmp / "cache.json"), \
                mock.patch.object(SR, "load_env"), mock.patch.object(SR.sys, "argv", ["x", "20261108"]):
            try:
                SR.main()
            except SystemExit:
                pass
        data = json.loads((out_root / "20261108" / SR.RESULTS_ALL_NAME).read_text())
        self.assertEqual(list(data["races"]), ["福島1R"])
        log = json.loads((out_root / "20261108" / SR.FETCH_LOG_NAME).read_text())["attempts"][-1]
        self.assertIn("予期しない例外", log["stopped"])
        self.assertEqual(log["failed"]["福島2R"], "止めたので取っていない")

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
