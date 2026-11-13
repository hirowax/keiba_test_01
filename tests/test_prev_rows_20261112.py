"""
前走が障害戦の馬・前走の着順「取」・初出走の馬の扱い（2026-11-12。prev_inputs.py）の回帰テスト。

公開するテストなので、実ページは使わず、実ページ（research/prev_rows_check_20261112/。git に入れていない）で確かめた
構造だけを再現した小さな模擬の HTML を使う。実ページでの確かめは tests/test_prev_rows_real_20261112.py（手元に
ページが無ければ飛ばす）。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import prev_inputs as pi  # noqa: E402
from data_quality import USABLE_PREV_STATUSES  # noqa: E402
from race_pickup import score_horses  # noqa: E402
from rerun_failed_pickup import needs_rerun  # noqa: E402

HID, NAME = "2020100233", "セイウンパシュート"
COLS = ["日付", "開催", "レース名", "人気", "着順", "距離", "ﾀｲﾑ指数"]


def page(rows=None, hid=HID, name=NAME, canonical=None, h2=None, no_results=None, extra=""):
    """馬ページの模擬。rows=None なら成績の表を出さず「競走データがありません」（no_results の数だけ）。
    rows: [(日付, レースID, 人気, 着順, 距離, 指数)]"""
    canonical = HORSE_URL(hid) if canonical is None else canonical
    h2 = f"{name}の競走成績" if h2 is None else h2
    if rows is None:
        body = "".join('<div class="contents"> 競走データがありません </div>' for _ in range(1 if no_results is None else no_results))
    else:
        head = "<tr>" + "".join(f"<th>{c}</th>" for c in COLS) + "</tr>"
        trs = "".join(
            f"<tr><td>{d}</td><td>5東京2</td><td><a href=\"https://db.netkeiba.com/race/{rid}/\">テスト</a></td>"
            f"<td>{pop}</td><td>{rank}</td><td>{dist}</td><td class=\"{pi.IDX_CELL_CLASS} bml\">\n {idx} \n</td></tr>"
            for d, rid, pop, rank, dist, idx in rows)
        body = f'<table class="db_h_race_results nk_tb_common"><thead>{head}</thead><tbody>{trs}</tbody></table>'
        if no_results:
            body += '<div class="contents"> 競走データがありません </div>'
    return (f'<html><head><link rel="canonical" href="{canonical}"></head><body>'
            f'<div class="horse_title"><h1>{name}</h1><p class="txt_01">現役</p></div>'
            f'<div class="db_main_deta"><span id="horse_results_box"><div class="cate_bar"><h2>{h2}</h2></div>'
            f'{body}</span></div>{extra}</body></html>')


def HORSE_URL(hid):
    return f"https://db.netkeiba.com/horse/{hid}/"


JUMP = ("2026/10/03", "202605040104", "2", "8", "障3100", "")
FLAT_OLD = ("2026/05/10", "202605020602", "4", "3", "芝2000", "88")


def parse(html, target="20261107", hid=HID, name=NAME):
    return pi.parse_results_table(html, target, hid, name)


class NoRowsBeforeTargetTest(unittest.TestCase):
    """成績の表があって対象日より前の行が無い場合も、ページを確かめてから前走なし（2026-11-13〜。前走なしの全経路で同じ確かめ）"""
    TODAY_ROW = ("2026/11/07", "202605050211", "1", "1", "芝1600", "90")

    def test_normal_page_is_no_prev(self):
        for rows in ([], [self.TODAY_ROW]):
            e = parse(page(rows=rows))
            self.assertEqual(e["status"], pi.NO_PREV, rows)
            self.assertIn("ページの馬ID・馬名・見出しを確かめた", e["reason"])

    def test_abnormal_page_is_failed(self):
        bad = {"別の馬名": page(rows=[], name="ベツノウマ"),
               "別の馬ID": page(rows=[], canonical=HORSE_URL("2020100234")),
               "見出しが違う": page(rows=[], h2="ベツノウマの競走成績"),
               "存在しない馬": page(rows=[], name="", canonical="https://db.netkeiba.com/horse//")}
        for why, html in bad.items():
            e = parse(html)
            self.assertEqual(e["status"], pi.FAILED, why)
            self.assertIn("前走なしとは確かめられない", e["reason"], why)

    def test_without_id_or_name_is_failed(self):
        self.assertEqual(pi.parse_results_table(page(rows=[]), "20261107")["status"], pi.FAILED)


class DebutTest(unittest.TestCase):
    def test_debut_page_is_no_prev(self):
        e = parse(page())
        self.assertEqual(e["status"], pi.NO_PREV)
        self.assertIn("初出走", e["reason"])

    def test_name_with_spaces_only_difference_is_ok(self):
        self.assertEqual(parse(page(name="セイウン パシュート"))["status"], pi.NO_PREV)

    def test_nonexistent_horse_page_is_failed(self):
        """存在しない馬IDのページ（馬名が空・見出し「の競走成績」・canonical が /horse//）にも同じ文字が出る"""
        e = parse(page(name="", canonical="https://db.netkeiba.com/horse//"))
        self.assertEqual(e["status"], pi.FAILED)
        self.assertIn("成績の表が無い", e["reason"])

    def test_each_identity_condition_is_required(self):
        bad = {
            "別の馬名": page(name="ベツノウマ"),
            "別の馬ID": page(canonical=HORSE_URL("2020100234")),
            "見出しが違う": page(h2="ベツノウマの競走成績"),
            "文字が無い": page(no_results=0),
            "文字が2つ": page(no_results=2),
            "成績の欄が2つ": page(extra='<span id="horse_results_box"><h2>x</h2></span>'),
            "canonical が2つ": page(extra=f'<link rel="canonical" href="{HORSE_URL(HID)}">'),
        }
        for k, html in bad.items():
            self.assertEqual(parse(html)["status"], pi.FAILED, k)

    def test_without_id_or_name_is_failed(self):
        self.assertEqual(pi.parse_results_table(page(), "20261107")["status"], pi.FAILED)
        self.assertEqual(parse(page(), name=None)["status"], pi.FAILED)

    def test_table_and_no_results_text_together_is_failed(self):
        self.assertEqual(parse(page([FLAT_OLD], no_results=1))["status"], pi.FAILED)

    def test_two_tables_is_failed(self):
        html = page([FLAT_OLD]).replace("</table>", "</table><table class=\"db_h_race_results\"></table>")
        self.assertEqual(parse(html)["status"], pi.FAILED)


