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
from data_quality import build_quality, race_quality_issues  # noqa: E402
from scraper import INDEX_READER_VERSION, parse_speed_shutuba_html  # noqa: E402

SOURCES = {
    "index_reader_version": INDEX_READER_VERSION,
    "races": {"東京7R": {"modes": {"近走平均": "type=rank", "当該距離": "type=rank", "当該コース": "type=rank"},
                         "read_errors": {}}},
}


class QualityTest(unittest.TestCase):
    def test_all_checks_pass(self):
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R")}
        self.assertEqual(race_quality_issues(rdata), [])

    def test_collector_version_alone_is_not_enough(self):
        """10/31 のように馬番の修正後でも、指数の読み方が未記録なら使わない"""
        issues = race_quality_issues({"collector_version": "v"})
        self.assertEqual(issues, ["指数の読み方が未記録（2026-10-30 夜の修正前）"])

    def test_race_missing_from_index_sources(self):
        q = build_quality("v", SOURCES, "京都1R")
        self.assertIsNone(q["index_reader"])
        self.assertTrue(race_quality_issues({"quality": q}))

    def test_pre_fix_race(self):
        self.assertIn("馬番の取り方が修正前", race_quality_issues({}))

    def test_invalidated(self):
        rdata = {"collector_version": "v", "quality": build_quality("v", SOURCES, "東京7R")}
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
