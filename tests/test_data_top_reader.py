"""
3項目（各データ上位3頭・データ分析ピックアップ・出走馬分析）の読み取りの回帰テスト（2026-11-07〜。DATA_TOP_READER_VERSION）。

ページは公開のリポジトリに入れない決定（2026-11-07）なので、実ページ（research/rebuild_trial_202611/pages/race/ の
保存ページ）と同じ形の HTML を、ここに小さく書いて試す。形は 2026-11-07 に保存した6レースのページで確かめたもの:
- 出馬表の行: td.Waku{n}（枠番）・td.Umaban{n}（馬番）・td.HorseInfo の /horse/{ID} リンク（正式名）
- 上位3頭: section.top3data > table.AnaBestTable > .PickupHorseTableTitle（見出し）・div.Kyaku_Type_box >
  span.WakuBan{枠}.Kyaku_Type_Num（馬番）・span.UmaName（短い名前）
- ピックアップ: .DataPickupHorseWrap > dl > dt > span.Waku{枠}.Umaban_Num（馬番）・a.data_top_horse_link（馬ID）
- 出走馬分析: .RaceDataPickupList > div.RaceDataPickup#RaceDataPickup01（h3.Title_3rd「出走馬分析」）> table.PickupHorseTable01 > th > .PickupHorseTableTitle（見出しは表の中）・td >
  div.Umaban_InBox > span.Waku{枠}.Umaban_Num（馬番）＋短い名前。推奨なしは span.Txt。枠順の表は Umaban_Num の中に
  span.WakuTxt「枠」
- データ分析のページ: link rel=canonical に race_id、h3.TitleHeadingDataTop（見出し「データ分析」）。公開されていないときは
  .Race_Infomation_Box に「データ分析はレース当週およびレース前週のみの公開です。」（9/12 中山3R のページ）
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


RID = "202603030104"


def page_head(rid=RID, kind="data_top"):
    return f'<link rel="canonical" href="https://race.netkeiba.com/race/{kind}.html?race_id={rid}"/>'


def section(sid, title, tables):
    """データ分析の欄の1つ（実ページ: div.RaceDataPickup#RaceDataPickup0N > h3.Title_3rd ＋ ul.RaceDataTableList > li > table）"""
    return (f'<div class="RaceDataPickup " id="{sid}"><h3 class="Title_3rd"><span class="Icon_RaceData"></span>{title}</h3>'
            '<ul class="RaceDataTableList">' + "".join(f"<li>{t}</li>" for t in tables) + '</ul></div>')


def data_top_page(pickup, *tables, rid=RID, sections=None):
    """tables は出走馬分析の欄（RaceDataPickup01）に入れる。sections を渡すと、その欄を並べる"""
    if sections is None:
        sections = [("RaceDataPickup01", "出走馬分析", tables)]
    return BeautifulSoup(page_head(rid) + '<div class="RaceColumn02">' + pickup
                         + '<h3 class="TitleHeading TitleHeadingDataTop">データ分析</h3>'
                         + '<div class="RaceDataPickupList clearfix">' + "".join(section(*x) for x in sections)
                         + '</div></div>', "html.parser")


GOOD_PICKUP = pickup_block([("10", "6", "リバーソニック", "2023100010"), ("2", "1", "ダイシンレアレア", "2023100002"),
                            ("6", "3", "ロードヴェリタス", "2023100006")])
# コース分析の欄（RaceDataPickup02）・騎乗騎手分析の欄（RaceDataPickup04）の表
JOCKEY_TABLES = [analysis_table("今回のオッズで期待できる騎手", txt="丹内祐次")]
OTHER_TABLES = [analysis_table("このコースで有利な枠順", [("8", "8", ""), ("3", "3", "")], waku_txt=True),
                analysis_table("このコースが得意な騎手", txt="吉田隼人"),
                analysis_table("このコースに実績がある母父", txt="ディープインパクト")]


