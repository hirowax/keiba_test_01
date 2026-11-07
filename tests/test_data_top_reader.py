"""
3項目（各データ上位3頭・データ分析ピックアップ・出走馬分析）の読み取りの回帰テスト（2026-11-07〜。DATA_TOP_READER_VERSION）。

ページは公開のリポジトリに入れない決定（2026-11-07）なので、実ページ（research/rebuild_trial_202611/pages/race/ の
保存ページ）と同じ形の HTML を、ここに小さく書いて試す。形は 2026-11-07 に保存した6レースのページで確かめたもの:
- 出馬表の行: td.Waku{n}（枠番）・td.Umaban{n}（馬番）・td.HorseInfo の /horse/{ID} リンク（正式名）
- 上位3頭: section.top3data > table.AnaBestTable > .PickupHorseTableTitle（見出し）・div.Kyaku_Type_box >
  span.WakuBan{枠}.Kyaku_Type_Num（馬番）・span.UmaName（短い名前）
- ピックアップ: .DataPickupHorseWrap > dl > dt > span.Waku{枠}.Umaban_Num（馬番）・a.data_top_horse_link（馬ID）
- 出走馬分析: table.PickupHorseTable01 > th > .PickupHorseTableTitle（見出しは表の中）・td > div.Umaban_InBox >
  span.Waku{枠}.Umaban_Num（馬番）＋短い名前。推奨なしは span.Txt。枠順の表は Umaban_Num の中に span.WakuTxt「枠」
手元に保存ページがあれば、それでも確かめる（無ければ飛ばす）。

実行: python3 -m unittest discover -s tests -v
"""
import gzip
import json
import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import race_pickup as rp  # noqa: E402
from data_quality import build_quality, race_quality_issues  # noqa: E402

# 多頭数（枠番≠馬番）。馬番10・11 は同じ枠6。馬番1と12 は名前の先頭が同じ（「アスク」）
HORSES = {
    "1": ("1", "アスクビクター", "2023100001"),
    "2": ("1", "ダイシンレアレア", "2023100002"),
    "6": ("3", "ロードヴェリタス", "2023100006"),
    "7": ("4", "ハイドアンドシーク", "2023100007"),
    "10": ("6", "リバーソニック", "2023100010"),
    "11": ("6", "エクスペリエンス", "2023100011"),
    "12": ("7", "アスクワンダー", "2023100012"),
    "13": ("7", "タニセンビクトリー", "2023100013"),
}


def ref():
    return {"horse_name_full": {n: v[1] for n, v in HORSES.items()},
            "waku_map": {n: v[0] for n, v in HORSES.items()},
            "horse_id_map": {n: v[2] for n, v in HORSES.items()}}


def top3_table(title, items):
    boxes = "".join(f'<div class="Kyaku_Type_box"><span class="WakuBan{w} Kyaku_Type_Num">{n}</span><br/>'
                    f'<span class="UmaName">{s}</span></div>' for n, w, s in items)
    return (f'<table class="RaceCommon_Table PickupHorseTable01 AnaBestTable"><tbody><tr><th>'
            f'<div class="PickupHorseTableTitle">{title}</div></th><td><a href="#"><div class="AnaBest_HorseBox">'
            f'{boxes}<span class="AnaBest_More LinkMore">へ</span></div></a></td></tr></tbody></table>')


def top3_page(*tables):
    return BeautifulSoup('<section class="top3data"><div class="PickupHorseArea">' + "".join(tables)
                         + '</div></section>', "html.parser")


def analysis_table(title, items=None, txt=None, waku_txt=False):
    if items:
        inner = "".join(
            f'<div class="Umaban_InBox"><span class="Waku{w} Umaban_Num">{n}'
            + ('<span class="WakuTxt">枠</span>' if waku_txt else '') + f'</span>{s}</div>' for n, w, s in items)
    else:
        inner = f'<div><span class="Txt">{txt}</span></div>'
    return (f'<table class="RaceCommon_Table PickupHorseTable01 PickupRaceDataTable01"><tbody><tr><th>'
            f'<div class="PickupHorseTableTitle">{title}</div></th><td><a href="#">{inner}'
            f'<span class="arrow"></span></a></td></tr></tbody></table>')


def pickup_block(items):
    dls = "".join(f'<dl><dt><span class="Waku{w} Umaban_Num">{n}</span>'
                  f'<a class="data_top_horse_link" href="https://db.netkeiba.com/horse/{hid}/?rf=data_top">{nm}</a>'
                  f'</dt><dd class="PickupDataBox"></dd></dl>' for n, w, nm, hid in items)
    return f'<div class="DataPickupHorseWrap">{dls}</div>'


def data_top_page(pickup, *tables):
    return BeautifulSoup(pickup + "".join(tables), "html.parser")


