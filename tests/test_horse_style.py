"""
脚質の作成と記録（build_horse_style.py・schedule_check.py。2026-11-12〜）と、軸の記録での確かめ（axis_record.load_style_entry）の回帰テスト。
計画: docs/horse_style_plan_202611.md、設計: docs/horse_style_design_202611.md（7章のテスト）。

入力は自作の小さな結果（race_results_all.json の形）と、作業用の git リポジトリ。実データでの件数の確かめは
`python3 build_horse_style.py make 20261114 --dry-run`（設計 0章の数と照らす）。

実行: python3 -m unittest discover -s tests -v
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build_horse_style as B  # noqa: E402
import schedule_check as S  # noqa: E402
from prev_inputs import JST  # noqa: E402

VEN = {"東京": "05", "京都": "08", "新潟": "04"}


def rid(date, venue, num):
    return f"{date[:4]}{VEN[venue]}01{date[6:8]}{num:02d}"   # 開催日ごとに違うID（日の2桁に日付の日を使う）


def horses(n, corners=None, start_id=1):
    """着順のある n 頭。corners[i] はその馬の通過順の並び（省略なら [i+1, i+1]）"""
    return [{"rank": i + 1, "num": str(i + 1), "name": f"ウマ{i + 1}", "horse_id": f"{start_id + i:010d}",
             "corners": (corners[i] if corners else [i + 1, i + 1])} for i in range(n)]


def race(h, others=None, surface="ダート", distance=1400):
    return {"race_id": "", "condition": {"surface": surface, "distance": distance}, "horses": h, "others": others or []}


def day(date, venues=("東京",), races=None, nums=range(1, 13)):
    """1日分の race_results_all.json（全レース 8頭・同じ馬）と JRA 公式の一覧"""
    race_ids, rs = {}, {}
    for v in venues:
        for n in nums:
            label = f"{v}{n}R"
            race_ids[label] = rid(date, v, n)
            rs[label] = (races or {}).get(label) or race(horses(8))
    return {"date": date, "complete": True, "race_ids": race_ids, "races": rs}


def jra_cache_for(*days_):
    c = {"srl": {}, "sde": {}, "monthly": {}}
    for d in days_:
        for label, r in d["race_ids"].items():
            c["srl"][f"{d['date']}_{r[4:6]}"] = {"cname": "x"}
            c["sde"][r] = {"cname": "x", "date": d["date"]}
    return c


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


class ScheduleTest(unittest.TestCase):
    def test_confirmed(self):
        d = day("20261101", ("東京", "京都"))
        self.assertEqual(S.day_schedule("20261101", d["race_ids"], jra_cache_for(d))["status"], S.CONFIRMED)

    def test_whole_venue_missing_in_netkeiba_list(self):
        """開催場ごと一覧から抜けると、1R〜12R はそろっていても JRA 公式との照合で見つかる"""
        full = day("20261101", ("東京", "京都"))
        part = day("20261101", ("東京",))
        out = S.day_schedule("20261101", part["race_ids"], jra_cache_for(full))
        self.assertEqual(out["status"], S.UNCONFIRMED)
        self.assertIn("開催場が一致しない", out["reason"])

    def test_one_race_id_differs(self):
        d = day("20261101")
        c = jra_cache_for(d)
        del c["sde"][d["race_ids"]["東京5R"]]
        out = S.day_schedule("20261101", d["race_ids"], c)
        self.assertIn("レースIDが一致しない", out["reason"])

    def test_jra_list_missing(self):
        d = day("20261101")
        self.assertIn("JRA 公式の結果の一覧が無い", S.day_schedule("20261101", d["race_ids"], {"srl": {}, "sde": {}})["reason"])

    def test_not_1_to_12_even_if_lists_agree(self):
        d = day("20260207", nums=range(1, 8))
        out = S.day_schedule("20260207", d["race_ids"], jra_cache_for(d))
        self.assertEqual(out["status"], S.UNCONFIRMED)
        self.assertIn("1R〜12R そろわない", out["reason"])
        self.assertIn("中止・変更の記録は確かめていない", out["reason"])

    def test_label_and_id_mismatch(self):
        d = day("20261101")
        d["race_ids"]["東京5R"], d["race_ids"]["東京6R"] = d["race_ids"]["東京6R"], d["race_ids"]["東京5R"]
        self.assertIn("レース名とレースIDが合わない", S.day_schedule("20261101", d["race_ids"], jra_cache_for(d))["reason"])


class RaceVerdictTest(unittest.TestCase):
    def v(self, r, label="東京1R"):
        return B.race_verdict(label, r)

    def test_ok_and_ratio_uses_started_count(self):
        others = [{"status": "取消", "horse_id": "x"}]
        ok, reason, rows = self.v(race(horses(9), others))
        self.assertTrue(ok)
        self.assertEqual(rows[0], ("0000000001", 0.0))
        self.assertEqual(rows[8], ("0000000009", 1.0))

    def test_same_position_twice_is_allowed(self):
        """実ページに「13-13」が2頭いる例がある（同じ順位）"""
        c = [[1, 1]] + [[1, 1]] + [[i, i] for i in range(3, 10)]
        ok, _, rows = self.v(race(horses(9, c)))
        self.assertTrue(ok)
        self.assertEqual(rows[1][1], 0.0)

    def test_reasons(self):
        cases = [
            (race(horses(9), [{"status": "不明", "horse_id": "x"}]), B.R_UNKNOWN),
            (race(horses(7)), B.R_FEW),
            (race(horses(7), [{"status": "中止", "horse_id": "y"}]), B.R_NONFINISH),   # 発走した頭数は8
            (race(horses(9, [[]] * 9)), B.R_NOT_YET),
            (race(horses(9), [{"status": "失格", "horse_id": "y"}]), B.R_NONFINISH),
            (race(horses(9, [[i + 1] for i in range(9)]), surface="芝", distance=1000), B.R_SHAPE),
            (race(horses(9, [[1, 1]] + [[i + 2] for i in range(8)])), B.R_SHAPE),   # 値の数が違う
            (race(horses(9, [[]] + [[i + 2, 1] for i in range(8)])), B.R_BAD),       # 一部だけ空
            (race(horses(9, [[10, 1]] + [[i + 2, 1] for i in range(8)])), B.R_BAD),  # 範囲外
        ]
        for i, (r, want) in enumerate(cases):
            label = "新潟1R" if r["condition"]["distance"] == 1000 else "東京1R"
            self.assertEqual(self.v(r, label)[:2], (False, want), i)
        dup = horses(9)
        dup[1]["horse_id"] = dup[0]["horse_id"]
        self.assertEqual(self.v(race(dup))[1], B.R_BAD)
        no_id = horses(9)
        no_id[3]["horse_id"] = ""
        self.assertEqual(self.v(race(no_id))[1], B.R_BAD)

    def test_reason_order(self):
        """8頭未満で中止もいれば、先に当たる「8頭未満」で数える"""
        self.assertEqual(self.v(race(horses(6), [{"status": "中止", "horse_id": "y"}]))[1], B.R_FEW)

    def test_straight_only_niigata_turf_1000(self):
        ok, _, _ = self.v(race(horses(9, [[i + 1] for i in range(9)]), surface="芝", distance=1000), "東京1R")
        self.assertTrue(ok)   # 値が1つでも、全馬で数が同じで新潟の芝1000m でなければ使う


class InputDateTest(unittest.TestCase):
    def test_last_input_date(self):
        # 2026-11-15 は日曜（date コマンドで確認）: 土曜 11/14 は入らず、11/13 以前
        self.assertEqual(B.last_input_date("20261115"), "20261113")
        # 土曜の対象日: 前の日曜まで入る
        self.assertEqual(B.last_input_date("20261114"), "20261112")
        # 祝日の月曜（2026-11-23 は月曜・勤労感謝の日）: 土曜 11/21 まで入り、日曜 11/22 は入らない
        self.assertEqual(B.last_input_date("20261123"), "20261121")


class RepoCase(unittest.TestCase):
    """作業用の git リポジトリ（output/ の結果と JRA 公式のキャッシュ）"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        self.days = [day("20261107"), day("20261108"), day("20261114")]
        for d in self.days:
            p = self.repo / "output" / d["date"] / B.RESULTS_ALL_NAME
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(d, ensure_ascii=False))
        (self.repo / S.JRA_CACHE_PATH).write_text(json.dumps(jra_cache_for(*self.days)))
        git(self.repo, "add", "output")
        git(self.repo, "commit", "-q", "-m", "data")
        self.out = self.repo / "output"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def clock(self, *times):
        it = iter(times)
        last = [None]

        def now():
            try:
                last[0] = next(it)
            except StopIteration:
                pass
            return last[0]
        return now