def horse_tables(**override):
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
            # 馬の表だが数えない（2026-11-07 ユーザー決定）
            out.append(analysis_table("今回の厩舎コメント評価で実績がある馬", [("6", "3", "ロード"), ("2", "1", "ダイシ")]))
    return out


class AnalysisTest(unittest.TestCase):
    def test_counts_every_horse_by_own_title(self):
        """見出しは表の中のものを使い、表の全頭を数える（v5.1 までは1つ前の表の見出し・最初の1頭だけ）"""
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, sections=[
            ("RaceDataPickup02", "東京芝1400mのコース分析", OTHER_TABLES),
            ("RaceDataPickup01", "出走馬分析", horse_tables()),
            ("RaceDataPickup04", "騎乗騎手分析", JOCKEY_TABLES)]), ref(), RID)
        self.assertEqual(res["issues"], [])
        self.assertEqual(res["analysis_hits"], {"10": 4, "11": 1, "7": 1, "6": 1, "2": 1, "13": 3})
        self.assertEqual(res["page_state"], "公開")
        states = {t["title"]: t["state"] for t in res["analysis_tables"]}
        self.assertEqual(states["このコースが得意な馬"], "馬あり")   # v5.1 は数えていなかった表
        self.assertEqual(states["この距離が得意な馬"], "推奨なし")
        self.assertEqual(res["analysis_state"], "あり")

    def test_jockey_table_is_not_counted(self):
        """騎乗騎手分析の欄の表（今回のオッズで期待できる騎手）は読まない"""
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, sections=[
            ("RaceDataPickup01", "出走馬分析", horse_tables()), ("RaceDataPickup04", "騎乗騎手分析", JOCKEY_TABLES)]),
            ref(), RID)
        self.assertEqual(res["issues"], [])
        self.assertNotIn("今回のオッズで期待できる騎手", [t["title"] for t in res["analysis_tables"]])

    def test_stable_comment_table_is_not_counted(self):
        """「今回の厩舎コメント評価で実績がある馬」は馬の表だが数えない。無くても異常にしない（2026-11-07 ユーザー決定）"""
        tabs = horse_tables()
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref(), RID)
        self.assertNotIn("今回の厩舎コメント評価で実績がある馬", [t["title"] for t in res["analysis_tables"]])
        no_comment = [t for t in tabs if "厩舎コメント" not in t]
        res2 = rp.parse_data_top(data_top_page(GOOD_PICKUP, *no_comment), ref(), RID)
        self.assertEqual((res["analysis_hits"], res["issues"]), (res2["analysis_hits"], []))
        self.assertEqual(res2["issues"], [])

    def test_unknown_title_is_an_issue(self):
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, analysis_table("このコースのクッション値が得意な馬",
                                                                          [("10", "6", "リバー")])), ref(), RID)
        self.assertTrue(any("知らない見出し" in x for x in res["issues"]))
        self.assertEqual(res["analysis_hits"], {})

    def test_wrong_waku_name_or_missing_row_is_not_counted(self):
        """枠が違う・名前が違う・行が無い馬番は数えず、取得異常にする（0点にして黙って続けない）"""
        tabs = horse_tables(**{"この距離が得意な馬": [("10", "5", "リバー"), ("6", "3", "タニセ"), ("15", "8", "ナナシ")]})
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref(), RID)
        self.assertEqual(len([x for x in res["issues"] if "この距離が得意な馬" in x]), 3)
        st = {t["title"]: t["state"] for t in res["analysis_tables"]}
        self.assertEqual(st["この距離が得意な馬"], "取得異常")
        self.assertEqual(res["analysis_hits"]["10"], 4)   # 合わない項目は足さない

    def test_same_short_name_is_decided_by_umaban(self):
        """短い名前が2頭の正式名の先頭と同じ（アスク: 1番と12番）でも、馬番でその行の正式名の馬に決まる
        （2026-11-08 ユーザー決定。11/8 京都4R の「アメリ」7番・13番と同じ形）"""
        tabs = horse_tables(**{"この距離が得意な馬": [("1", "1", "アスク"), ("12", "7", "アスク")]})
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref(), RID)
        self.assertEqual([x for x in res["issues"] if "この距離が得意な馬" in x], [])
        self.assertEqual(res["analysis_hits"]["1"], 1)
        self.assertEqual(res["analysis_hits"]["12"], 1)
        t = [x for x in res["analysis_tables"] if x["title"] == "この距離が得意な馬"][0]
        self.assertEqual(t["names"], ["アスクビクター", "アスクワンダー"])

    def test_same_short_name_with_wrong_waku_is_an_issue(self):
        """馬番で決めるので、枠が出馬表のその馬番と違えば取得異常（12番の枠は7。8と書かれていれば合わない）"""
        tabs = horse_tables(**{"この距離が得意な馬": [("12", "8", "アスク")]})
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref(), RID)
        self.assertTrue(any("馬番12の枠" in x for x in res["issues"]))
        self.assertNotIn("12", res["analysis_hits"])

    def test_duplicate_in_table(self):
        tabs = horse_tables(**{"この距離が得意な馬": [("10", "6", "リバー"), ("10", "6", "リバー")]})
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref(), RID)
        self.assertTrue(any("2回ある" in x for x in res["issues"]))
        self.assertEqual(res["analysis_hits"]["10"], 5)   # 1回だけ数える

    def test_missing_table_is_an_issue(self):
        """公開されているページで、数える7種類のうち一部の表が無いのは「欄がない」と記録し、取得異常にする
        （無いことが正常かは確かめていない。2026-11-07 外部レビュー）"""
        tabs = [t for t in horse_tables() if "この競馬場が得意な馬" not in t]
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref(), RID)
        st = {t["title"]: t["state"] for t in res["analysis_tables"]}
        self.assertEqual(st["この競馬場が得意な馬"], "欄がない")
        self.assertEqual(res["issues"], ["出走馬分析: 数える表が無い（['この競馬場が得意な馬']）"])
        self.assertEqual(res["analysis_state"], "取得異常")

    def test_duplicate_title_counted_once(self):
        """同じ見出しの表が2つあれば、2つ目は数えずに取得異常（同じ馬を2回数えない）"""
        tabs = horse_tables() + [analysis_table("この競馬場が得意な馬", [("6", "3", "ロード")])]
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *tabs), ref(), RID)
        self.assertEqual(res["analysis_hits"]["6"], 1)   # 2つ目の「この競馬場が得意な馬」は数えない
        self.assertTrue(any("同じ見出しの表が2つある" in x for x in res["issues"]))


