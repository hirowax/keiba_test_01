"""
data_quality.py（レース単位の品質判定）と、指数の取得異常の記録の回帰テスト。
2026-10-30 レビュー指摘: 品質を収集版の有無だけで判定しない（馬番・指数・無効化を別々に確認する）。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_pages import read_fixture  # noqa: E402
from data_quality import DATA_TOP_READER_OLD, HORSE_STYLE_OLD, race_quality_issues  # noqa: E402
from data_quality import build_quality as _build_quality  # noqa: E402
import build_horse_style as bhs  # noqa: E402
from prev_inputs import PREV_FETCH_VERSION  # noqa: E402
from scraper import CANDIDATE_RULE_VERSION, INDEX_READER_VERSION, parse_speed_shutuba_html  # noqa: E402

SOURCES = {
    "index_reader_version": INDEX_READER_VERSION,
    "candidate_rule_version": CANDIDATE_RULE_VERSION,
    "races": {"東京7R": {"modes": {"近走平均": "type=rank", "当該距離": "type=rank", "当該コース": "type=rank"},
                         "read_errors": {}}},
}

PREV_OK = {"prev_fetch": PREV_FETCH_VERSION, "prev_inputs_run": "run1",
           "prev_status": {"candidates": {"3": "取れた", "5": "前走なし",
                                           "9": "指数が空（着順が数字でない）"},
                           "others": {"取れた": 9}, "top1": "保留"}}
OLD_PREV = "前走データの取り方が修正前（2026-11-05 より前）"
TABLE_OK = {"version": "test", "ok": True, "modes": {}, "problems": []}
PAGES_OK = {"shutuba": {"path": "p", "sha256": "h"}, "data_top": {"path": "p", "sha256": "h"}}
DTR = "test"   # 出走馬分析などの読み方の版（2026-11-07〜。直した読み取りはまだ無いので、テスト用の値）
HS_OK = {"run": "r1", "sha256": "h", "status": bhs.STATUS_OK, "version": bhs.STYLE_BUILD_VERSION, "failure_reason": None}


def build_quality(*a, **k):
    """脚質の参照（2026-11-12〜）は、指定が無ければ作成成功を入れる（脚質以外の判定のテストのため）"""
    k.setdefault("horse_style", HS_OK)
    return _build_quality(*a, **k)


class QualityTest(unittest.TestCase):
    def test_all_checks_pass(self):
        """候補が「前走なし」「指数が空（着順が数字でない）」でも、1位の判定が保留でも、品質の問題にはしない（決めたルールどおりの結果）"""
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=TABLE_OK, data_top_reader=DTR, pages=PAGES_OK)}
        self.assertEqual(race_quality_issues(rdata), [])

    def test_collector_version_alone_is_not_enough(self):
        """10/31 のように馬番の修正後でも、指数の読み方が未記録なら使わない"""
        issues = race_quality_issues({"collector_version": "v"})
        self.assertEqual(issues, ["指数の読み方が未記録（2026-10-30 夜の修正前）",
                                  "候補の選び方が修正前（「*」付きを外す・同じ値は並び順。2026-11-02 より前）",
                                  OLD_PREV, DATA_TOP_READER_OLD, HORSE_STYLE_OLD])

    def test_old_candidate_rule(self):
        """11/1 のように指数の読み方が記録されていても、候補の選び方が修正前なら使わない"""
        old = {k: v for k, v in SOURCES.items() if k != "candidate_rule_version"}
        rdata = {"collector_version": "v", "quality": build_quality("v", old, "東京7R", prev=PREV_OK, table_check=TABLE_OK, data_top_reader=DTR, pages=PAGES_OK)}
        self.assertEqual(race_quality_issues(rdata),
                         ["候補の選び方が修正前（「*」付きを外す・同じ値は並び順。2026-11-02 より前）"])

    def test_old_prev_fetch(self):
        """前走データの版が無い（2026-11-05 より前の取り方）レースは使わない"""
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", data_top_reader=DTR, pages=PAGES_OK)}
        self.assertEqual(race_quality_issues(rdata), [OLD_PREV])

    def test_candidate_prev_failed(self):
        """版があっても、候補の前走データが取得失敗・入力が無いなら使わない（版だけで品質を保証しない）"""
        for st in ("取得失敗", "入力が無い"):
            prev = dict(PREV_OK, prev_status=dict(PREV_OK["prev_status"], candidates={"3": "取れた", "5": st}))
            rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=prev, table_check=TABLE_OK, data_top_reader=DTR, pages=PAGES_OK)}
            self.assertEqual(race_quality_issues(rdata), [f"候補の前走データが取れていない: {{'5': '{st}'}}"])

    def test_other_horse_prev_failed_is_not_an_issue(self):
        """比較相手の取得失敗は、1位の判定の保留として扱い、品質の問題にはしない"""
        prev = dict(PREV_OK, prev_status=dict(PREV_OK["prev_status"], others={"取れた": 5, "取得失敗": 2}))
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=prev, table_check=TABLE_OK, data_top_reader=DTR, pages=PAGES_OK)}
        self.assertEqual(race_quality_issues(rdata), [])

    def test_index_table_check_missing_or_bad(self):
        """前走データが修正後のデータで、指数の表と出馬表の照合の記録が無い・問題があれば使わない（2026-11-06〜）"""
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=PREV_OK, data_top_reader=DTR, pages=PAGES_OK)}
        self.assertEqual(race_quality_issues(rdata), ["指数の表と出馬表の照合の記録が無い"])
        bad = {"version": "test", "ok": False, "modes": {}, "problems": ["average: {'missing': ['4']}"]}
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=bad, data_top_reader=DTR, pages=PAGES_OK)}
        self.assertEqual(race_quality_issues(rdata), ["指数の表が出馬表とそろわない: [\"average: {'missing': ['4']}\"]"])

    def test_old_data_top_reader(self):
        """出走馬分析などの読み方の版が無い採点は、ほかの条件がすべて通っても使わない（2026-11-07。🔥・買い目の対象外）"""
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=TABLE_OK)}
        self.assertEqual(race_quality_issues(rdata), [DATA_TOP_READER_OLD])
        self.assertIsNone(rdata["quality"]["data_top_reader"])

    def test_old_reader_and_reader_issues_both_reported(self):
        """修正前の読み方と読み取りの異常は、別の理由として両方出す（2026-11-07 外部レビュー。調べやすさのため）"""
        q = build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=TABLE_OK, data_top_issues=["x"])
        issues = race_quality_issues({"collector_version": "v", "quality": q})
        self.assertEqual(issues[0], DATA_TOP_READER_OLD)
        self.assertTrue(issues[1].startswith("3項目"))
        self.assertEqual(len(issues), 2)

    def test_page_save_failure_or_missing_record(self):
        """読み方が修正後の採点で、ページの保存に失敗した・記録が無いなら使わない（2026-11-07 外部レビュー）"""
        bad = {"shutuba": {"path": "p", "sha256": "h"}, "data_top": {"error": "OSError: disk full"}}
        q = build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=TABLE_OK, data_top_reader=DTR, pages=bad)
        self.assertEqual(race_quality_issues({"collector_version": "v", "quality": q}),
                         ["採点に使ったページを保存できなかった: {'data_top': 'OSError: disk full'}"])
        q = build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=TABLE_OK, data_top_reader=DTR)
        self.assertEqual(race_quality_issues({"collector_version": "v", "quality": q}), ["採点に使ったページの保存の記録が無い"])

    def test_race_missing_from_index_sources(self):
        q = build_quality("v", SOURCES, "京都1R")
        self.assertIsNone(q["index_reader"])
        self.assertTrue(race_quality_issues({"quality": q}))

    def test_pre_fix_race(self):
        self.assertIn("馬番の取り方が修正前", race_quality_issues({}))

    def test_invalidated(self):
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=TABLE_OK, data_top_reader=DTR, pages=PAGES_OK)}
        self.assertEqual(race_quality_issues(rdata, "指数1000で採点"), ["無効: 指数1000で採点"])

    def test_error_race(self):
        rdata = {"error": "shutuba map invalid", "quality": build_quality("v", SOURCES, "東京7R")}
        self.assertIn("採点エラー: shutuba map invalid", race_quality_issues(rdata))


class IndexErrorRecordTest(unittest.TestCase):
    def test_fallback_read_error_is_recorded(self):
        html = read_fixture("submenu_202605041007.html")
        soup = BeautifulSoup(html, "html.parser")
        c = soup.find(class_="sk__max_course_index")
        for hidden in c.find_all(class_="Sort_Function_Data_Hidden"):
            hidden.decompose()
        c.string = "1072"
        errors = {}
        res = parse_speed_shutuba_html(str(soup), "202605041007", errors)
        self.assertNotIn("course", res)
        self.assertEqual(list(errors), ["course"])
        self.assertIn("1072", errors["course"][0])

class ResultsSaveMarkerEntryTest(unittest.TestCase):
    """研究用の入口の確認（2026-11-13〜）"""

    def test_skip_and_stop(self):
        import json
        import tempfile
        import data_quality as D
        t = Path(tempfile.mkdtemp())
        (t / "20261107").mkdir()
        (t / "20261108").mkdir()
        self.assertFalse(D.skip_results_day(t / "20261107"))
        D.stop_if_any_results_save_incomplete(t)   # 印が無ければ止めない
        (t / "20261108" / D.RESULTS_SAVE_MARKER).write_text(json.dumps({"state": "保存中", "errors": ["x"]}), encoding="utf-8")
        self.assertTrue(D.skip_results_day(t / "20261108"))
        with self.assertRaises(SystemExit) as cm:
            D.stop_if_any_results_save_incomplete(t)
        self.assertIn("20261108", str(cm.exception))
        self.assertNotIn("20261107", str(cm.exception))


if __name__ == "__main__":
    unittest.main()


class HorseStyleQualityTest(unittest.TestCase):
    """脚質の参照の判定（docs/horse_style_design_202611.md 4.3）"""

    def issues(self, **k):
        q = _build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=TABLE_OK, data_top_reader=DTR,
                           pages=PAGES_OK, **k)
        return race_quality_issues({"collector_version": "v", "quality": q})

    def test_ok(self):
        self.assertEqual(self.issues(horse_style=HS_OK), [])

    def test_old_method_when_key_missing(self):
        self.assertEqual(self.issues(), [HORSE_STYLE_OLD])

    def test_no_record(self):
        self.assertEqual(self.issues(horse_style=None), ["脚質の記録が無い"])

    def test_bad_shape(self):
        self.assertEqual(self.issues(horse_style={**HS_OK, "sha256": None}), ["脚質の記録の形が違う"])
        self.assertEqual(self.issues(horse_style="x"), ["脚質の記録の形が違う"])

    def test_previous_version_is_known(self):
        """前の版（20261112）で作った記録を参照するレースも、版の理由では問題にしない（その日は軸の再検証では試運転）"""
        self.assertEqual(self.issues(horse_style={**HS_OK, "version": bhs.STYLE_BUILD_VERSION_20261112}), [])

    def test_version_missing_or_unknown(self):
        for v in (None, "知らない版"):
            out = self.issues(horse_style={**HS_OK, "version": v})
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0].startswith("脚質の作成コードの版が無い・知らない版"))

    def test_failed_with_and_without_reason(self):
        self.assertEqual(self.issues(horse_style={**HS_OK, "status": bhs.STATUS_FAILED, "failure_reason": "使えたレースが0件"}),
                         ["脚質を作れない: 使えたレースが0件"])
        self.assertEqual(self.issues(horse_style={**HS_OK, "status": bhs.STATUS_FAILED}),
                         ["脚質を作れない: 理由の記録が無い"])

    def test_unknown_status(self):
        self.assertEqual(self.issues(horse_style={**HS_OK, "status": "途中"}), ["脚質の記録の状態が分からない: '途中'"])

    def test_error_race_is_not_judged_twice(self):
        rd = {"error": "x", "quality": _build_quality("v", SOURCES, "東京7R")}
        self.assertFalse(any("脚質" in x for x in race_quality_issues(rd)))
