"""
axis_record.py（買い目の軸の再検証の記録・欠けの検査・記録の復元）の回帰テスト。
計画: docs/axis_recheck_plan_202611.md（2026-11-12 承認）。2026-11-12 の外部レビューで求められた試験を含む。

実行: python3 -m unittest discover -s tests -v
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import axis_record as A  # noqa: E402

D = "20261114"                                         # 土曜
BEFORE = datetime(2026, 11, 13, 17, 30, tzinfo=A.JST)  # 期限（11/14 00:00）より前
AFTER = datetime(2026, 11, 14, 0, 0, tzinfo=A.JST)     # ちょうど期限
NO_ISSUES = mock.patch.object(A, "race_quality_issues", lambda r, i=None: [])


def csv_row(venue, rnum, num, name, ranks=("1位", "1位", "1位")):
    return [venue, rnum, num, name, "90", ranks[0], "90", ranks[1], "90", ranks[2]]


def write_csv(date_dir: Path, rows, header=True):
    date_dir.mkdir(parents=True, exist_ok=True)
    with open(date_dir / A.CSV_NAME, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if header:
            w.writerow(A.CSV_COLUMNS)
        w.writerows(rows)


def race(scored, names=None, pop=None, scratched=None, **extra):
    names = names if names is not None else {str(i): f"ウマ{i}" for i in range(1, 9)}
    return {"scored": [{"馬番": n, "馬名": names.get(n, f"ウマ{n}"), "score": s} for n, s in scored],
            "horse_name_full": names, "horse_id_map": {n: f"id{n}" for n in names},
            "pop_map": pop if pop is not None else {n: n for n in names},
            "scratched_nums": scratched or {}, "quality": {}, **extra}


def rows_of(nums, ranks=None):
    ranks = ranks or {}
    return [dict(zip(A.CSV_COLUMNS, csv_row("東京", "11R", n, f"ウマ{n}", ranks.get(n, ("1位", "2位", "3位")))))
            for n in nums]


class DecideTest(unittest.TestCase):
    """軸の決め方（計画 4.1）"""

    def decide(self, rdata, rows):
        with NO_ISSUES:
            return A.decide_race(rdata, rows)[0]

    def test_top_tie_is_no_axis_but_lower_tie_is_not(self):
        """最上位の同点だけ軸なし。10・8・8点 → 10点の馬。10・10・9点 → 軸なし"""
        c = self.decide(race([("1", 10), ("2", 8), ("3", 8)]), rows_of(["1", "2", "3"]))
        self.assertEqual((c["候補1"]["state"], c["候補1"]["num"]), ("軸あり", "1"))
        self.assertEqual(c["候補2"]["num"], "1")
        c = self.decide(race([("1", 10), ("2", 10), ("3", 9)]), rows_of(["1", "2", "3"]))
        self.assertEqual((c["候補1"]["state"], c["候補1"]["reason"], c["候補1"]["tied"]),
                         ("軸なし", "同点で決められない", ["1", "2"]))
        self.assertEqual(c["候補2"]["reason"], "同点で決められない")

    def test_candidate2_needs_n_points(self):
        c = self.decide(race([("1", 7), ("2", 6)]), rows_of(["1", "2"]))
        self.assertEqual(c["候補1"]["num"], "1")
        self.assertEqual((c["候補2"]["state"], c["候補2"]["reason"]), ("軸なし", "8点未満"))
        c = self.decide(race([("1", 9), ("2", 9)]), rows_of(["1", "2"]))
        self.assertEqual(c["候補2"]["reason"], "同点で決められない")
        c = self.decide(race([("1", 8)]), rows_of(["1"]))
        self.assertEqual(c["候補2"]["state"], "軸あり")   # 境界: ちょうど8点は軸あり

    def test_candidate3_rank_sum_and_tie(self):
        c = self.decide(race([("1", 5), ("2", 5)]),
                        rows_of(["1", "2"], {"1": ("1位", "1位", "1位"), "2": ("2位", "1位", "1位")}))
        self.assertEqual((c["候補3"]["num"], c["候補3"]["rank_sum"]), ("1", 3))
        c = self.decide(race([("1", 5), ("2", 4), ("3", 4)]),
                        rows_of(["1", "2", "3"], {"1": ("1位", "1位", "2位"), "2": ("2位", "1位", "1位"),
                                                  "3": ("3位", "3位", "3位")}))
        self.assertEqual((c["候補3"]["reason"], c["候補3"]["tied"]), ("同点で決められない", ["1", "2"]))

    def test_candidate3_unreadable_rank_makes_whole_candidate3_unknown(self):
        """1頭でも順位が読めなければ、読めた馬だけで選ばず、候補3全体を確認不能にする"""
        c = self.decide(race([("1", 5), ("2", 4)]),
                        rows_of(["1", "2"], {"1": ("1位", "1位", "1位"), "2": ("2位", "?", "1位")}))
        self.assertEqual((c["候補3"]["state"], c["候補3"]["reason"]), ("軸なし", "確認不能"))
        self.assertEqual(c["候補1"]["num"], "1")   # ほかの候補には影響しない

    def test_baseline_uses_all_runners_from_shutuba(self):
        """人気が欠けた出走馬（pop_map に無い馬）を見落とさない"""
        names = {str(i): f"ウマ{i}" for i in range(1, 6)}
        pop = {"1": "2", "2": "1", "3": "3", "4": "4"}         # 5番の人気が無い
        c = self.decide(race([("1", 5)], names=names, pop=pop), rows_of(["1"]))
        self.assertEqual((c["比べる基準"]["state"], c["比べる基準"]["reason"]), ("軸なし", "確認不能"))
        self.assertIn("['5']", c["比べる基準"]["detail"])

    def test_baseline_excludes_scratched_and_detects_tie(self):
        names = {str(i): f"ウマ{i}" for i in range(1, 6)}
        pop = {"1": "2", "2": "1", "3": "3", "4": "4"}          # 5番は取消（人気なし）
        c = self.decide(race([("1", 5)], names=names, pop=pop, scratched={"5": "取消"}), rows_of(["1"]))
        self.assertEqual((c["比べる基準"]["num"], c["比べる基準"]["pop"]), ("2", 1))
        pop = {"1": "1", "2": "1", "3": "3", "4": "4", "5": "5"}
        c = self.decide(race([("1", 5)], names=names, pop=pop), rows_of(["1"]))
        self.assertEqual(c["比べる基準"]["reason"], "同点で決められない")
        c = self.decide(race([("1", 5)], names=names, pop={}), rows_of(["1"]))
        self.assertEqual(c["比べる基準"]["reason"], "確認不能")

    def test_scratched_candidate_is_excluded_from_candidate3(self):
        """CSV にある取消の馬は、候補3の対象から外す（採点の候補にもいない）"""
        c = self.decide(race([("1", 5)], scratched={"2": "取消"}),
                        rows_of(["1", "2"], {"1": ("3位", "3位", "3位"), "2": ("1位", "1位", "1位")}))
        self.assertEqual(c["候補3"]["num"], "1")

    def test_quality_problem_is_no_axis_for_all(self):
        rdata = race([("1", 10)])
        with mock.patch.object(A, "race_quality_issues", lambda r, i=None: ["何かの問題"]):
            c, issues = A.decide_race(rdata, rows_of(["1"]))
        self.assertEqual(issues, ["何かの問題"])
        self.assertTrue(all(v == {"state": "軸なし", "reason": "品質の問題"} for v in c.values()))
        self.assertEqual(set(c), set(A.CANDIDATES))

    def test_axis_horse_without_horse_id_is_record_error(self):
        """軸の馬の馬IDが無い・空・形が違う → 壊れた記録を作らず、記録できないにする"""
        for bad in (None, "", "12/3"):
            r = race([("1", 10), ("2", 5)])
            r["horse_id_map"]["1"] = bad
            if bad is None:
                del r["horse_id_map"]["1"]
            with NO_ISSUES, self.assertRaises(A.RecordError):
                A.decide_race(r, rows_of(["1", "2"]))

    def test_broken_input_is_record_error_not_no_axis(self):
        """入力の破損・必須の項目の欠けは、品質の問題（軸なし）にしない"""
        with NO_ISSUES:
            with self.assertRaises(A.RecordError):
                A.decide_race(race([("1", 5)], names={}), rows_of(["1"]))
            with self.assertRaises(A.RecordError):
                A.decide_race(race([("1", "5")]), rows_of(["1"]))          # 点数が数でない
            with self.assertRaises(A.RecordError):
                A.decide_race(race([("1", 5)]), rows_of(["1", "2"]))       # CSV と採点の候補が違う
            r = race([("1", 5)])
            del r["scored"]
            with self.assertRaises(A.RecordError):
                A.decide_race(r, rows_of(["1"]))


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_missing_csv_is_not_zero_races(self):
        s, _ = A.load_schedule(self.tmp / D)
        self.assertEqual(s["status"], "確定できない")

    def test_header_only_csv_is_zero_races(self):
        write_csv(self.tmp / D, [])
        s, _ = A.load_schedule(self.tmp / D)
        self.assertEqual((s["status"], s["races"]), ("確定", []))

    def test_broken_csv_is_unknown(self):
        (self.tmp / D).mkdir()
        (self.tmp / D / A.CSV_NAME).write_text("開催場,馬番\n東京,1\n", encoding="utf-8")
        self.assertEqual(A.load_schedule(self.tmp / D)[0]["status"], "確定できない")
        write_csv(self.tmp / D, [csv_row("東京", "11R", "1", "ウマ1")[:5]])
        self.assertEqual(A.load_schedule(self.tmp / D)[0]["status"], "確定できない")

    def test_order_and_labels(self):
        write_csv(self.tmp / D, [csv_row("東京", "11R", "3", "ウマ3"), csv_row("京都", "1R", "2", "ウマ2"),
                                 csv_row("東京", "11R", "5", "ウマ5")])
        s, by = A.load_schedule(self.tmp / D)
        self.assertEqual(s["races"], ["東京11R", "京都1R"])
        self.assertEqual([r["馬番"] for r in by["東京11R"]], ["3", "5"])


RUN = "20261113T170000000000_abcdef12"


def with_prev(rdata: dict, race_id: str = "202605050411") -> dict:
    """前走データの記録を指す採点結果にする"""
    return {**rdata, "race_id": race_id, "quality": {**rdata.get("quality", {}), "prev_inputs_run": RUN}}


def write_prev(out: Path, races: dict, run: str = RUN, target: str = D):
    """prev_inputs/{実行ID}.json。races は {label: (race_id, [馬ID...])}"""
    d = out / target / "prev_inputs"
    d.mkdir(parents=True, exist_ok=True)
    data = {"run_id": run, "target_date": target,
            "races": {l: {"race_id": rid, "horses": {h: {"status": "取れた"} for h in hs}} for l, (rid, hs) in races.items()}}
    (d / f"{run}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def make_day(out: Path, races_rows: dict, pickup_races: dict):
    rows = [r for rs in races_rows.values() for r in rs]
    write_csv(out / D, rows)
    (out / D / "pickup_scores.json").write_text(json.dumps(
        {"date": D, "scoring_version": "v5.2", "races": pickup_races}, ensure_ascii=False), encoding="utf-8")


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "output"
        make_day(self.out, {"東京11R": [csv_row("東京", "11R", "1", "ウマ1"), csv_row("東京", "11R", "2", "ウマ2")],
                            "京都1R": [csv_row("京都", "1R", "3", "ウマ3")]},
                 {"東京11R": race([("1", 9), ("2", 7)]), "京都1R": {"error": "ページを取れない"}})
        self.patch = NO_ISSUES
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp)

    def rec(self):
        return json.loads((self.out / D / A.RECORD_NAME).read_text(encoding="utf-8"))

    def record(self, now=BEFORE):
        with mock.patch("builtins.print"):
            return A.cmd_record(D, now=now, output_dir=self.out, meta={"versions": {}, "code_commit": "x"})

    def test_record_and_scoring_error(self):
        """採点エラーのレースは、保留にせず、全候補を「軸なし（採点エラー）」として明示的に記録する"""
        self.assertEqual(self.record(), 0)
        r = self.rec()
        self.assertEqual(r["schedule"]["races"], ["東京11R", "京都1R"])
        self.assertEqual(r["races"]["東京11R"]["candidates"]["候補2"]["num"], "1")
        self.assertEqual(r["races"]["東京11R"]["mode"], "期限内")
        c = r["races"]["京都1R"]["candidates"]
        self.assertEqual(set(c), set(A.CANDIDATES))
        self.assertTrue(all(v["state"] == "軸なし" and v["reason"] == "採点エラー" for v in c.values()))
        self.assertNotIn("pending", r)

    def test_same_input_does_nothing_and_changed_input_stops(self):
        self.record()
        first = self.rec()["races"]["東京11R"]
        self.assertEqual(self.record(), 0)
        self.assertEqual(self.rec()["races"]["東京11R"], first)
        self.assertEqual(self.rec()["runs"][-1]["unchanged"], ["東京11R", "京都1R"])   # 採点エラーのレースも記録がある
        # 東京11R の採点が変わった → 書き換えず、問題として返す
        p = json.loads((self.out / D / "pickup_scores.json").read_text(encoding="utf-8"))
        p["races"]["東京11R"]["scored"][0]["score"] = 3
        (self.out / D / "pickup_scores.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.record(), 1)
        self.assertEqual(self.rec()["races"]["東京11R"], first)

    def test_top_level_change_counts_as_different_input(self):
        """レースの外の共通の情報（ファイルの上位の版など）が変わっても、同じ記録とはしない"""
        self.record()
        p = json.loads((self.out / D / "pickup_scores.json").read_text(encoding="utf-8"))
        p["scoring_version"] = "v9"
        (self.out / D / "pickup_scores.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.record(), 1)

    def test_rule_change_counts_as_different_input(self):
        self.record()
        with mock.patch.object(A, "AXIS_PLAN_VERSION", "2099-01-01"):
            self.assertEqual(self.record(), 1)

    def test_rescored_or_broken_after_scoring_error_is_a_problem(self):
        """採点エラーで記録した後に、採点し直された・壊れた → 記録は書き換えず、問題として返す"""
        self.record()
        first = self.rec()["races"]["京都1R"]
        for new in (race([("3", 6)]), {"scored": "壊れた"}):
            p = json.loads((self.out / D / "pickup_scores.json").read_text(encoding="utf-8"))
            p["races"]["京都1R"] = new
            (self.out / D / "pickup_scores.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(self.record(), 1)
            self.assertEqual(self.rec()["races"]["京都1R"], first)

    def test_existing_empty_or_null_record_is_not_overwritten(self):
        """既存のファイルが {} や null → 新しい記録として扱わず、上書きしない"""
        for text in ("{}", "null", "[]"):
            (self.out / D / A.RECORD_NAME).write_text(text, encoding="utf-8")
            with self.assertRaises(A.RecordFailure):
                self.record()
            self.assertEqual((self.out / D / A.RECORD_NAME).read_text(encoding="utf-8"), text)

    def test_pickup_date_must_match(self):
        p = json.loads((self.out / D / "pickup_scores.json").read_text(encoding="utf-8"))
        p["date"] = "20261108"
        (self.out / D / "pickup_scores.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.record(), 1)
        self.assertEqual(self.rec()["races"], {})
        self.assertTrue(any("対象日" in x for x in self.rec()["runs"][-1]["problems"]))

    def test_csv_name_must_match(self):
        """CSV の馬名が別の馬なら、馬番の集合が同じでも記録しない"""
        write_csv(self.out / D, [csv_row("東京", "11R", "1", "ウマ1"), csv_row("東京", "11R", "2", "ベツノウマ"),
                                 csv_row("京都", "1R", "3", "ウマ3")])
        self.assertEqual(self.record(), 1)
        self.assertNotIn("東京11R", self.rec()["races"])

    def test_prev_inputs_required_and_included(self):
        """前走データの記録を指すレースは、記録が無い・合わなければ記録しない。記録の中身も同じかどうかの判定に入る"""
        p = json.loads((self.out / D / "pickup_scores.json").read_text(encoding="utf-8"))
        p["races"]["東京11R"] = with_prev(p["races"]["東京11R"])
        (self.out / D / "pickup_scores.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.record(), 1)                       # ファイルが無い
        self.assertNotIn("東京11R", self.rec()["races"])
        write_prev(self.out, {"東京11R": ("202605050411", ["id1"])})   # 候補の2番（id2）が無い
        self.assertEqual(self.record(), 1)
        write_prev(self.out, {"東京11R": ("202605050411", ["id1", "id2"])}, target="20261108")  # 別の日のもの
        self.assertEqual(self.record(), 1)
        write_prev(self.out, {"東京11R": ("999", ["id1", "id2"])})     # race_id が違う
        self.assertEqual(self.record(), 1)
        self.assertNotIn("東京11R", self.rec()["races"])
        write_prev(self.out, {"東京11R": ("202605050411", ["id1", "id2"])})
        self.assertEqual(self.record(), 0)
        self.assertIn("東京11R", self.rec()["races"])
        write_prev(self.out, {"東京11R": ("202605050411", ["id1", "id2", "id3"])})   # 前走データの中身が変わった
        self.assertEqual(self.record(), 1)

    def test_after_deadline_writes_nothing(self):
        self.assertEqual(self.record(now=AFTER), 0)
        self.assertFalse((self.out / D / A.RECORD_NAME).exists())

    def test_schedule_is_saved_before_computing(self):
        """軸の計算が落ちても、予定のレースは先に保存されている"""
        with mock.patch.object(A, "compute_day", side_effect=RuntimeError("落ちた")):
            with self.assertRaises(RuntimeError):
                self.record()
        r = self.rec()
        self.assertEqual(r["schedule"]["races"], ["東京11R", "京都1R"])
        self.assertEqual(r["races"], {})

    def test_missing_csv_fails_not_zero(self):
        (self.out / D / A.CSV_NAME).unlink()
        with self.assertRaises(A.RecordFailure):
            self.record()
        self.assertEqual(self.rec()["schedule"]["status"], "確定できない")

    def test_cannot_record_is_reported_not_converted(self):
        p = json.loads((self.out / D / "pickup_scores.json").read_text(encoding="utf-8"))
        del p["races"]["東京11R"]["horse_name_full"]
        (self.out / D / "pickup_scores.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.record(), 1)
        r = self.rec()
        self.assertNotIn("東京11R", r["races"])
        self.assertTrue(any("記録できない" in x for x in r["runs"][-1]["problems"]))

    def test_existing_record_with_broken_new_input_is_a_problem(self):
        """記録のあるレースの入力が変わり、今は記録できない → 黙って飛ばさず、問題として返す（記録は書き換えない）"""
        self.record()
        first = self.rec()["races"]["東京11R"]
        p = json.loads((self.out / D / "pickup_scores.json").read_text(encoding="utf-8"))
        del p["races"]["東京11R"]["horse_name_full"]
        (self.out / D / "pickup_scores.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.record(), 1)
        self.assertEqual(self.rec()["races"]["東京11R"], first)
        self.assertTrue(any("入力・規則が違う" in x for x in self.rec()["runs"][-1]["problems"]))

    def test_interrupted_write_keeps_existing_record(self):
        """置き換えの途中で止まっても、既存の記録は壊れず、一時ファイルも残らない"""
        self.record()
        before = (self.out / D / A.RECORD_NAME).read_bytes()
        with mock.patch.object(A.os, "replace", side_effect=OSError("途中で止まった")):
            with self.assertRaises(OSError):
                self.record()
        self.assertEqual((self.out / D / A.RECORD_NAME).read_bytes(), before)
        self.assertEqual(list((self.out / D).glob("*.tmp")), [])

    def test_second_run_while_locked_fails(self):
        """二重起動: ほかのプロセスがロックを持っていれば、書かずに止まる"""
        lock = self.out / D / A.LOCK_NAME
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import fcntl,sys,time;f=open(sys.argv[1],'a');"
                                   "fcntl.flock(f.fileno(),fcntl.LOCK_EX);print('ok',flush=True);time.sleep(30)",
                                   str(lock)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "ok")
            with self.assertRaises(A.RecordFailure):
                self.record()
            self.assertFalse((self.out / D / A.RECORD_NAME).exists())
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()

    def test_broken_record_file_is_not_overwritten(self):
        (self.out / D / A.RECORD_NAME).write_text("{壊れた", encoding="utf-8")
        with self.assertRaises(A.RecordFailure):
            self.record()
        self.assertEqual((self.out / D / A.RECORD_NAME).read_text(encoding="utf-8"), "{壊れた")


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "output"
        (self.tmp / "jra_calendar_2026.json").write_text(json.dumps({"dates": ["20261114", "20261115"]}),
                                                         encoding="utf-8")
        make_day(self.out, {"東京11R": [csv_row("東京", "11R", "1", "ウマ1")]}, {"東京11R": race([("1", 9)])})
        with NO_ISSUES, mock.patch("builtins.print"):
            A.cmd_record(D, now=BEFORE, output_dir=self.out, meta={})

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def check(self, now):
        with mock.patch("builtins.print") as p:
            rc = A.cmd_check(now=now, output_dir=self.out, start=D, base_dir=self.tmp)
        out = "\n".join(" ".join(map(str, c.args)) for c in p.call_args_list)
        return rc, out

    def status(self):
        return json.loads((self.out / A.STATUS_NAME).read_text(encoding="utf-8"))

    def test_missing_day_record_is_notified(self):
        """11/15 は記録も CSV も無い → 予定を確定できない記録なし（対象0件にしない）"""
        rc, out = self.check(datetime(2026, 11, 15, 12, 0, tzinfo=A.JST))
        self.assertEqual(rc, 2)
        self.assertIn("AXIS_NOTIFY", out)
        self.assertIn("20261115", out)
        self.assertEqual(self.status()["record_gaps"], 1)

    def test_payout_gap_notified_only_after_wednesday(self):
        write_csv(self.out / "20261115", [])     # 11/15 は候補0件（正常）
        (self.out / "20261115" / A.RECORD_NAME).write_text(json.dumps(
            {"schedule": {"status": "確定", "races": []}, "races": {}, "runs": []}), encoding="utf-8")
        rc, out = self.check(datetime(2026, 11, 18, 17, 0, tzinfo=A.JST))   # 水曜の期限前
        self.assertEqual(rc, 0)
        self.assertEqual(self.status()["payout_gaps_waiting"], 4)          # 候補1・2・3・基準の4つ（同じ馬）
        rc, out = self.check(datetime(2026, 11, 19, 0, 0, tzinfo=A.JST))    # 期限切れ
        self.assertEqual(rc, 2)
        self.assertIn("払戻などの欠け", out)
        # 結果と払戻がそろえば欠けは無くなる（当たり外れは出さない）
        (self.out / D / "race_results.json").write_text(json.dumps(
            {"東京11R": [{"rank": 5, "num": "1", "name": "ウマ1"}]}, ensure_ascii=False), encoding="utf-8")
        (self.out / D / "payouts_jra.json").write_text(json.dumps(
            {"東京11R": {"tansho": {"2": 300}, "fukusho": {"2": 120, "3": 150, "4": 200}}}), encoding="utf-8")
        rc, out = self.check(datetime(2026, 11, 19, 0, 0, tzinfo=A.JST))
        self.assertEqual(rc, 0)
        self.assertNotIn("当たり", out)

    def test_broken_candidate_record_is_notified(self):
        """候補の記録の欠け・知らない状態・必須の項目の欠けは「記録不正」として知らせる"""
        path = self.out / D / A.RECORD_NAME
        good = json.loads(path.read_text(encoding="utf-8"))
        now = datetime(2026, 11, 14, 12, 0, tzinfo=A.JST)
        write_csv(self.out / "20261115", [])
        (self.out / "20261115" / A.RECORD_NAME).write_text(json.dumps(
            {"schedule": {"status": "確定", "races": []}, "races": {}, "runs": []}), encoding="utf-8")
        self.assertEqual(self.check(now)[0], 0)
        breaks = [
            lambda c: c.pop("候補2"),
            lambda c: c["候補1"].update(state="たぶん軸"),
            lambda c: c["候補1"].pop("name"),
            lambda c: c["候補1"].update(horse_id=None),
            lambda c: c["候補1"].update(horse_id=""),
            lambda c: c["候補1"].update(horse_id="20/19"),
            lambda c: c["候補3"].update(state="軸なし", reason=""),
        ]
        for brk in breaks:
            r = json.loads(json.dumps(good))
            brk(r["races"]["東京11R"]["candidates"])
            path.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
            rc, out = self.check(now)
            self.assertEqual(rc, 2, out)
            self.assertIn("記録不正", out)
        path.write_text("{}", encoding="utf-8")
        rc, out = self.check(now)
        self.assertEqual(rc, 2)
        self.assertIn("記録ファイルの形が違う", out)

    def test_refund_and_nonfinisher_are_confirmed(self):
        (self.out / D / "race_results.json").write_text(json.dumps({"東京11R": [{"num": "2", "name": "ウマ2"}]}),
                                                        encoding="utf-8")
        (self.out / D / "payouts_jra.json").write_text(json.dumps(
            {"東京11R": {"tansho": {"2": 300}, "fukusho": {"2": 120}, "refund": {"umaban": ["1"]}}}), encoding="utf-8")
        self.assertIsNone(A.purchase_gap(self.out / D, "東京11R", "1", "ウマ1", {}))
        (self.out / D / "payouts_jra.json").write_text(json.dumps(
            {"東京11R": {"tansho": {"2": 300}, "fukusho": {"2": 120}}}), encoding="utf-8")
        self.assertIn("返還・中止・失格の記録も無い", A.purchase_gap(self.out / D, "東京11R", "1", "ウマ1", {}))
        (self.out / D / "race_nonfinishers.json").write_text(json.dumps(
            {"東京11R": [{"status": "中止", "num": "1", "name": "ウマ1"}]}, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(A.purchase_gap(self.out / D, "東京11R", "1", "ウマ1", {}))   # 中止で、複勝の払戻はある

    def test_nonfinisher_without_fukusho_payout_is_a_gap(self):
        """中止・失格（不的中）でも、複勝の払戻を確かめられなければ欠けとして残す（購入が成立したかは別）"""
        (self.out / D / "race_results.json").write_text(json.dumps({"東京11R": [{"num": "2", "name": "ウマ2"}]}),
                                                        encoding="utf-8")
        (self.out / D / "race_nonfinishers.json").write_text(json.dumps(
            {"東京11R": [{"status": "失格", "num": "1", "name": "ウマ1"}]}, ensure_ascii=False), encoding="utf-8")
        for payouts in ({}, {"東京11R": {"tansho": {"2": 300}}}, {"東京11R": {"tansho": {"2": 300}, "fukusho": {}}}):
            (self.out / D / "payouts_jra.json").write_text(json.dumps(payouts), encoding="utf-8")
            self.assertIn("中止・失格だが、複勝の払戻が無い", A.purchase_gap(self.out / D, "東京11R", "1", "ウマ1", {}))

    def test_check_failure_raises_and_keeps_last_success(self):
        """検査そのものが失敗したら例外（通知の対象）。最後の成功の日時は更新しない"""
        write_csv(self.out / "20261115", [])
        (self.out / "20261115" / A.RECORD_NAME).write_text(json.dumps(
            {"schedule": {"status": "確定", "races": []}, "races": {}, "runs": []}), encoding="utf-8")
        self.check(datetime(2026, 11, 15, 12, 0, tzinfo=A.JST))
        last = self.status()["last_completed_at"]
        (self.tmp / "jra_calendar_2026.json").unlink()
        with self.assertRaises(A.RecordFailure):
            self.check(datetime(2026, 11, 16, 12, 0, tzinfo=A.JST))
        self.assertEqual(self.status()["last_completed_at"], last)
        # main() は失敗を終了コード1で返す（run_cron.sh が通知する）
        with mock.patch.object(A, "cmd_check", side_effect=A.RecordFailure("x")), \
                mock.patch("builtins.print"):
            self.assertEqual(A.main(["check"]), 1)

    def test_stale_check_is_reported_by_record(self):
        with mock.patch.object(A, "AXIS_START_DATE", D):
            self.assertIn("一度も", A.check_status_stale(BEFORE, self.out))
            A.atomic_write_json(self.out / A.STATUS_NAME,
                                {"last_completed_at": A.iso(BEFORE - timedelta(days=9))})
            self.assertIn("8日より前", A.check_status_stale(BEFORE, self.out))
            A.atomic_write_json(self.out / A.STATUS_NAME, {"last_completed_at": A.iso(BEFORE - timedelta(days=1))})
            self.assertIsNone(A.check_status_stale(BEFORE, self.out))

    def test_payout_deadline(self):
        self.assertEqual(A.payout_deadline("20261114"), datetime(2026, 11, 19, tzinfo=A.JST))  # 土 → 水の終わり
        self.assertEqual(A.payout_deadline("20261115"), datetime(2026, 11, 19, tzinfo=A.JST))  # 日
        self.assertEqual(A.payout_deadline("20261123"), datetime(2026, 11, 26, tzinfo=A.JST))  # 月（祝日）
        self.assertEqual(A.payout_deadline("20261118"), datetime(2026, 11, 26, tzinfo=A.JST))  # 水 → 翌週


def git(repo, *args, env=None):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return r.stdout.strip()


class RestoreTest(unittest.TestCase):
    """記録の復元（期限前に GitHub にあった commit の入力とコードだけで計算し直す）"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.repo = cls.tmp / "repo"
        cls.repo.mkdir()
        git(cls.repo, "init", "-q")
        git(cls.repo, "config", "user.email", "t@example.com")
        git(cls.repo, "config", "user.name", "t")
        for f in ("axis_record.py", "data_quality.py", "race_pickup.py", "prev_inputs.py", "scraper.py",
                  "index_table_check.py", "build_horse_style.py", "schedule_check.py"):
            shutil.copy(ROOT / f, cls.repo / f)
        git(cls.repo, "add", ".")
        git(cls.repo, "commit", "-q", "-m", "plan")
        cls.plan = git(cls.repo, "rev-parse", "HEAD")
        out = cls.repo / "output"
        make_day(out, {"東京11R": [csv_row("東京", "11R", "1", "ウマ1")]}, {"東京11R": race([("1", 9)])})
        git(cls.repo, "add", "output")
        # 古い日時の commit（期限より前の日時）。push は期限の後だった、という場面に使う
        env = {**os.environ, "GIT_COMMITTER_DATE": "2026-11-13T18:00:00+09:00",
               "GIT_AUTHOR_DATE": "2026-11-13T18:00:00+09:00"}
        git(cls.repo, "commit", "-q", "-m", f"data: {D}", env=env)
        cls.data = git(cls.repo, "rev-parse", "HEAD")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def setUp(self):
        self.out = Path(tempfile.mkdtemp()) / "output"
        (self.out / D).mkdir(parents=True)

    def restore(self, acts, **kw):
        with mock.patch.object(A, "AXIS_PLAN_COMMIT", self.plan), mock.patch("builtins.print"):
            return A.cmd_restore(D, "記録が失敗したため", now=AFTER + timedelta(days=1), output_dir=self.out,
                                 repo=self.repo, activity_fetcher=lambda: acts, **kw)

    def test_old_commit_date_pushed_after_deadline_is_refused(self):
        """commit の日時が期限前でも、期限前に push された記録が無ければ復元しない"""
        acts = [{"timestamp": "2026-11-14T03:00:00Z", "after": self.data}]   # 11/14 12:00 JST（期限後）
        with self.assertRaises(A.RecordFailure) as cm:
            self.restore(acts)
        self.assertIn("確かめられない", str(cm.exception))
        self.assertFalse((self.out / D / A.RECORD_NAME).exists())

    def test_any_kind_of_main_update_is_used(self):
        """pr_merge なども main の更新として扱う。期限前の最後の更新が pr_merge なら、その commit を使う"""
        acts = [{"timestamp": "2026-11-13T05:00:00Z", "after": self.plan, "activity_type": "push"},
                {"timestamp": "2026-11-13T09:00:00Z", "after": self.data, "activity_type": "pr_merge"}]
        self.assertEqual(self.restore(acts), 0)

    def test_last_update_deleting_main_is_refused(self):
        acts = [{"timestamp": "2026-11-13T05:00:00Z", "after": self.data, "activity_type": "push"},
                {"timestamp": "2026-11-13T09:00:00Z", "after": "0" * 40, "activity_type": "branch_deletion"}]
        with self.assertRaises(A.RecordFailure) as cm:
            self.restore(acts)
        self.assertIn("main が無い", str(cm.exception))

    def test_commit_without_inputs_is_refused(self):
        acts = [{"timestamp": "2026-11-13T09:00:00Z", "after": self.plan}]   # 入力の無い commit
        with self.assertRaises(A.RecordFailure) as cm:
            self.restore(acts)
        self.assertIn("無い", str(cm.exception))

    def test_commit_without_plan_is_refused(self):
        acts = [{"timestamp": "2026-11-13T09:00:00Z", "after": self.data}]
        with mock.patch.object(A, "AXIS_PLAN_COMMIT", "0" * 40), mock.patch("builtins.print"):
            with self.assertRaises(A.RecordFailure):
                A.cmd_restore(D, "理由", now=AFTER, output_dir=self.out, repo=self.repo,
                              activity_fetcher=lambda: acts)

    def test_restore_adds_missing_races_marked_as_restored(self):
        """期限前（11/13 18:00 JST）の push の commit を、取り出した作業場所のコードで計算し直す"""
        acts = [{"timestamp": "2026-11-13T09:00:00Z", "after": self.data},
                {"timestamp": "2026-11-14T03:00:00Z", "after": "f" * 40}]   # 期限後の push は使わない
        self.assertEqual(self.restore(acts), 0)
        r = json.loads((self.out / D / A.RECORD_NAME).read_text(encoding="utf-8"))
        x = r["races"]["東京11R"]
        self.assertEqual(x["mode"], "復元")
        self.assertEqual(x["restore"]["input_commit"], self.data)
        self.assertEqual(x["restore"]["reason"], "記録が失敗したため")
        self.assertEqual(x["code_commit"], self.data)
        self.assertFalse(x["code_dirty"])
        # 一時的な作業場所は片付いている
        self.assertNotIn("axis_restore_", git(self.repo, "worktree", "list"))

    def test_uncommitted_rule_file_is_dirty(self):
        """判定に使うファイルが commit されていなければ、未コミットの変更ありとする"""
        self.assertFalse(A.code_state(self.repo)["code_dirty"])
        extra = self.repo / "data_quality.py"
        original = extra.read_bytes()
        try:
            extra.write_bytes(original + b"\n# changed\n")
            self.assertTrue(A.code_state(self.repo)["code_dirty"])
        finally:
            extra.write_bytes(original)
        new = self.repo / "axis_record.py"
        git(self.repo, "rm", "-q", "--cached", "axis_record.py")
        try:
            self.assertTrue(A.code_state(self.repo)["code_dirty"])   # 追跡していない
        finally:
            git(self.repo, "add", "axis_record.py")
            git(self.repo, "reset", "-q")
            self.assertTrue(new.exists())

    def test_restore_does_not_touch_existing_records(self):
        acts = [{"timestamp": "2026-11-13T09:00:00Z", "after": self.data}]
        existing = {"date": D, "schedule": {"status": "確定", "races": ["東京11R"],
                                            "csv_sha256": A.sha256_file(self.repo / "output" / D / A.CSV_NAME)},
                    "races": {"東京11R": {"input_key": "別の入力", "candidates": {}}}, "runs": []}
        (self.out / D / A.RECORD_NAME).write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.restore(acts), 1)   # 入力が違う → 書き換えず、問題として返す
        r = json.loads((self.out / D / A.RECORD_NAME).read_text(encoding="utf-8"))
        self.assertEqual(r["races"]["東京11R"]["input_key"], "別の入力")


if __name__ == "__main__":
    unittest.main()