class AnalysisSectionTest(unittest.TestCase):
    """出走馬分析は id RaceDataPickup01 の欄だけを読む（2026-11-09。11/8 東京11R のページで、レース分析の欄の表を読んでいた）"""
    RACE_TABLES = [analysis_table("このレースで有利な枠順", [("8", "8", ""), ("3", "3", "")], waku_txt=True),
                   analysis_table("このレースが得意な騎手", txt="戸崎圭太"),
                   analysis_table("このレースで狙いの人気", txt="1番人気")]

    def test_race_analysis_section_is_not_read(self):
        """レース分析の欄（11/8 東京11R のページにあった）がある場合: レース分析の欄（RaceDataPickup03）の表は読まない。数は普通のページと同じ"""
        plain = rp.parse_data_top(data_top_page(GOOD_PICKUP, *horse_tables()), ref(), RID)
        g = rp.parse_data_top(data_top_page(GOOD_PICKUP, sections=[
            ("RaceDataPickup03", "アルゼンチン共和国杯のレース分析", self.RACE_TABLES),
            ("RaceDataPickup02", "東京芝2500mのコース分析", OTHER_TABLES),
            ("RaceDataPickup01", "出走馬分析", horse_tables()),
            ("RaceDataPickup04", "騎乗騎手分析", JOCKEY_TABLES)]), ref(), RID)
        self.assertEqual(g["issues"], [])
        self.assertEqual(g["analysis_hits"], plain["analysis_hits"])
        self.assertEqual(g["analysis_state"], "あり")

    def test_missing_section_is_an_issue(self):
        """出走馬分析の欄が無い（11/8 に取った東京11R のページは、レース分析の欄だけだった）"""
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, sections=[
            ("RaceDataPickup03", "アルゼンチン共和国杯のレース分析", self.RACE_TABLES)]), ref(), RID)
        self.assertEqual(res["analysis_state"], "取得異常")
        self.assertEqual(res["analysis_hits"], {})
        self.assertTrue(any("出走馬分析の欄" in x and "欄が無い" in x for x in res["issues"]))

    def test_wrong_heading_is_an_issue(self):
        """id が 01 でも見出しが「出走馬分析」でなければ読まない"""
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, sections=[
            ("RaceDataPickup01", "騎乗騎手分析", horse_tables())]), ref(), RID)
        self.assertEqual(res["analysis_state"], "取得異常")
        self.assertEqual(res["analysis_hits"], {})
        self.assertTrue(any("見出しが「騎乗騎手分析」" in x for x in res["issues"]))

    def test_horse_table_outside_section_is_not_read(self):
        """出走馬分析の見出しの表でも、01 の外にあれば読まない（01 の中に無ければ「数える表が無い」）"""
        tabs = horse_tables()
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, sections=[
            ("RaceDataPickup02", "東京芝1400mのコース分析", tabs[:1]),
            ("RaceDataPickup01", "出走馬分析", tabs[1:])]), ref(), RID)
        self.assertTrue(any("数える表が無い" in x and "このコースが得意な馬" in x for x in res["issues"]))


