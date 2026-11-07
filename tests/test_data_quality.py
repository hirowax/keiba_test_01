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
from data_quality import DATA_TOP_READER_OLD, build_quality, race_quality_issues  # noqa: E402
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
DTR = "test"   # 出走馬分析などの読み方の版（2026-11-07〜。直した読み取りはまだ無いので、テスト用の値）


class QualityTest(unittest.TestCase):
    def test_all_checks_pass(self):
        """候補が「前走なし」「指数が空（着順が数字でない）」でも、1位の判定が保留でも、品質の問題にはしない（決めたルールどおりの結果）"""
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=TABLE_OK, data_top_reader=DTR)}
        self.assertEqual(race_quality_issues(rdata), [])

    def test_collector_version_alone_is_not_enough(self):
        """10/31 のように馬番の修正後でも、指数の読み方が未記録なら使わない"""
        issues = race_quality_issues({"collector_version": "v"})
        self.assertEqual(issues, ["指数の読み方が未記録（2026-10-30 夜の修正前）",
                                  "候補の選び方が修正前（「*」付きを外す・同じ値は並び順。2026-11-02 より前）",
                                  OLD_PREV, DATA_TOP_READER_OLD])

    def test_old_candidate_rule(self):
        """11/1 のように指数の読み方が記録されていても、候補の選び方が修正前なら使わない"""
        old = {k: v for k, v in SOURCES.items() if k != "candidate_rule_version"}
        rdata = {"collector_version": "v", "quality": build_quality("v", old, "東京7R", prev=PREV_OK, table_check=TABLE_OK, data_top_reader=DTR)}
        self.assertEqual(race_quality_issues(rdata),
                         ["候補の選び方が修正前（「*」付きを外す・同じ値は並び順。2026-11-02 より前）"])

    def test_old_prev_fetch(self):
        """前走データの版が無い（2026-11-05 より前の取り方）レースは使わない"""
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", data_top_reader=DTR)}
        self.assertEqual(race_quality_issues(rdata), [OLD_PREV])

    def test_candidate_prev_failed(self):
        """版があっても、候補の前走データが取得失敗・入力が無いなら使わない（版だけで品質を保証しない）"""
        for st in ("取得失敗", "入力が無い"):
            prev = dict(PREV_OK, prev_status=dict(PREV_OK["prev_status"], candidates={"3": "取れた", "5": st}))
            rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=prev, table_check=TABLE_OK, data_top_reader=DTR)}
            self.assertEqual(race_quality_issues(rdata), [f"候補の前走データが取れていない: {{'5': '{st}'}}"])

    def test_other_horse_prev_failed_is_not_an_issue(self):
        """比較相手の取得失敗は、1位の判定の保留として扱い、品質の問題にはしない"""
        prev = dict(PREV_OK, prev_status=dict(PREV_OK["prev_status"], others={"取れた": 5, "取得失敗": 2}))
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=prev, table_check=TABLE_OK, data_top_reader=DTR)}
        self.assertEqual(race_quality_issues(rdata), [])

    def test_index_table_check_missing_or_bad(self):
        """前走データが修正後のデータで、指数の表と出馬表の照合の記録が無い・問題があれば使わない（2026-11-06〜）"""
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=PREV_OK, data_top_reader=DTR)}
        self.assertEqual(race_quality_issues(rdata), ["指数の表と出馬表の照合の記録が無い"])
        bad = {"version": "test", "ok": False, "modes": {}, "problems": ["average: {'missing': ['4']}"]}
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=bad, data_top_reader=DTR)}
        self.assertEqual(race_quality_issues(rdata), ["指数の表が出馬表とそろわない: [\"average: {'missing': ['4']}\"]"])

    def test_old_data_top_reader(self):
        """出走馬分析などの読み方の版が無い採点は、ほかの条件がすべて通っても使わない（2026-11-07。🔥・買い目の対象外）"""
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=TABLE_OK)}
        self.assertEqual(race_quality_issues(rdata), [DATA_TOP_READER_OLD])
        self.assertIsNone(rdata["quality"]["data_top_reader"])

    def test_race_missing_from_index_sources(self):
        q = build_quality("v", SOURCES, "京都1R")
        self.assertIsNone(q["index_reader"])
        self.assertTrue(race_quality_issues({"quality": q}))

    def test_pre_fix_race(self):
        self.assertIn("馬番の取り方が修正前", race_quality_issues({}))

    def test_invalidated(self):
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R", prev=PREV_OK, table_check=TABLE_OK, data_top_reader=DTR)}
        self.assertEqual(race_quality_issues(rdata, "指数1000で採点"), ["無効: 指数1000で採点"])

    def test_error_race(self):
        rdata = {"error": "shutuba map invalid", "quality": build_quality("v", SOURCES, "東京7R")}
        self.assertIn("採点エラー: shutuba map invalid", race_quality_issues(rdata))


class IndexErrorRecordTest(unittest.TestCase):
    def test_fallback_read_error_is_recorded(self):
        html = (Path(__file__).resolve().parent / "fixtures" / "submenu_202605041007.html").read_text(encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
