"""
paper_trade.py の精算・集計の回帰テスト（2026-10-30 レビュー指摘）。

- 無効レース（invalidated.json）を精算・集計から外し、件数と理由を表示する
- 払戻データが無いレースを「払戻0円・不的中」にしない
- 買い目の馬番が結果データに無い（取消・除外・競走中止等）メニューを外れにしない

入力は自作の小さな JSON。payouts_jra.json・race_results.json の形は実ファイル
（output/20261018/）に合わせたが、取消・除外が JRA 払戻ページにどう載るかは未確認。

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

DATE = "20261031"


def menus(axis="1", o="2"):
    return {
        "M1": {"type": "fukusho", "bets": {axis: 100}, "cost": 100},
        "M2": {"type": "tansho", "bets": {axis: 100}, "cost": 100},
        "M3": {"type": "wide", "bets": {f"{axis}-{o}": 100}, "cost": 100},
        "M4": {"type": "trio", "skip_reason": "k<2", "bets": {}, "cost": 0},
    }


def runners(*nums):
    return [{"rank": i + 1, "num": n} for i, n in enumerate(nums)]


class SettleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self._orig = paper_trade.OUTPUT_DIR
        paper_trade.OUTPUT_DIR = self.out
        (self.out / DATE).mkdir()
        self.write("paper_bets.json", {"date": DATE, "settled": False, "races": {
            "東京1R": {"axis_num": 1, "menus": menus()},          # 普通に精算できる
            "東京2R": {"axis_num": 1, "menus": menus()},          # 払戻データが無い
            "東京3R": {"axis_num": 1, "menus": menus()},          # ○の2番が結果に無い（取消等）
            "京都4R": {"axis_num": 1, "menus": menus()},          # 無効
        }})
        pay = {"tansho": {"1": 300}, "fukusho": {"1": 130, "2": 150, "3": 200}, "wide": {"1-2": 400}}
        self.write("payouts_jra.json", {"東京1R": pay, "東京3R": pay, "京都4R": pay})
        self.write("race_results.json", {"東京1R": runners("1", "2", "3"), "東京2R": runners("1", "2", "3"),
                                         "東京3R": runners("1", "3", "4"), "京都4R": runners("1", "2", "3")})
        self.write("invalidated.json", {"races": {"京都4R": {"reason": "指数1000で採点"}}})

    def tearDown(self):
        paper_trade.OUTPUT_DIR = self._orig
        self.tmp.cleanup()

    def write(self, name, data):
        (self.out / DATE / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def bets(self):
        return json.loads((self.out / DATE / "paper_bets.json").read_text(encoding="utf-8"))

    def settle(self):
        with redirect_stdout(io.StringIO()):
            paper_trade.settle_for_date(DATE)
        return self.bets()

    def test_normal_race(self):
        m = self.settle()["races"]["東京1R"]["menus"]
        self.assertEqual((m["M1"]["status"], m["M1"]["return"], m["M1"]["hit"]), ("settled", 130, True))
        self.assertEqual(m["M2"]["return"], 300)
        self.assertEqual(m["M3"]["return"], 400)
        self.assertEqual(m["M4"]["status"], "no_bet")

    def test_missing_payout_is_not_a_loss(self):
        b = self.settle()
        m = b["races"]["東京2R"]["menus"]["M1"]
        self.assertEqual(m["status"], "pending_no_payout")
        self.assertNotIn("return", m)
        self.assertNotIn("hit", m)
        self.assertFalse(b["settled"])

    def test_runner_missing_from_results_is_not_a_loss(self):
        m = self.settle()["races"]["東京3R"]["menus"]
        self.assertEqual(m["M3"]["status"], "pending_not_in_results")  # 2番が結果に無い
        self.assertNotIn("return", m["M3"])
        self.assertEqual(m["M1"]["status"], "settled")                # 1番は出走
        self.assertEqual(m["M1"]["return"], 130)

    def test_void_race_kept_but_not_settled(self):
        r = self.settle()["races"]["京都4R"]
        self.assertEqual(r["void"], "指数1000で採点")
        self.assertEqual(r["menus"]["M2"]["status"], "void")
        self.assertNotIn("return", r["menus"]["M2"])
        self.assertEqual(r["menus"]["M2"]["bets"], {"1": 100})          # 元の買い目は残す

    def test_report_excludes_void_and_pending(self):
        self.settle()
        orig = paper_trade.PAPER_TRADE_START
        paper_trade.PAPER_TRADE_START = "20260101"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                paper_trade.cmd_report(None)
        finally:
            paper_trade.PAPER_TRADE_START = orig
        text = buf.getvalue()
        self.assertIn("無効として集計から外したレース: 1", text)
        self.assertIn("京都4R: 指数1000で採点", text)
        self.assertIn("払戻データ無し 3", text)            # 東京2R の M1〜M3
        self.assertIn("結果データに馬番無し（返還の可能性） 1", text)
        # M2 単勝: 精算済みは 東京1R と 東京3R の2レースだけ（無効の京都4R・払戻無しの東京2Rは入らない）
        self.assertIn("対象レース: 2  的中: 2", text.split("### M2")[1])
        self.assertIn("総投資: 200円  総払戻: 600円", text.split("### M2")[1])

    def test_old_format_settlement_not_counted(self):
        """status の無い旧形式の精算（払戻欠けの確認前）は集計に入れない"""
        b = self.bets()
        b["settled"] = True
        for r in b["races"].values():
            for m in r["menus"].values():
                m["return"], m["hit"] = 0, False
        self.write("paper_bets.json", b)
        self.write("invalidated.json", {"races": {}})
        orig = paper_trade.PAPER_TRADE_START
        paper_trade.PAPER_TRADE_START = "20260101"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                paper_trade.cmd_report(None)
        finally:
            paper_trade.PAPER_TRADE_START = orig
        self.assertIn("旧形式の精算", buf.getvalue())
        self.assertIn("精算済みデータがありません", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