T_OK = datetime(2026, 11, 14, 17, 5, tzinfo=JST)          # 対象日 11/15 の前日 17:05
T_LAST = datetime(2026, 11, 14, 23, 59, 59, tzinfo=JST)
T_DEADLINE = datetime(2026, 11, 15, 0, 0, 0, tzinfo=JST)


class BuildTest(RepoCase):
    def test_inputs_are_two_days_before_and_read_from_blob(self):
        commit = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(B.select_inputs("20261115", commit, self.repo),
                         ["output/20261107/race_results_all.json", "output/20261108/race_results_all.json"])
        listing, data = B.fix_inputs("20261115", commit, self.repo)
        self.assertEqual([x["path"] for x in listing][-1], S.JRA_CACHE_PATH)
        self.assertTrue(all(len(x["blob"]) == 40 for x in listing))

    def test_uncommitted_change_fails(self):
        p = self.out / "20261108" / B.RESULTS_ALL_NAME
        p.write_text(p.read_text() + " ")
        rec = B.make_record("20261115", self.repo, self.clock(T_OK))
        self.assertEqual(rec["status"], B.STATUS_FAILED)
        self.assertIn("commit されていない変更", rec["failure"]["reason"])

    def test_reads_blob_not_working_file(self):
        """確かめた後に作業ファイルが変わっても、入力（blob）は変わらない"""
        commit = git(self.repo, "rev-parse", "HEAD")
        listing, data = B.fix_inputs("20261115", commit, self.repo)
        p = self.out / "20261107" / B.RESULTS_ALL_NAME
        p.write_text("{}")
        self.assertEqual(json.loads(data["output/20261107/race_results_all.json"])["date"], "20261107")

    def test_success_and_counts(self):
        rec = B.make_record("20261115", self.repo, self.clock(T_OK, T_OK, T_LAST))
        self.assertEqual(rec["status"], B.STATUS_OK, rec.get("failure"))
        self.assertEqual(rec["races_used"], 24)
        self.assertEqual(rec["counts"]["horses"], 8)
        self.assertEqual(rec["horses"]["0000000001"]["n_races"], 24)
        self.assertEqual(rec["period"], {"first": "20261107", "last": "20261108"})

    def test_finish_at_deadline_fails(self):
        rec = B.make_record("20261115", self.repo, self.clock(T_OK, T_OK, T_DEADLINE))
        self.assertEqual(rec["status"], B.STATUS_FAILED)
        self.assertIn("期限", rec["failure"]["reason"])

    def test_start_after_deadline_fails(self):
        rec = B.make_record("20261115", self.repo, self.clock(T_DEADLINE))
        self.assertEqual(rec["status"], B.STATUS_FAILED)

    def test_zero_races_is_failure(self):
        rec = B.make_record("20261108", self.repo, self.clock(datetime(2026, 11, 7, 17, tzinfo=JST)))
        self.assertEqual(rec["status"], B.STATUS_FAILED)   # 入力が1日も無い（11/7 は2日前より後）
        self.assertIn("0件", rec["failure"]["reason"])

    def test_unconfirmed_day_is_excluded(self):
        c = json.loads((self.repo / S.JRA_CACHE_PATH).read_text())
        for r in list(c["sde"]):
            if c["sde"][r]["date"] == "20261107":
                del c["sde"][r]
        (self.repo / S.JRA_CACHE_PATH).write_text(json.dumps(c))
        git(self.repo, "commit", "-qam", "cache")
        rec = B.make_record("20261115", self.repo, self.clock(T_OK, T_OK, T_LAST))
        self.assertEqual(rec["days_used"], ["20261108"])
        self.assertIn("JRA 公式の結果の一覧が無い", rec["days_excluded"]["20261107"])


