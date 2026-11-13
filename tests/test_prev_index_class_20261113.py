"""
馬ページの指数の列を class（TimeIndexHeadCell01）で決める変更（2026-11-13）の回帰テスト。

経緯: 11/13 の 11/14 の採点で、成績の表の指数の見出しが「タイム指数」（全角）として読め、見出しの文字「ﾀｲﾑ指数」（半角）の
完全一致で探していたため、70頭すべてが取得失敗になった（画面の見た目は変わっていない）。

実ページ（tests/fixtures/。git に入れない）: 2026-11-13 18:02 にログインした状態で取った馬ページ2つ。
- horse_2021110023_20261113.html（エクストラバック）: 対象日 11/14 より前の最新の行は 2026/10/25 京都8R（202608040908）
  5着・9番人気・芝1200・指数89。
- horse_2022101469_20261113.html（ヴェナートル）: 2026/07/26 札幌10R（202601010210）8着・9番人気・芝1500・指数77。
  非表示のタイム指数M・スタート指数・追走指数・上がり指数は 68・83・69・97（読まない）。
照合: レースID・着順・人気は output/20261025・20260726 の race_results_all.json と一致。指数は 11/14 の指数の表
（output/20261114.xlsx の京都12R 近走平均の「前走」の列）の値と一致（89・77）。

実行: python3 -m unittest discover -s tests -v
"""
import json
import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_pages import read_fixture  # noqa: E402
import prev_inputs as pi  # noqa: E402

PAGES = {"2021110023": ("エクストラバック", {"date": "20261025", "race_id": "202608040908", "rank": 5, "pop": 9,
                                             "dist": 1200, "idx": 89}),
         "2022101469": ("ヴェナートル", {"date": "20260726", "race_id": "202601010210", "rank": 8, "pop": 9,
                                          "dist": 1500, "idx": 77})}
CLS = pi.IDX_CELL_CLASS


def html_of(hid):
    return read_fixture(f"horse_{hid}_20261113.html")


def parse(html, hid="2022101469"):
    return pi.parse_results_table(html, "20261114", hid, PAGES[hid][0])


def edit(hid, fn):
    soup = BeautifulSoup(html_of(hid), "html.parser")
    fn(soup.select_one("table.db_h_race_results"))
    return str(soup)


def add_class(tag, c):
    tag["class"] = (tag.get("class") or []) + [c]


def drop_class(tag, c):
    tag["class"] = [x for x in (tag.get("class") or []) if x != c]


class RealPageTest(unittest.TestCase):
    def test_reads_visible_index_by_class(self):
        for hid, (name, want) in PAGES.items():
            e = parse(html_of(hid), hid)
            self.assertEqual(e["status"], pi.OK, (hid, e.get("reason")))
            self.assertEqual({k: e["values"][k] for k in want}, want, hid)

    def test_header_text_is_fullwidth_but_not_used(self):
        """今日のページの見出しは「タイム指数」（全角）。列は class で決めるので、文字が変わっても同じ列を読む"""
        e = parse(html_of("2022101469"))
        self.assertIn("タイム指数", e["headers"][20])
        self.assertNotIn("ﾀｲﾑ指数", e["headers"])

    def test_hidden_master_columns_are_not_read(self):
        """非表示のタイム指数M（68）・スタート（83）・追走（69）・上がり（97）は読まない"""
        e = parse(html_of("2022101469"))
        self.assertEqual(e["values"]["idx"], 77)
        soup = BeautifulSoup(html_of("2022101469"), "html.parser")
        row = [tr for tr in soup.select("table.db_h_race_results tr") if tr.find("td")][0]
        hidden = [td.get_text(strip=True) for td in row.find_all("td") if "TimeIndexMasterCell01" in (td.get("class") or [])]
        self.assertEqual(hidden, ["68", "83", "69", "97"])


class StructureFailTest(unittest.TestCase):
    """見出し・セルの欠け、重複、位置の不一致は取得失敗（隠れた別の指数へ代わりに進まない）"""

    def assertFailed(self, html, words):
        e = parse(html)
        self.assertEqual(e["status"], pi.FAILED, e.get("reason"))
        self.assertIn(words, e["reason"])

    def test_header_class_missing_or_duplicated(self):
        self.assertFailed(edit("2022101469", lambda t: drop_class(t.select_one(f"th.{CLS}"), CLS)), "が0個")
        def dup(t):
            add_class(t.find_all("th")[19], CLS)   # 着差の見出し
        self.assertFailed(edit("2022101469", dup), "が2個")

    def test_master_header_is_not_used_as_fallback(self):
        """指数の見出しの class が無ければ、タイム指数M の見出しがあっても取得失敗"""
        self.assertFailed(edit("2022101469", lambda t: drop_class(t.select_one(f"th.{CLS}"), CLS)), "指数の見出し")

    def test_row_cell_missing_moved_or_duplicated(self):
        def first_row(t):
            return [tr for tr in t.find_all("tr") if tr.find("td")][0]

        def missing(t):
            drop_class(first_row(t).select_one(f"td.{CLS}"), CLS)

        def moved(t):
            r = first_row(t)
            drop_class(r.select_one(f"td.{CLS}"), CLS)
            add_class(r.find_all("td")[21], CLS)   # 非表示のタイム指数M のセル

        def dup(t):
            add_class(first_row(t).find_all("td")[19], CLS)
        for fn in (missing, moved, dup):
            self.assertFailed(edit("2022101469", fn), "見出しの位置に1つだけ、ではない")


class FailedRecordNotReusedTest(unittest.TestCase):
    """11/13 17:43〜 の取得失敗の記録（70頭）は、成功した値として再利用しない"""

    def test_failed_entries(self):
        files = sorted(Path(__file__).resolve().parent.parent.glob("output/20261114/prev_inputs/2026*.json"))
        if not files:
            raise unittest.SkipTest("11/14 の前走データの記録が手元に無い")
        entries = [e for r in json.loads(files[0].read_text(encoding="utf-8"))["races"].values()
                   for e in r["horses"].values()]
        self.assertEqual(len(entries), 70)
        self.assertTrue(all(e["status"] == pi.FAILED for e in entries))
        self.assertFalse(any(pi.is_reusable(e, "20261114") for e in entries))
        # 取得の版が今のコードと違うことも、再利用されない理由になる
        self.assertTrue(all(e["fetch_version"] != pi.PREV_FETCH_VERSION for e in entries))


if __name__ == "__main__":
    unittest.main()
