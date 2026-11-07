"""
指数の表と出馬表の照合（index_table_check.py。2026-11-06 外部レビュー指摘）の回帰テスト。
実ページの表（tests/fixtures/rank_202605041007_average.html、東京7R・11頭）と、それを加工した表で試す。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixture_pages import read_fixture  # noqa: E402
from index_table_check import check_index_tables  # noqa: E402
from scraper import parse_rank_html, ranked_horses  # noqa: E402



def real_table():
    return parse_rank_html(read_fixture("rank_202605041007_average.html"))


def shutuba_of(df, scratched=None, drop=(), names_override=None):
    """表の馬から出馬表を作る（drop の馬番は出馬表から外す）"""
    hs = ranked_horses(df)[0]
    ids = {h["馬番"]: f"H{h['馬番']}" for h in hs if h["馬番"] not in drop}
    names = {h["馬番"]: h["馬名"] for h in hs if h["馬番"] not in drop}
    names.update(names_override or {})
    return {"horse_id_map": ids, "horse_name_full": names, "scratched_nums": scratched or {}}


def without(df, num):
    col = next(c for c in df.columns if "馬番" in str(c))
    return df[df[col].astype(str).str.strip() != num].reset_index(drop=True)


class IndexTableCheckTest(unittest.TestCase):
    def setUp(self):
        self.df = real_table()
        self.horses = ranked_horses(self.df)[0]
        self.nums = [h["馬番"] for h in self.horses]

    def three(self, df=None):
        d = self.df if df is None else df
        return {"average": d, "distance": d.copy(), "course": d.copy()}

    def test_complete_tables_pass(self):
        self.assertEqual(len(self.nums), 11)
        r = check_index_tables(self.three(), shutuba_of(self.df))
        self.assertTrue(r["ok"], r["problems"])

    def test_three_horse_table_fails(self):
        """3頭だけの表（type=rank のプリフライトは通る形）は、欠けとして問題になる"""
        top3 = self.df.head(4).reset_index(drop=True)   # 先頭の行は見出し（「5走前」）
        self.assertEqual(len(ranked_horses(top3)[0]), 3)
        r = check_index_tables({"average": top3, "distance": self.df, "course": self.df}, shutuba_of(self.df))
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["modes"]["average"]["missing"]), 8)
        self.assertEqual(r["modes"]["distance"]["missing"], [])

    def test_each_table_is_checked_separately(self):
        """ある表に無い馬が別の表にいても、通さない（3表をまとめて判定しない）"""
        n = self.nums[0]
        r = check_index_tables({"average": without(self.df, n), "distance": self.df, "course": self.df},
                               shutuba_of(self.df))
        self.assertFalse(r["ok"])
        self.assertEqual(r["modes"]["average"]["missing"], [n])

    def test_extra_horse_fails(self):
        n = self.nums[-1]
        r = check_index_tables(self.three(), shutuba_of(self.df, drop=(n,)))
        self.assertFalse(r["ok"])
        self.assertEqual(r["modes"]["course"]["extra"], [n])

    def test_duplicate_horse_fails(self):
        dup = pd.concat([self.df, self.df.iloc[[1]]], ignore_index=True)   # 1頭目（0行目は見出し）を2回
        r = check_index_tables(self.three(dup), shutuba_of(self.df))
        self.assertFalse(r["ok"])
        self.assertTrue(any("重複" in e for e in r["modes"]["average"]["errors"]))

    def test_name_mismatch_fails_but_spaces_are_allowed(self):
        n, name = self.horses[0]["馬番"], self.horses[0]["馬名"]
        ok = check_index_tables(self.three(), shutuba_of(self.df, names_override={n: name[:1] + "　" + name[1:]}))
        self.assertTrue(ok["ok"], ok["problems"])
        bad = check_index_tables(self.three(), shutuba_of(self.df, names_override={n: name + "ス"}))
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["modes"]["average"]["name_mismatch"][0]["馬番"], n)
        no_name = check_index_tables(self.three(), shutuba_of(self.df, names_override={n: ""}))
        self.assertFalse(no_name["ok"])

    def test_scratched_horse_may_be_missing_or_present(self):
        n = self.nums[2]
        sh = shutuba_of(self.df, scratched={n: "取消"})
        self.assertTrue(check_index_tables(self.three(without(self.df, n)), sh)["ok"])
        r = check_index_tables(self.three(), sh)
        self.assertTrue(r["ok"])
        self.assertEqual(r["modes"]["average"]["scratched_in_table"], [n])

    def test_missing_table_or_empty_shutuba_fails(self):
        self.assertFalse(check_index_tables({"average": self.df, "distance": self.df}, shutuba_of(self.df))["ok"])
        self.assertFalse(check_index_tables(None, shutuba_of(self.df))["ok"])
        self.assertFalse(check_index_tables(self.three(), {})["ok"])


if __name__ == "__main__":
    unittest.main()
