"""
評価用スクリプト new_candidates_variants.py の回帰テスト（2026-11-06 外部レビュー指摘）。
実ページの表（tests/fixtures/rank_202605041007_average.html、東京7R。「96*」の3番が1位）を3指数とも同じ表として使う。
この表では、今の定義の候補に3番が入り、旧方式（6f2ed8d）では「96*」を読めずに3番が外れる（=新しく候補）。

実行: python3 -m unittest discover -s tests -v
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import new_candidates_variants as ncv  # noqa: E402
import scraper  # noqa: E402
from data_quality import build_quality  # noqa: E402
from prev_inputs import PREV_FETCH_VERSION  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
DATE = "20261107"
LABEL = "東京7R"


def tables():
    df = scraper.parse_rank_html((FIX / "rank_202605041007_average.html").read_text(encoding="utf-8"))
    return {LABEL: {"average": df, "distance": df.copy(), "course": df.copy()}}


def good_quality(cands):
    src = {"index_reader_version": scraper.INDEX_READER_VERSION,
           "candidate_rule_version": ncv.FIXED["candidate_rule_version"],
           "races": {LABEL: {"modes": {"近走平均": "type=rank", "当該距離": "type=rank", "当該コース": "type=rank"}}}}
    prev = {"prev_fetch": PREV_FETCH_VERSION, "prev_inputs_run": "r",
            "prev_status": {"candidates": {n: "取れた" for n in cands}, "others": {}, "top1": "判定"}}
    return build_quality("v", src, LABEL, prev=prev, table_check={"version": "t", "ok": True, "modes": {}, "problems": []})


class VariantsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out, self.summ = root / "output", root / "summary"
        (self.out / DATE).mkdir(parents=True)
        self.summ.mkdir()
        (self.out / f"{DATE}.xlsx").write_bytes(b"")   # 有無だけ見る（読み込みは差し替える）
        t = tables()[LABEL]
        self.new_def = sorted({str(r["馬番"]) for r in scraper.find_triple_top5_rows(LABEL, {k: v.copy() for k, v in t.items()})},
                              key=int)
        self.assertIn("3", self.new_def)
        scores = {n: 10 - i for i, n in enumerate(self.new_def)}   # 3番が最高点（🔥・軸）
        scored = [{"馬番": n, "馬名": f"馬{n}", "score": scores[n], "breakdown": []} for n in self.new_def]
        self.pickup = {"date": DATE, "scoring_version": "v5.1",
                       "races": {LABEL: {"collector_version": "v", "scored": scored, "scratched_nums": {},
                                         "quality": good_quality(self.new_def)}}}
        self.patches = [mock.patch.object(ncv, "OUTPUT_DIR", self.out), mock.patch.object(ncv, "SUMMARY_DIR", self.summ),
                        mock.patch.object(ncv, "load_index_tables", lambda p: tables())]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_script(self, pickup=None):
        (self.out / DATE / "pickup_scores.json").write_text(json.dumps(pickup or self.pickup, ensure_ascii=False))
        buf = io.StringIO()
        code = 0
        with redirect_stdout(buf), mock.patch.object(sys, "argv", ["x", DATE]):
            try:
                ncv.main()
            except SystemExit as e:
                code = e.code
        files = sorted((self.out / DATE / "new_candidates_variants").glob("*.json")) \
            if (self.out / DATE / "new_candidates_variants").exists() else []
        return code, buf.getvalue(), files

    def test_eligible_race_has_both_variants_and_excludes_new_candidate(self):
        code, msg, files = self.run_script()
        self.assertEqual(code, 0, msg)
        rec = json.loads(files[0].read_text())["races"][LABEL]
        self.assertTrue(rec["eligible"], rec["reasons"])
        self.assertIn("3", rec["new_candidates"])
        self.assertIn("3", rec["include"]["fire"])
        self.assertNotIn("3", rec["exclude"]["fire"])
        self.assertNotIn("3", [h["馬番"] for h in rec["exclude"]["scored"]])

    def test_other_scoring_version_stops(self):
        p = dict(self.pickup, scoring_version="v6")
        code, msg, files = self.run_script(p)
        self.assertEqual(code, 2)
        self.assertEqual(files, [])

    def test_ineligible_races_get_no_fire_or_bets(self):
        """品質の問題・候補の不一致・候補の版の違いがあれば対象外で、🔥・買い目を作らない"""
        cases = []
        bad_q = json.loads(json.dumps(self.pickup))
        bad_q["races"][LABEL]["quality"]["index_table_check"] = None
        cases.append(("品質", bad_q))
        mism = json.loads(json.dumps(self.pickup))
        mism["races"][LABEL]["scored"] = mism["races"][LABEL]["scored"][1:]
        cases.append(("合わない", mism))
        rule = json.loads(json.dumps(self.pickup))
        rule["races"][LABEL]["quality"]["candidate_rule"] = "古い版"
        cases.append(("候補の版", rule))
        err = json.loads(json.dumps(self.pickup))
        err["races"][LABEL] = {"error": "x"}
        cases.append(("採点の記録", err))
        for word, p in cases:
            with self.subTest(word):
                for f in (self.out / DATE / "new_candidates_variants").glob("*.json") \
                        if (self.out / DATE / "new_candidates_variants").exists() else []:
                    f.unlink()
                code, msg, files = self.run_script(p)
                self.assertEqual(code, 0, msg)
                rec = json.loads(files[0].read_text())["races"][LABEL]
                self.assertFalse(rec["eligible"])
                self.assertTrue(any(word in r for r in rec["reasons"]), rec["reasons"])
                self.assertNotIn("include", rec)
                self.assertNotIn("exclude", rec)

    def test_race_missing_from_pickup_is_counted(self):
        p = dict(self.pickup, races={})
        code, msg, files = self.run_script(p)
        rec = json.loads(files[0].read_text())["races"][LABEL]
        self.assertFalse(rec["eligible"])
        self.assertIn("pickup_scores.json に無い", rec["reasons"][0])

    def test_rerun_keeps_previous_record(self):
        """入力が同じなら作らない。入力が違えば別の実行として足し、前の記録は残す"""
        _, _, f1 = self.run_script()
        _, msg, f2 = self.run_script()
        self.assertEqual(len(f2), 1, msg)
        changed = json.loads(json.dumps(self.pickup))
        changed["races"][LABEL]["scored"][0]["score"] = 9
        _, _, f3 = self.run_script(changed)
        self.assertEqual(len(f3), 2)
        self.assertIn(f1[0], f3)

    def test_frozen_code_change_stops(self):
        with mock.patch.dict(ncv.FROZEN_CODE, {"paper_trade.build_menus": "0000000000000000"}):
            code, msg, files = self.run_script()
        self.assertEqual(code, 2)
        self.assertIn("関数の中身が変わった", msg)
        self.assertEqual(files, [])

    def test_leak_of_excluded_horse_into_bets_stops(self):
        """外す場合の買い目に新しく候補が入ったら止める（検査そのものが働くか）"""
        real = ncv.variant

        def leaky(scored, summary_race):
            v = real(scored, summary_race)
            v["bets"] = {"axis_num": 3, "axis_name": "", "menu_rule": "", "menus": {}}
            return v
        with mock.patch.object(ncv, "variant", leaky):
            code, msg, files = self.run_script()
        self.assertEqual(code, 3)
        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()