GOOD_PICKUP = pickup_block([("10", "6", "リバーソニック", "2023100010"), ("2", "1", "ダイシンレアレア", "2023100002"),
                            ("6", "3", "ロードヴェリタス", "2023100006")])
OTHER_TABLES = [analysis_table("このコースで有利な枠順", [("8", "8", ""), ("3", "3", "")], waku_txt=True),
                analysis_table("このコースが得意な騎手", txt="吉田隼人"),
                analysis_table("このコースに実績がある母父", txt="ディープインパクト")]


def six_horse_tables(**override):
    base = {
        "このコースが得意な馬": [("10", "6", "リバー"), ("11", "6", "エクス"), ("7", "4", "ハイド")],
        "この距離が得意な馬": None,
        "この競馬場が得意な馬": [("6", "3", "ロード"), ("10", "6", "リバー")],
        "今回の馬場状態が得意な馬": [("2", "1", "ダイシ"), ("10", "6", "リバー"), ("13", "7", "タニセ")],
        "今回のレース間隔で実績がある馬": [("13", "7", "タニセ")],
        "今回の調教評価で実績がある馬": [("13", "7", "タニセ"), ("10", "6", "リバー")],
    }
    base.update(override)
    out = []
    for t, items in base.items():
        out.append(analysis_table(t, items) if items else analysis_table(t, txt="推奨なし"))
        if t == "今回の調教評価で実績がある馬":
            out.append(analysis_table("今回のオッズで期待できる騎手", txt="丹内祐次"))
    return out


class AnalysisTest(unittest.TestCase):
    def test_counts_every_horse_by_own_title(self):
        """見出しは表の中のものを使い、表の全頭を数える（v5.1 までは1つ前の表の見出し・最初の1頭だけ）"""
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *OTHER_TABLES, *six_horse_tables()), ref())
        self.assertEqual(res["issues"], [])
        self.assertEqual(res["analysis_hits"], {"10": 4, "11": 1, "7": 1, "6": 1, "2": 1, "13": 3})
        states = {t["title"]: t["state"] for t in res["analysis_tables"]}
        self.assertEqual(states["このコースが得意な馬"], "馬あり")   # v5.1 は数えていなかった表
        self.assertEqual(states["この距離が得意な馬"], "推奨なし")
        self.assertEqual(res["analysis_state"], "あり")

    def test_jockey_table_is_not_counted(self):
        """騎手名だけの表（今回のオッズで期待できる騎手）は数えない。騎手名が馬名の一部に似ていても数えない"""
        tabs = six_horse_tables()
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref())
        self.assertNotIn("今回のオッズで期待できる騎手", [t["title"] for t in res["analysis_tables"]])

    def test_unknown_title_is_an_issue(self):
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, analysis_table("このコースのクッション値が得意な馬",
                                                                          [("10", "6", "リバー")])), ref())
        self.assertTrue(any("知らない見出し" in x for x in res["issues"]))
        self.assertEqual(res["analysis_hits"], {})

    def test_wrong_waku_name_or_missing_row_is_not_counted(self):
        """枠が違う・名前が違う・行が無い馬番は数えず、取得異常にする（0点にして黙って続けない）"""
        tabs = six_horse_tables(**{"この距離が得意な馬": [("10", "5", "リバー"), ("6", "3", "タニセ"), ("15", "8", "ナナシ")]})
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref())
        self.assertEqual(len([x for x in res["issues"] if "この距離が得意な馬" in x]), 3)
        st = {t["title"]: t["state"] for t in res["analysis_tables"]}
        self.assertEqual(st["この距離が得意な馬"], "取得異常")
        self.assertEqual(res["analysis_hits"]["10"], 4)   # 合わない項目は足さない

    def test_ambiguous_short_name_is_an_issue(self):
        """短い名前が2頭の正式名の先頭と同じ（アスク: 1番と12番）なら照らせない"""
        tabs = six_horse_tables(**{"この距離が得意な馬": [("1", "1", "アスク")]})
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref())
        self.assertTrue(any("別の馬がいる" in x for x in res["issues"]))
        self.assertNotIn("1", res["analysis_hits"])

    def test_duplicate_in_table(self):
        tabs = six_horse_tables(**{"この距離が得意な馬": [("10", "6", "リバー"), ("10", "6", "リバー")]})
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref())
        self.assertTrue(any("2回ある" in x for x in res["issues"]))
        self.assertEqual(res["analysis_hits"]["10"], 5)   # 1回だけ数える

    def test_missing_section_and_missing_table(self):
        """欄全体が無い（9/12 のページ）と、6種類のうち一部の表が無いのは「欄がない」として記録する（取得異常ではない）"""
        res = rp.parse_data_top(BeautifulSoup("<div>データなし</div>", "html.parser"), ref())
        self.assertEqual((res["pickup_state"], res["analysis_state"], res["issues"]), ("欄がない", "欄がない", []))
        tabs = [t for t in six_horse_tables() if "この競馬場が得意な馬" not in t]
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref())
        st = {t["title"]: t["state"] for t in res["analysis_tables"]}
        self.assertEqual(st["この競馬場が得意な馬"], "欄がない")
        self.assertEqual(res["issues"], [])

    def test_empty_table_without_suisho_nashi_is_an_issue(self):
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, analysis_table("この距離が得意な馬", txt="")), ref())
        self.assertTrue(any("馬も「推奨なし」も無い" in x for x in res["issues"]))