class ExactlyOneTest(unittest.TestCase):
    """欄を決める要素は、ちょうど1つのときだけ読む（2026-11-09 外部レビュー。最初の1つを黙って使わない）"""
    def test_duplicate_analysis_section(self):
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, sections=[
            ("RaceDataPickup01", "出走馬分析", horse_tables()), ("RaceDataPickup01", "出走馬分析", horse_tables())]),
            ref(), RID)
        self.assertEqual((res["analysis_state"], res["analysis_hits"]), ("取得異常", {}))
        self.assertTrue(any("欄が2つある" in x for x in res["issues"]))

    def test_two_headings_in_section(self):
        tabs = "".join(horse_tables())
        html = (page_head() + GOOD_PICKUP + '<h3 class="TitleHeading TitleHeadingDataTop">データ分析</h3>'
                '<div class="RaceDataPickupList"><div class="RaceDataPickup" id="RaceDataPickup01">'
                '<h3 class="Title_3rd">出走馬分析</h3><h3 class="Title_3rd">騎乗騎手分析</h3>' + tabs + '</div></div>')
        res = rp.parse_data_top(BeautifulSoup(html, "html.parser"), ref(), RID)
        self.assertEqual((res["analysis_state"], res["analysis_hits"]), ("取得異常", {}))
        self.assertTrue(any("見出しが2つ" in x for x in res["issues"]))

    def test_section_outside_list(self):
        html = (page_head() + GOOD_PICKUP + '<h3 class="TitleHeading TitleHeadingDataTop">データ分析</h3>'
                '<div class="RaceDataPickupList">' + section("RaceDataPickup02", "東京芝1400mのコース分析", OTHER_TABLES)
                + '</div>' + section("RaceDataPickup01", "出走馬分析", horse_tables()))
        res = rp.parse_data_top(BeautifulSoup(html, "html.parser"), ref(), RID)
        self.assertEqual((res["analysis_state"], res["analysis_hits"]), ("取得異常", {}))
        self.assertTrue(any("データ分析の欄の外" in x for x in res["issues"]))

    def test_duplicate_list_or_pickup_or_heading(self):
        base = data_top_page(GOOD_PICKUP, *horse_tables())
        for extra, word in (('<div class="RaceDataPickupList"></div>', "データ分析の欄が2つ"),
                            (GOOD_PICKUP, "ピックアップの欄が2つ"),
                            ('<h3 class="TitleHeading TitleHeadingDataTop">データ分析</h3>', "データ分析の見出しが2つ")):
            soup = BeautifulSoup(str(base) + extra, "html.parser")
            res = rp.parse_data_top(soup, ref(), RID)
            self.assertEqual(res["page_state"], "取得失敗", word)
            self.assertEqual((res["pickup_nums"], res["analysis_hits"]), (set(), {}), word)
            self.assertTrue(any(word in x for x in res["issues"]), (word, res["issues"]))

    def test_duplicate_top3_section(self):
        one = str(top3_page(top3_table("能力", [("10", "6", "リバー")])))
        res = rp.parse_top3data(BeautifulSoup(one + one, "html.parser"), ref())
        self.assertEqual((res["state"], res["hits"]), ("取得異常", {}))
        self.assertEqual(res["issues"], ["上位3頭: 出馬表に欄が2つある"])


