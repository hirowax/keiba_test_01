"""
ペーパートレードの◯と紐をページの順位で選ぶ処理（paper_trade.himo_set_page_rank / maru_num_page_rank /
build_menus の版の切り替え）と、結果ページの着順が数字でない馬の読み取り（scrape_results.parse_result_html）の
回帰テスト（2026-11-02。docs/index_top3_definition_202611.md）。

サマリーは自作の小さな JSON（export_json.py の出力の形に合わせた）。結果ページは実ページ
（tests/fixtures/result_202605041106_torikeshi.html: 11/1 東京6R。13番が取消）。
除外・失格の実ページは手元に無い（中止は 11/1 京都4R・6R の取り直しで確認したがテスト用には保存していない）。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_pages import read_fixture  # noqa: E402
import paper_trade  # noqa: E402
from scrape_results import parse_result_html  # noqa: E402



def h(num, rank, val):
    return {"num": str(num), "name": f"馬{num}", "rank": rank, "val": val}


SCORED = [{"馬番": "1", "score": 9}]
SUMMARY = {
    "label": "東京7R",
    # 近走平均: 5番と6番が同率1位（どちらも3指数の候補でない）
    "average": [h(5, "1位", 90.0), h(6, "1位", 90.0), h(1, "3位", 85.0), h(7, "4位", 80.0), h(8, "5位", 79.0)],
    "distance": [h(1, "1位", 100.0), h(7, "2位", 95.0), h(9, "3位", 90.0)],
    "course": [h(1, "1位", 99.0), h(9, "2位", 92.0)],
    "triple": [{"num": "1", "name": "馬1"}],
}


class PageRankMenuTest(unittest.TestCase):
    def test_himo(self):
        """D = 同率1位の5番・6番、C = 5位以内に2指数で入る7番・9番。◎の1番は入らない"""
        axis, himo = paper_trade.himo_set_page_rank(SCORED, SUMMARY)
        self.assertEqual(axis, 1)
        self.assertEqual(himo, {5, 6, 7, 9})

    def test_maru_tie_takes_smaller_umaban(self):
        self.assertEqual(paper_trade.maru_num_page_rank(SCORED, SUMMARY), 5)

    def test_maru_is_second_scored_horse_if_any(self):
        self.assertEqual(paper_trade.maru_num_page_rank(SCORED + [{"馬番": "9"}], SUMMARY), 9)

    def test_rule_switch(self):
        self.assertEqual(paper_trade.menu_rule_of(SUMMARY), paper_trade.MENU_RULE_PAGE_RANK)
        old = {k: ([{kk: vv for kk, vv in x.items() if kk != "rank"} for x in v] if isinstance(v, list) else v)
               for k, v in SUMMARY.items()}
        self.assertEqual(paper_trade.menu_rule_of(old), paper_trade.MENU_RULE_OLD)
        _, _, menus = paper_trade.build_menus(SCORED, SUMMARY)
        self.assertEqual(menus["M3"]["o_num"], 5)
        self.assertEqual(sorted(menus["M4"]["himo"]), [5, 6, 7, 9])


class ResultNonFinisherTest(unittest.TestCase):
    def test_torikeshi_is_kept(self):
        r = parse_result_html(read_fixture("result_202605041106_torikeshi.html"), "東京6R")
        self.assertEqual(len(r["horses"]), 14)
        self.assertEqual([(o["num"], o["status"], o["name"]) for o in r["others"]],
                         [("13", "取消", "ソリッドベーシス")])
        self.assertEqual([x["num"] for x in r["horses"] if x["rank"] == 1], ["12"])

    def test_unknown_status_is_not_dropped(self):
        html = read_fixture("result_202605041106_torikeshi.html")
        html = html.replace(">取消<", ">再審<", 1)
        r = parse_result_html(html, "東京6R")
        self.assertEqual([(o["num"], o["status"], o["rank_text"]) for o in r["others"]], [("13", "不明", "再審")])


if __name__ == "__main__":
    unittest.main()