class ScratchTest(unittest.TestCase):
    SCR = ("2026/10/24", "202604040304", "", "取", "芝2000", "")
    EXC = ("2026/09/12", "202606040311", "", "除", "ダ1200", "")

    def test_scratch_row_is_skipped(self):
        e = parse(page([self.SCR, FLAT_OLD]))
        self.assertEqual((e["status"], e["values"]["date"], e["values"]["idx"]), (pi.OK, "20260510", 88))
        self.assertEqual([(x["rank"], x["meaning"]) for x in e["skipped_rows"]], [("取", "出走取消")])

    def test_only_not_started_rows_with_normal_page_is_no_prev(self):
        for rows in ([self.SCR], [self.SCR, self.EXC]):
            e = parse(page(rows))
            self.assertEqual(e["status"], pi.NO_PREV, rows)
            self.assertIn("実際に出走した行が無い", e["reason"])
            self.assertEqual(e["selected_reason"], "対象日より前に実際に出走した行が無い")
            self.assertEqual(len(e["skipped_rows"]), len(rows))

    def test_only_not_started_rows_with_abnormal_page_is_failed(self):
        e = parse(page([self.SCR], name="ベツノウマ"))
        self.assertEqual(e["status"], pi.FAILED)
        self.assertEqual(len(e["skipped_rows"]), 1)

    def test_scratch_then_unknown_spelling_is_failed(self):
        e = parse(page([self.SCR, ("2026/09/12", "202606040311", "", "失", "ダ1200", ""), FLAT_OLD]))
        self.assertEqual(e["status"], pi.FAILED)

    def test_rows_after_target_do_not_count(self):
        """対象日より後の出走は関係しない（対象日より前の行が取消だけなら前走なし）"""
        later = ("2026/11/08", "202605050208", "3", "1", "ダ1300", "90")
        self.assertEqual(parse(page([later, self.SCR]))["status"], pi.NO_PREV)


class JumpTest(unittest.TestCase):
    def test_jump_row_with_empty_idx_on_normal_page(self):
        e = parse(page([JUMP, FLAT_OLD]))
        self.assertEqual(e["status"], pi.JUMP_IDX_EMPTY)
        self.assertEqual(e["values"], {"date": "20261003", "race_id": "202605040104", "rank": 8, "pop": 2,
                                       "dist": 3100, "surface_dist": "障3100", "idx": None})

    def test_stars_anywhere_in_idx_column_is_failed(self):
        e = parse(page([JUMP, ("2026/06/01", "202605030101", "3", "2", "芝1800", "**"), FLAT_OLD]))
        self.assertEqual(e["status"], pi.FAILED)
        self.assertIn("**", e["reason"])

    def test_no_numeric_idx_before_newest_row_is_failed(self):
        """障害戦しか走っていない馬など、最新の行より前に数字の指数が無ければ、ログインした状態を確かめられない"""
        jump_old = ("2026/08/15", "202604030401", "7", "4", "障3250", "")
        for rows in ([JUMP], [JUMP, jump_old]):
            e = parse(page(rows))
            self.assertEqual(e["status"], pi.FAILED, rows)
            self.assertIn("ログインした状態を確かめられない", e["reason"])

    def test_numeric_only_in_newest_row_is_not_enough(self):
        """最新の行（対象日より後）だけが数字でも、ログインしていない状態でも見える行なので証拠にしない"""
        newest = ("2026/11/08", "202605050208", "3", "1", "ダ1300", "90")
        self.assertEqual(parse(page([newest, JUMP]))["status"], pi.FAILED)

    def test_abnormal_page_is_failed(self):
        self.assertEqual(parse(page([JUMP, FLAT_OLD], name="ベツノウマ"))["status"], pi.FAILED)
        self.assertEqual(pi.parse_results_table(page([JUMP, FLAT_OLD]), "20261107")["status"], pi.FAILED)

    def test_jump_row_with_stars_or_numeric_idx(self):
        self.assertEqual(parse(page([JUMP[:5] + ("**",), FLAT_OLD]))["status"], pi.FAILED)
        e = parse(page([JUMP[:5] + ("75",), FLAT_OLD]))   # 障害戦でも数字があれば、今までどおりの「取れた」
        self.assertEqual((e["status"], e["values"]["idx"]), (pi.OK, 75))

    def test_flat_row_with_empty_idx_is_still_failed(self):
        flat_empty = ("2026/10/03", "202605040108", "2", "8", "芝2000", "")
        self.assertEqual(parse(page([flat_empty, FLAT_OLD]))["status"], pi.FAILED)

    def test_jump_stopped_race_is_idx_empty(self):
        """障害戦の競走中止（着順「中」）は、今までどおり「指数が空（着順が数字でない）」"""
        e = parse(page([("2026/10/03", "202605040104", "2", "中", "障3100", ""), FLAT_OLD]))
        self.assertEqual(e["status"], pi.IDX_EMPTY)