class SavedScheduleTest(RepoCase):
    def test_saved_unconfirmed_schedule_excludes_day(self):
        p = self.out / "20261107" / B.RESULTS_ALL_NAME
        d = json.loads(p.read_text())
        d["schedule_check"] = {"status": S.UNCONFIRMED, "reason": "採点したレースがレース一覧に無い"}
        p.write_text(json.dumps(d, ensure_ascii=False))
        git(self.repo, "commit", "-qam", "sc")
        rec = B.make_record("20261115", self.repo, self.clock(T_OK, T_OK, T_LAST))
        self.assertEqual(rec["days_used"], ["20261108"])
        self.assertIn("採点したレースがレース一覧に無い", rec["days_excluded"]["20261107"])


class EnsureTest(RepoCase):
    def ensure(self, *times):
        return B.ensure_style("20261115", self.out, self.repo, self.clock(*(times or (T_OK, T_OK, T_LAST))))

    def files(self):
        return sorted((self.out / "20261115" / "horse_style").glob("*.json"))

    def test_second_call_reuses_confirmed(self):
        rec1, ref1 = self.ensure()
        rec2, ref2 = self.ensure()
        self.assertEqual(ref1, ref2)
        self.assertEqual(len(self.files()), 1)

    def test_failure_then_success_confirms_and_keeps_failure(self):
        p = self.out / "20261108" / B.RESULTS_ALL_NAME
        orig = p.read_text()
        p.write_text(orig + " ")
        rec1, ref1 = self.ensure()
        self.assertEqual(ref1["status"], B.STATUS_FAILED)
        self.assertTrue(ref1["failure_reason"])
        p.write_text(orig)
        rec2, ref2 = self.ensure()
        self.assertEqual(ref2["status"], B.STATUS_OK)
        rec3, ref3 = self.ensure()
        self.assertEqual(ref3, ref2)
        self.assertEqual(len(self.files()), 2)   # 失敗の記録は消さない
        self.assertEqual(B.load_for_rerun("20261115", self.out)[1], ref2)

    def test_broken_or_unknown_record_stops_without_new_record(self):
        d = self.out / "20261115" / "horse_style"
        d.mkdir(parents=True)
        for body in ("{壊れた", json.dumps({"status": "途中", "target_date": "20261115", "run_id": "a"})):
            (d / "a.json").write_text(body)
            with self.assertRaises(B.StyleRecordError):
                self.ensure()
            self.assertEqual([p.name for p in self.files()], ["a.json"])

    def test_reuse_checks_record_fields(self):
        rec, ref = self.ensure()
        path = self.files()[0]
        good = json.loads(path.read_text())
        bad_cases = [
            {**good, "target_date": "20261114"},
            {**good, "run_id": "other"},
            {**good, "style_build_version": "古い版"},
            {**good, "finished_at": T_DEADLINE.isoformat()},
            {**good, "horses": {}},
            {**good, "counts": {**good["counts"], "horses": 999}},
            {**good, "horses": {**good["horses"], "x": {"style": "逃げ"}}},
        ]
        for i, b in enumerate(bad_cases):
            path.write_text(json.dumps(b, ensure_ascii=False))
            with self.assertRaises(B.StyleRecordError, msg=i):
                self.ensure()
        self.assertEqual(len(self.files()), 1)

    def test_two_successes_stop(self):
        rec, ref = self.ensure()
        p = self.files()[0]
        other = json.loads(p.read_text())
        other["run_id"] = "z"
        (p.parent / "z.json").write_text(json.dumps(other, ensure_ascii=False))
        with self.assertRaises(B.StyleRecordError):
            self.ensure()

    def test_rerun_without_record_stops(self):
        with self.assertRaises(B.StyleRecordError):
            B.load_for_rerun("20261115", self.out)

    def test_save_failure_raises(self):
        d = self.out / "20261115"
        d.mkdir(parents=True)
        (d / "horse_style").write_text("x")   # フォルダを作れない
        with self.assertRaises(Exception):
            self.ensure()


