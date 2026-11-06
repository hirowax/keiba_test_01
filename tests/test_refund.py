"""
取消・除外の返還（残りのタスク5）の回帰テスト。
- scrape_jra_payouts.parse_payouts の「返還」欄の読み取り（実ページ: 11/1 東京6R 13番取消、11/1 京都4R 8番中止）
- paper_trade.settle_for_date の返還（賭け金を返す）・中止（不的中）・記録なし（要確認）の扱い（自作の JSON）
除外の実ページ（2026-01-18 京都12R の3頭・「返還枠番」、2026-10-18 京都9R の1頭）は 2026-11-06 に足した。失格の返還の実ページは手元に無い。

実行: python3 -m unittest discover -s tests -v
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import paper_trade  # noqa: E402
from scrape_jra_payouts import parse_payouts  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
DATE = "20261107"


class ParseRefundTest(unittest.TestCase):
    def test_torikeshi(self):
        p = parse_payouts((FIX / "jra_payout_202605041106_torikeshi.html").read_text(encoding="utf-8"))
        self.assertEqual(p["refund"], {"umaban": [13], "waku": [7], "wakuban": [], "text": "返還馬番 13番 返還同枠 7枠"})
        self.assertEqual(p["tansho"], {"12": 260})

    def test_chushi_has_no_refund(self):
        p = parse_payouts((FIX / "jra_payout_202608041104_chushi.html").read_text(encoding="utf-8"))
        self.assertEqual(p["refund"], {"umaban": [], "waku": [], "wakuban": [], "text": ""})

    def test_jogai_three_with_wakuban(self):
        """「返還枠番」の枠の番号を馬番に足さない（2026-11-06 まで 5・6・7・5・6 と読んでいた）"""
        p = parse_payouts((FIX / "jra_payout_202608010712_jogai3_wakuban.html").read_text(encoding="utf-8"))
        self.assertEqual(p["refund"], {"umaban": [5, 6, 7], "waku": [], "wakuban": [5, 6],
                                       "text": "返還馬番 5番, 6番, 7番 返還枠番 5枠, 6枠"})
        self.assertEqual(p["tansho"], {"3": 1320})

    def test_jogai_one(self):
        p = parse_payouts((FIX / "jra_payout_202608040709_jogai1.html").read_text(encoding="utf-8"))
        self.assertEqual(p["refund"], {"umaban": [7], "waku": [], "wakuban": [], "text": "返還馬番 7番"})


class SettleRefundTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self._orig = paper_trade.OUTPUT_DIR
        paper_trade.OUTPUT_DIR = self.out
        (self.out / DATE).mkdir()
        menus = lambda: {
            "M1": {"type": "fukusho", "bets": {"1": 100}, "cost": 100},
            "M3": {"type": "wide", "bets": {"1-5": 100}, "cost": 100},
            "M4": {"type": "trio", "bets": {"1-2-5": 100, "1-2-3": 100}, "cost": 200},
        }
        self.write("paper_bets.json", {"date": DATE, "races": {"東京1R": {"menus": menus()}, "東京2R": {"menus": menus()},
                                                                "東京3R": {"menus": menus()}}})
        pay = {"tansho": {"1": 300}, "fukusho": {"1": 130, "2": 150, "3": 200}, "wide": {"1-2": 400},
               "trio": {"1-2-3": 1500}}
        self.write("payouts_jra.json", {
            "東京1R": {**pay, "refund": {"umaban": [5], "waku": [3], "text": "返還馬番 5番 返還同枠 3枠"}},
            "東京2R": {**pay, "refund": {"umaban": [], "waku": [], "text": ""}},
            "東京3R": {**pay, "refund": {"umaban": [], "waku": [], "text": ""}},
        })
        runners = [{"rank": i + 1, "num": str(n)} for i, n in enumerate((1, 2, 3, 4))]
        self.write("race_results.json", {"東京1R": runners, "東京2R": runners, "東京3R": runners})
        self.write("race_nonfinishers.json", {"東京2R": [{"num": "5", "status": "中止"}]})

    def tearDown(self):
        paper_trade.OUTPUT_DIR = self._orig
        self.tmp.cleanup()

    def write(self, name, data):
        (self.out / DATE / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def settle(self):
        with redirect_stdout(io.StringIO()):
            paper_trade.settle_for_date(DATE)
        return json.loads((self.out / DATE / "paper_bets.json").read_text(encoding="utf-8"))

    def test_refunded_horse_returns_stake(self):
        m = self.settle()["races"]["東京1R"]["menus"]
        self.assertEqual((m["M3"]["status"], m["M3"]["return"], m["M3"]["hit"], m["M3"]["refunded"]),
                         ("settled", 100, False, ["1-5"]))
        # 3連複: 1-2-5 は返還で100円、1-2-3 は的中で1500円
        self.assertEqual((m["M4"]["return"], m["M4"]["hit"], m["M4"]["refunded"]), (1600, True, ["1-2-5"]))
        self.assertEqual(m["M4"]["bets"], {"1-2-5": 100, "1-2-3": 100})  # 元の買い目は残す

    def test_chushi_is_a_loss(self):
        m = self.settle()["races"]["東京2R"]["menus"]
        self.assertEqual((m["M3"]["status"], m["M3"]["return"], m["M3"]["hit"]), ("settled", 0, False))
        self.assertNotIn("refunded", m["M3"])

    def test_unknown_stays_pending(self):
        b = self.settle()
        self.assertEqual(b["races"]["東京3R"]["menus"]["M3"]["status"], "pending_not_in_results")
        self.assertEqual(b["races"]["東京3R"]["menus"]["M1"]["status"], "settled")
        self.assertFalse(b["settled"])


if __name__ == "__main__":
    unittest.main()