G1_PAGES = ROOT / "research" / "data_top_g1_202611" / "pages"


@unittest.skipUnless(G1_PAGES.exists(), "手元に保存ページが無い")
class SavedG1PageTest(unittest.TestCase):
    """2026-11-08 東京11R（アルゼンチン共和国杯）の実ページ。出馬表は 11/8 10:5x に取ったもの"""
    RID = "202605050211"

    def test_pages(self):
        class SP:
            def __init__(self, h):
                self.h = h
            def goto(self, *a, **k):
                pass
            def content(self):
                return self.h
        import time as _t
        sleep, _t.sleep = _t.sleep, (lambda *a, **k: None)
        try:
            sh = rp.scrape_shutuba(SP(gzip.open(G1_PAGES / f"{self.RID}_shutuba_20261108.html.gz", "rt",
                                                encoding="utf-8").read()), self.RID)
            now = rp.scrape_data_top(SP(gzip.open(G1_PAGES / f"{self.RID}_data_top_20261109.html.gz", "rt",
                                                  encoding="utf-8").read()), self.RID, sh)
            old = rp.scrape_data_top(SP(gzip.open(G1_PAGES / f"{self.RID}_data_top_20261108.html.gz", "rt",
                                                  encoding="utf-8").read()), self.RID, sh)
        finally:
            _t.sleep = sleep
        # 11/9 に取ったページ: 出走馬分析の6表を読み、レース分析の10表は読まない
        self.assertEqual(now["issues"], [])
        self.assertEqual(now["analysis_hits"], {"18": 2, "7": 2, "15": 1, "14": 2, "3": 1, "9": 3, "5": 1, "8": 2,
                                                "4": 1, "11": 1, "1": 1, "13": 1})
        # 11/8 に取ったページ: データ分析の見出しも出走馬分析の欄も無い → 取得失敗
        self.assertEqual(old["page_state"], "取得失敗")
        self.assertEqual(old["analysis_hits"], {})