class BindAndAxisTest(RepoCase):
    def setUp(self):
        super().setUp()
        self.rec, self.ref = B.ensure_style("20261115", self.out, self.repo, self.clock(T_OK, T_OK, T_LAST))
        ids = {str(i): f"{i:010d}" for i in range(1, 9)}
        ids["9"] = "0000009999"   # 履歴の無い馬
        ids["10"] = "0000000010"  # 取消
        self.ids = ids
        active = {n: h for n, h in ids.items() if n != "10"}
        bound = B.bind_race(self.rec, active)
        self.rdata = {"race_id": "x", "horse_id_map": ids, "scratched_nums": {"10": "取消"},
                      "quality": {"horse_style": self.ref},
                      "horse_style": {"run": self.ref["run"], "sha256": self.ref["sha256"], "horses": bound}}

    def load(self, rdata=None):
        import axis_record as A
        return A.load_style_entry(self.out / "20261115", "20261115", "東京1R", rdata or self.rdata, {})

    def test_bind_states(self):
        b = self.rdata["horse_style"]["horses"]
        self.assertEqual(set(b), {str(i) for i in range(1, 10)})
        self.assertEqual(b["1"]["status"], B.HS_OK)
        self.assertEqual(b["9"]["status"], B.HS_NONE)
        self.assertEqual(B.bind_race(None, {"1": "a"}), {})

    def test_axis_ok(self):
        part = self.load()
        self.assertEqual(part["status"], B.STATUS_OK)

    def test_axis_rejects(self):
        import axis_record as A
        import copy

        def broken(fn):
            r = copy.deepcopy(self.rdata)
            fn(r)
            return r
        cases = {
            "1頭抜けている": lambda r: r["horse_style"]["horses"].pop("3"),
            "余分な馬": lambda r: r["horse_style"]["horses"].__setitem__("10", {"horse_id": "0000000010"}),
            "馬IDの対応": lambda r: r["horse_style"]["horses"]["2"].__setitem__("horse_id", "0000000003"),
            "値": lambda r: r["horse_style"]["horses"]["1"].__setitem__("style", "差し"),
            "参照なし": lambda r: r["quality"].__setitem__("horse_style", None),
            "ハッシュ違い": lambda r: r["quality"]["horse_style"].__setitem__("sha256", "0" * 64),
            "記録が無い": lambda r: r["quality"]["horse_style"].__setitem__("run", "nothing"),
            "状態の違い": lambda r: r["quality"]["horse_style"].__setitem__("status", B.STATUS_FAILED),
        }
        for name, fn in cases.items():
            with self.assertRaises(A.RecordError, msg=name):
                self.load(broken(fn))

    def test_axis_old_scoring_without_key_passes(self):
        r = dict(self.rdata, quality={})
        self.assertIsNone(self.load(r))

    def test_axis_explicit_failure_passes_and_reason_must_match(self):
        import axis_record as A
        d = self.out / "20261116" / "horse_style"
        d.mkdir(parents=True)
        fail = {"status": B.STATUS_FAILED, "target_date": "20261116", "run_id": "f1",
                "style_build_version": B.STYLE_BUILD_VERSION, "finished_at": T_OK.isoformat(),
                "failure": {"reason": "入力に commit されていない変更がある: x"}}
        (d / "f1.json").write_text(json.dumps(fail, ensure_ascii=False))
        ref = B.ref_of(d / "f1.json", fail)
        r = {"quality": {"horse_style": ref}, "horse_id_map": {}, "scratched_nums": {}}
        part = A.load_style_entry(self.out / "20261116", "20261116", "東京1R", r, {})
        self.assertEqual(part["status"], B.STATUS_FAILED)
        r["quality"]["horse_style"] = {**ref, "failure_reason": "別の理由"}
        with self.assertRaises(A.RecordError):
            A.load_style_entry(self.out / "20261116", "20261116", "東京1R", r, {})


class CliTest(unittest.TestCase):
    def test_no_args_does_not_write(self):
        import io
        from contextlib import redirect_stdout
        before = B.LEGACY_STYLE_PATH.stat().st_mtime if B.LEGACY_STYLE_PATH.exists() else None
        with redirect_stdout(io.StringIO()):
            self.assertEqual(B.main([]), 2)
        after = B.LEGACY_STYLE_PATH.stat().st_mtime if B.LEGACY_STYLE_PATH.exists() else None
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