def entry(status, **v):
    values = None
    if status in (pi.OK, pi.IDX_EMPTY, pi.JUMP_IDX_EMPTY):
        values = {"date": "20261020", "race_id": "202605040104", "pop": 3, "rank": 2, "dist": 3100,
                  "surface_dist": "障3100", "idx": None, **v}
    return {"status": status, "reason": "", "values": values, "target_date": "20261107",
            "fetched_at": "2026-11-06T17:30:00+09:00", "fetch_version": pi.PREV_FETCH_VERSION, "source": "取得"}


class DownstreamTest(unittest.TestCase):
    """新しい状態「指数が空（障害戦）」を、読み取り以外の処理にも通す"""

    def test_reusable_and_quality_ok(self):
        self.assertTrue(pi.is_reusable(entry(pi.JUMP_IDX_EMPTY), "20261107"))
        self.assertIn(pi.JUMP_IDX_EMPTY, USABLE_PREV_STATUSES)

    def test_prev_db_entry_keeps_date_pop_rank_dist_but_not_idx(self):
        self.assertEqual(pi.prev_db_entry(entry(pi.JUMP_IDX_EMPTY)),
                         {"prev_date": "2026/10/20", "prev_pop": "3", "prev_rank": "2", "prev_idx": "",
                          "prev_dist": "障3100"})

    def test_race_top1_excludes_jump(self):
        ents = {"A": entry(pi.OK, idx=80, surface_dist="芝2000"), "B": entry(pi.JUMP_IDX_EMPTY)}
        t = pi.race_top1({"1": "A", "2": "B"}, ents)
        self.assertEqual((t["decision"], t["race_max"], t["excluded"]), ("判定", 80, {"2": pi.JUMP_IDX_EMPTY}))
        self.assertEqual(t["rule"], pi.TOP1_RULE)

    def test_status_summary_and_rerun(self):
        ents = {"A": entry(pi.OK, idx=80), "B": entry(pi.JUMP_IDX_EMPTY), "C": entry(pi.JUMP_IDX_EMPTY)}
        active = {"1": "A", "2": "B", "3": "C"}
        st = pi.prev_status_summary(["3"], active, ents, pi.race_top1(active, ents))
        self.assertEqual(st, {"candidates": {"3": pi.JUMP_IDX_EMPTY}, "others": {pi.OK: 1, pi.JUMP_IDX_EMPTY: 1},
                              "top1": "判定"})
        self.assertEqual(needs_rerun({"quality": {"prev_status": st}}), [])

    def test_scoring_uses_date_pop_rank_but_no_index_points(self):
        """指数の加点・レース内1位は付かず、中4週（18日）と前走好走（3人気2着）は付く。障3100 と 2000m は同距離でない"""
        saved = {"position_nums": set(), "top3_hits": {}, "horse_id_map": {"3": "B"}, "pop_map": {}, "odds_map": {}}
        prev_db = {"B": pi.prev_db_entry(entry(pi.JUMP_IDX_EMPTY))}
        scored = score_horses([{"馬番": 3, "馬名": "x"}], saved, {"pickup_nums": set(), "analysis_hits": {}},
                              prev_db=prev_db, race_max_prev_idx=80, race_date="20261107", race_dist=2000)
        labels = [b["label"] for b in scored[0]["breakdown"]]
        self.assertEqual(scored[0]["score"], 4, labels)
        self.assertTrue(any(x.startswith("中4週以内(18日)") for x in labels), labels)
        self.assertTrue(any(x.startswith("前走好走") for x in labels), labels)
        self.assertFalse(any("前走指数" in x for x in labels), labels)


if __name__ == "__main__":
    unittest.main()