class DataTopPageTest(unittest.TestCase):
    """ページを取れなかった場合と、正常なページに欄が無い場合を分ける（2026-11-07 外部レビュー）"""

    def test_login_page_is_fetch_failure(self):
        """ログインの案内だけのページは「欄がない」ではなく取得失敗"""
        soup = BeautifulSoup('<div class="Login">ログインしてください</div><a href="/login">ログイン</a>', "html.parser")
        res = rp.parse_data_top(soup, ref(), RID)
        self.assertEqual((res["page_state"], res["pickup_state"], res["analysis_state"]), ("取得失敗",) * 3)
        self.assertEqual(len(res["issues"]), 1)
        self.assertEqual((res["pickup_nums"], res["analysis_hits"]), (set(), {}))

    def test_login_page_with_canonical_is_fetch_failure(self):
        soup = BeautifulSoup(page_head() + '<div>ログインしてください</div>', "html.parser")
        res = rp.parse_data_top(soup, ref(), RID)
        self.assertEqual(res["page_state"], "取得失敗")
        self.assertIn("データ分析の見出しが無い", res["issues"][0])

    def test_other_race_page_is_fetch_failure(self):
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, *horse_tables(), rid="202603030105"), ref(), RID)
        self.assertEqual(res["page_state"], "取得失敗")
        self.assertIn("レースIDが違う", res["issues"][0])
        self.assertEqual(res["analysis_hits"], {})

    def test_not_published(self):
        """公開期間外の案内（9/12 中山3R のページの文字）は「公開されていない」。評価には使わないので issues に入れる"""
        soup = BeautifulSoup(page_head() + '<h3 class="TitleHeading TitleHeadingDataTop">データ分析</h3>'
                             '<div class="Race_Infomation_Box">データ分析はレース当週およびレース前週のみの公開です。</div>',
                             "html.parser")
        res = rp.parse_data_top(soup, ref(), RID)
        self.assertEqual((res["page_state"], res["pickup_state"], res["analysis_state"]), ("公開されていない",) * 3)
        self.assertEqual(len(res["issues"]), 1)

    def test_no_section_no_notice_is_fetch_failure(self):
        soup = BeautifulSoup(page_head() + '<h3 class="TitleHeading TitleHeadingDataTop">データ分析</h3>', "html.parser")
        res = rp.parse_data_top(soup, ref(), RID)
        self.assertEqual(res["page_state"], "取得失敗")

    def test_analysis_without_pickup(self):
        soup = BeautifulSoup(page_head() + '<h3 class="TitleHeading TitleHeadingDataTop">データ分析</h3>'
                             '<div class="RaceDataPickupList">' + section("RaceDataPickup01", "出走馬分析", horse_tables())
                             + '</div>', "html.parser")
        res = rp.parse_data_top(soup, ref(), RID)
        self.assertEqual(res["pickup_state"], "取得異常")
        self.assertEqual(res["issues"], ["ピックアップ: 出走馬分析はあるがピックアップの欄が無い"])

    def test_empty_table_without_suisho_nashi_is_an_issue(self):
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP, analysis_table("この距離が得意な馬", txt="")), ref(), RID)
        self.assertTrue(any("馬も「推奨なし」も無い" in x for x in res["issues"]))


class PickupTest(unittest.TestCase):
    def test_reads_umaban_and_horse_id(self):
        res = rp.parse_data_top(data_top_page(GOOD_PICKUP), ref(), RID)
        self.assertEqual(res["pickup_nums"], {"10", "2", "6"})
        self.assertEqual(res["pickup_state"], "馬あり")

    def test_horse_id_mismatch_is_an_issue(self):
        bad = pickup_block([("10", "6", "リバーソニック", "2023999999")])
        res = rp.parse_data_top(data_top_page(bad), ref(), RID)
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

    def test_missing_section_is_an_issue(self):
        """保存した7レースの出馬表にはすべて欄があった。無ければ取得異常"""
        res = rp.parse_top3data(BeautifulSoup("<div></div>", "html.parser"), ref())
        self.assertEqual((res["state"], res["hits"]), ("取得異常", {}))
        self.assertEqual(len(res["issues"]), 1)

    def test_unknown_title_is_an_issue(self):
        soup = top3_page(top3_table("能力", [("2", "1", "ダイシ")]), top3_table("人気", [("6", "3", "ロード")]))
        res = rp.parse_top3data(soup, ref())
        self.assertEqual(res["hits"], {"2": 1})
        self.assertEqual(res["issues"], ["上位3頭: 知らない見出しの表がある（人気）"])

    def test_duplicate_title_counted_once(self):
        """同じ見出しの表が2つあれば2つ目は数えない（外部レビューの模擬入力: 同じ馬が2回加点された）"""
        t = top3_table("能力", [("6", "3", "ロード")])
        res = rp.parse_top3data(top3_page(t, t), ref())
        self.assertEqual(res["hits"], {"6": 1})
        self.assertEqual(res["issues"], ["上位3頭: 同じ見出しの表が2つある（能力）"])
        self.assertEqual(res["state"], "取得異常")

    def test_missing_known_table_is_recorded(self):
        """4種類のうち無い表は「欄がない」と記録する（10/25 新潟4R のページに「騎手」が無かった）。異常にはしない"""
        res = rp.parse_top3data(top3_page(top3_table("能力", [("6", "3", "ロード")])), ref())
        st = {t["title"]: t["state"] for t in res["tables"]}
        self.assertEqual(st, {"能力": "馬あり", "上昇度": "欄がない", "騎手": "欄がない", "距離": "欄がない"})
        self.assertEqual(res["issues"], [])


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
                # 9/12 中山3R は公開されていない
                if rid == "202606040303":
                    want = [f"データ分析: 公開されていない（ページの案内「{rp.DATA_TOP_NOT_PUBLISHED}」）"]
                else:
                    want = []
                self.assertEqual(sh["top3_issues"] + dt["issues"], want)
                self.assertEqual(sh["map_issues"], [])

    def test_other_race_page_is_rejected(self):
        """出馬表のページのレースIDが取ったレースと違えば map_issues にする（採点しない）"""
        class SP:
            def __init__(self, h):
                self.h = h

            def goto(self, *a, **k):
                return None

            def content(self):
                return self.h
        h = gzip.open(PAGES / "202603030104_shutuba.html.gz", "rt", encoding="utf-8").read()
        sh = rp.scrape_shutuba(SP(h), "202603030107")
        self.assertTrue(any("レースIDが違う" in x for x in sh["map_issues"]))