class PickupTest(unittest.TestCase):
    def test_reads_umaban_and_horse_id(self):
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP), ref())
        self.assertEqual(res["pickup_nums"], {"10", "2", "6"})
        self.assertEqual(res["pickup_state"], "馬あり")

    def test_horse_id_mismatch_is_an_issue(self):
        bad = pickup_block([("10", "6", "リバーソニック", "2023999999")])
        res = rp.parse_data_top(data_top_page(bad), ref())
        self.assertEqual(res["pickup_nums"], set())
        self.assertTrue(any("馬ID" in x for x in res["issues"]))


class Top3Test(unittest.TestCase):
    def test_counts_tables_including_jockey(self):
        """数字は馬番（枠番ではない）。騎手の表も今の定義どおり数える（馬番と枠だけ照らす）"""
        soup = top3_page(top3_table("能力", [("2", "1", "ダイシ"), ("6", "3", "ロード"), ("13", "7", "タニセ")]),
                         top3_table("騎手", [("1", "1", "小沢大"), ("2", "1", "丹内祐"), ("10", "6", "吉田隼")]))
        res = rp.parse_top3data(soup, ref())
        self.assertEqual(res["issues"], [])
        self.assertEqual(res["hits"], {"2": 2, "6": 1, "13": 1, "1": 1, "10": 1})

    def test_wrong_waku_or_name(self):
        soup = top3_page(top3_table("能力", [("2", "2", "ダイシ"), ("6", "3", "リバー")]))
        res = rp.parse_top3data(soup, ref())
        self.assertEqual(res["hits"], {})
        self.assertEqual(len(res["issues"]), 2)

    def test_missing_section(self):
        res = rp.parse_top3data(BeautifulSoup("<div></div>", "html.parser"), ref())
        self.assertEqual((res["state"], res["issues"], res["hits"]), ("欄がない", [], {}))


class QualityTest(unittest.TestCase):
    def test_reader_issues_make_race_bad(self):
        src = {"index_reader_version": "r", "candidate_rule_version": "c",
               "races": {"東京7R": {"modes": {"average": "type=rank"}}}}
        q = build_quality("v", src, "東京7R", data_top_reader=rp.DATA_TOP_READER_VERSION,
                          data_top_issues=["出走馬分析（この距離が得意な馬）: 馬番15の行が出馬表に無い"])
        issues = race_quality_issues({"collector_version": "v", "quality": q})
        self.assertTrue(any(x.startswith("3項目") for x in issues))


PAGES = ROOT / "research" / "rebuild_trial_202611" / "pages" / "race"


@unittest.skipUnless(PAGES.exists(), "手元に保存ページが無い")
class SavedPagesTest(unittest.TestCase):
    """手元の保存ページ（6レース）で、確認スクリプト（research/reading_check_3items_20261107/）の結果と同じになる"""

    def test_matches_reading_check(self):
        class SP:
            def __init__(self, h):
                self.h = h

            def goto(self, *a, **k):
                return None

            def content(self):
                return self.h
        expect = json.load(open(ROOT / "research" / "reading_check_3items_20261107" / "result_20261107.json", encoding="utf-8"))
        races = json.load(open(ROOT / "research" / "rebuild_trial_202611" / "selected_races.json", encoding="utf-8"))
        for r in races:
            rid = r["race_id"]
            sh = rp.scrape_shutuba(SP(gzip.open(PAGES / f"{rid}_shutuba.html.gz", "rt", encoding="utf-8").read()), rid)
            dt = rp.scrape_data_top(SP(gzip.open(PAGES / f"{rid}_data_top.html.gz", "rt", encoding="utf-8").read()), rid, sh)
            e = expect[f"{r['date']} {r['label']}"]
            with self.subTest(race=r["label"]):
                self.assertEqual(sh["top3_hits"], e["top3_this_reading"])
                self.assertEqual(sorted(dt["pickup_nums"], key=int), e["pickup_this_reading"])
                self.assertEqual(dt["analysis_hits"], e["analysis_this_reading"])
                self.assertEqual(sh["top3_issues"] + dt["issues"], [])


if __name__ == "__main__":
    unittest.main()