class SavePageTest(unittest.TestCase):
    """採点に使ったページの保存（実行IDごと・ハッシュ・失敗の記録。2026-11-07 外部レビュー）"""

    def test_saved_per_run_with_hash(self):
        import hashlib
        import tempfile
        import run_pickup_all as rpa
        with tempfile.TemporaryDirectory() as t:
            out = Path(t)
            a = rpa.save_page(out, "run1", RID, "data_top", "<html>一</html>")
            b = rpa.save_page(out, "run2", RID, "data_top", "<html>二</html>")
            self.assertEqual(a["path"], f"pages/run1/{RID}_data_top.html.gz")
            self.assertNotEqual(a["path"], b["path"])   # 取り直しても前の実行のページは残る
            for r, h in ((a, "<html>一</html>"), (b, "<html>二</html>")):
                got = gzip.open(out / r["path"], "rt", encoding="utf-8").read()
                self.assertEqual(got, h)
                self.assertEqual(r["sha256"], hashlib.sha256(h.encode("utf-8")).hexdigest())

    def test_failure_is_recorded(self):
        import tempfile
        import run_pickup_all as rpa
        with tempfile.TemporaryDirectory() as t:
            blocker = Path(t) / "pages"
            blocker.write_text("x")   # pages がファイルなので、フォルダを作れない
            r = rpa.save_page(Path(t), "run1", RID, "shutuba", "<html></html>")
            self.assertIn("error", r)
            self.assertNotIn("sha256", r)
            self.assertEqual(rpa.save_page(Path(t), "run1", RID, "shutuba", "")["error"], "ページの中身が無い")


class RerunVersionTest(unittest.TestCase):
    """版が違う pickup_scores.json への部分再実行を止める（2026-11-07 外部レビュー）"""

    def test_mismatch(self):
        from rerun_failed_pickup import version_mismatch
        self.assertEqual(version_mismatch({"scoring_version": rp.SCORING_VERSION,
                                           "collector_version": rp.COLLECTOR_VERSION}), [])
        self.assertEqual(len(version_mismatch({"scoring_version": "v5.1",
                                               "collector_version": rp.COLLECTOR_VERSION})), 1)
        self.assertEqual(len(version_mismatch({})), 2)

    def test_main_refuses_and_leaves_file(self):
        import tempfile
        from unittest import mock
        import rerun_failed_pickup as rfp
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "output" / "20261107"
            out.mkdir(parents=True)
            body = json.dumps({"date": "20261107", "scoring_version": "v5.1",
                               "collector_version": rp.COLLECTOR_VERSION,
                               "races": {"東京1R": {"error": "x"}}}, ensure_ascii=False)
            (out / "pickup_scores.json").write_text(body, encoding="utf-8")
            with mock.patch.object(rfp, "BASE_DIR", Path(t)), mock.patch.object(sys, "argv", ["x", "20261107"]), \
                    mock.patch.object(rfp, "sync_playwright") as pw:
                with self.assertRaises(SystemExit) as cm:
                    rfp.main()
            self.assertEqual(cm.exception.code, 1)
            pw.assert_not_called()
            self.assertEqual((out / "pickup_scores.json").read_text(encoding="utf-8"), body)


@unittest.skipUnless(PAGES.exists(), "手元に保存ページが無い")
class ScoreRaceFlowTest(unittest.TestCase):
    """保存ページで run_pickup_all.score_race を通し、ページの保存・quality・品質の判定まで確かめる
    （前走データの取得と待ち時間は差し替える。指数の表は渡さないので、指数の照合の理由も出る）"""

    def run_race(self, t, rid="202603030104", break_save=False):
        from unittest import mock
        import run_pickup_all as rpa
        import prev_inputs as pi
        from data_quality import race_quality_issues

        class Page:
            def __init__(self):
                self.url = ""

            def goto(self, url, **k):
                self.url = url

            def content(self):
                kind = "data_top" if "data_top" in self.url else "shutuba"
                return gzip.open(PAGES / f"{rid}_{kind}.html.gz", "rt", encoding="utf-8").read()

            def close(self):
                pass

        out = Path(t) / "output" / "20261107"
        out.mkdir(parents=True)
        if break_save:
            (out / "pages").write_text("x")
        lock = pi.DateLock(out)
        rec = pi.RunRecord(out, "20261107", "test", rp.SCORING_VERSION, lock)

        def fake_fetch(page, hid, date, browse, settle, horse_name=None):
            return {"status": pi.NO_PREV, "reason": "", "values": None, "target_date": "20261107",
                    "fetched_at": "2026-11-06T17:30:00+09:00", "fetch_version": pi.PREV_FETCH_VERSION,
                    "source": "取得", "horse_id": hid}
        src = {"index_reader_version": "r", "candidate_rule_version": "c",
               "races": {"福島4R": {"modes": {"average": "type=rank"}}}}
        with mock.patch.object(rpa, "BASE_DIR", Path(t)), mock.patch.object(rpa, "fetch_entry", fake_fetch), \
                mock.patch.object(rpa, "human_sleep"), mock.patch.object(rp.time, "sleep"), \
                mock.patch.object(rp.random, "random", return_value=0.99):
            res, _, blocked = rpa.score_race(Page(), None, "20261107", "福島4R", rid, "福島", "4R",
                                             [{"馬番": 2, "馬名": "x"}], src, {}, rec, {}, {})
        rec.finish()
        lock.release()
        return res, race_quality_issues(res), out

    def test_flow(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            res, issues, out = self.run_race(t)
            q = res["quality"]
            self.assertEqual(q["data_top_reader"], rp.DATA_TOP_READER_VERSION)
            self.assertEqual(set(q["pages"]), {"shutuba", "data_top"})
            for v in q["pages"].values():
                self.assertTrue((out / v["path"]).exists())
                self.assertIn(res["prev_inputs_run"], v["path"])
            self.assertEqual(res["data_top_page_state"], "公開")
            self.assertEqual(res["pickup_nums"], sorted(res["pickup_nums"], key=int))
            self.assertEqual(q["data_top_issues"], [])
            self.assertFalse(any(x.startswith("3項目") or "ページ" in x for x in issues))
            # 指数の表を渡していないので、その理由だけが出る
            self.assertTrue(issues and all(x.startswith("指数の表が出馬表とそろわない") for x in issues))

    def test_save_failure_makes_race_bad(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            res, issues, out = self.run_race(t, break_save=True)
            self.assertIn("error", res["quality"]["pages"]["shutuba"])
            self.assertTrue(any(x.startswith("採点に使ったページを保存できなかった") for x in issues))
            self.assertIn("scored", res)   # 診断用の採点は続ける


if __name__ == "__main__":
    unittest.main()
