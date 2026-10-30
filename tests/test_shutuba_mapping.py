"""
出馬表パース（race_pickup.parse_shutuba_rows）と採点入口の回帰テスト。

2026-10-30 発覚: 行内の最初の数字セル（td.Waku* = 枠番）を馬番として使い、
horse_id/オッズ/人気が別馬に付いていた（docs/incident_umaban_20261030.md）。
頭数が揃っていても対応がずれるケースを必ず検査する。

行の構造は 2026-10-30 に実ページで確認したもの:
- 通常行: td.Waku{枠} → td.Umaban{枠}（中身が馬番）→ td.CheckMark → td.HorseInfo … td.Popular / td.Popular_Ninki
- 取消行（race_id=202508020701 の2番）: tr.HorseList.Cancel、td.Cancel_Txt「取消」、オッズ・人気セルは td.Cancel_NoData「--」
- ページ上部 .RaceData02 の「N頭」は取消を含む登録頭数で、表の行数と一致

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from race_pickup import (  # noqa: E402
    ShutubaMapError, active_horse_ids, parse_field_size, parse_shutuba_rows, score_horses,
)


def gate_of(num: int, n: int) -> int:
    """JRAの枠割り（n頭立て・馬番num → 枠番）"""
    if n <= 8:
        return num
    if n > 16:
        sizes = [2] * 8
        for g in range(8 - (n - 16), 8):  # 17頭: 8枠3頭 / 18頭: 7・8枠3頭
            sizes[g] = 3
    else:
        sizes = [1] * (16 - n) + [2] * (n - 8)
    acc = 0
    for g, size in enumerate(sizes, start=1):
        acc += size
        if num <= acc:
            return g
    raise ValueError


def row_html(num, n, hid=None, odds="10.0", pop=None, name=None, cancel=None,
             umaban_cell=True):
    """cancel: None / "取消" / "除外"（実ページの取消行の構造）"""
    g = gate_of(num, n)
    hid = hid if hid is not None else f"20200{num:05d}"
    name = name or f"テストホース{num}"
    pop = pop if pop is not None else str(num)
    link = f'<a href="https://db.netkeiba.com/horse/{hid}">{name}</a>' if hid else name
    umaban = f'<td class="Umaban{g} Txt_C">{num}</td>' if umaban_cell else ""
    if cancel:
        return (f'<tr class="HorseList Cancel" id="tr_{num}">'
                f'<td class="Waku{g} Txt_C">{g}</td>{umaban}'
                f'<td class="Cancel_Txt">{cancel}</td>'
                f'<td class="HorseInfo"><span class="HorseName">{link}</span></td>'
                f'<td>牡3</td><td class="Cancel_NoData Txt_C">57.0</td>'
                f'<td class="Cancel_NoData Jockey">騎手</td><td class="Trainer Cancel_NoData">栗東調教</td>'
                f'<td class="Cancel_NoData Txt_L">--</td><td class="Cancel_NoData Txt_R">--</td>'
                f'<td class="Cancel_NoData Txt_C">--</td></tr>')
    return (f'<tr class="HorseList" id="tr_{num}">'
            f'<td class="Waku{g} Txt_C">{g}</td>{umaban}'
            f'<td class="CheckMark Horse_Select">--◎◯▲△☆消</td>'
            f'<td class="HorseInfo"><span class="HorseName">{link}</span></td>'
            f'<td class="Barei Txt_C">牡4</td><td class="Txt_C">57.0</td>'
            f'<td class="Jockey">騎手</td><td class="Trainer">栗東 調教</td>'
            f'<td class="Weight">480 (+2)</td>'
            f'<td class="Txt_R Popular">{odds}</td>'
            f'<td class="Popular Popular_Ninki Txt_C">{pop}</td></tr>')


def page(rows, field_size):
    header = (f'<div class="RaceData02"><span>4回</span><span>京都</span>'
              f'<span>{field_size}頭</span></div>') if field_size is not None else ""
    return BeautifulSoup(f'{header}<table class="Shutuba_Table"><tbody>{"".join(rows)}</tbody></table>',
                         "html.parser")


def full_field(n, overrides=None):
    return page([row_html(i, n, odds=f"{i}.{i % 10}", **(overrides or {}).get(i, {}))
                 for i in range(1, n + 1)], n)


def parse(soup, expected_nums=frozenset()):
    return parse_shutuba_rows(soup, field_size=parse_field_size(soup), expected_nums=expected_nums)


def shutuba_data(parsed):
    return {**parsed, "position_nums": set(), "top3_hits": {}}


class ShutubaMappingTest(unittest.TestCase):
    def test_every_row_maps_to_its_own_umaban(self):
        for n in [5, 8, 9, 12, 15, 16, 17, 18]:
            with self.subTest(n=n):
                r = parse(full_field(n), expected_nums={str(i) for i in range(1, n + 1)})
                self.assertEqual(r["map_issues"], [])
                self.assertEqual(sorted(r["horse_id_map"], key=int), [str(i) for i in range(1, n + 1)])
                for i in range(1, n + 1):
                    k = str(i)
                    # 馬番・馬ID・名前・オッズ・人気がすべて同じ行由来
                    self.assertEqual(r["horse_id_map"][k], f"20200{i:05d}")
                    self.assertEqual(r["horse_map"][k], f"テストホース{i}")
                    self.assertEqual(r["odds_map"][k], float(f"{i}.{i % 10}"))
                    self.assertEqual(r["pop_map"][k], k)
                    self.assertEqual(r["odds_status"][k], "ok")

    def test_gate_number_never_used_as_umaban(self):
        """15頭: 枠番キー（旧バグ）なら 3→馬番4 の馬IDになる。頭数一致でも対応ずれを検出"""
        r = parse(full_field(15))
        self.assertEqual(r["horse_id_map"]["3"], "2020000003")
        self.assertEqual(r["horse_id_map"]["14"], "2020000014")
        self.assertEqual(len(r["horse_id_map"]), 15)

    def test_small_field_gate_equals_umaban(self):
        r = parse(full_field(7))
        self.assertEqual(r["map_issues"], [])
        self.assertEqual(len(r["horse_id_map"]), 7)

    def test_scratched_horse_real_structure(self):
        """実ページの取消行: 対応表には残し、取消として記録、オッズ・人気は入れない"""
        for word in ("取消", "除外"):
            with self.subTest(word=word):
                r = parse(full_field(11, {2: {"cancel": word}}))
                self.assertEqual(r["map_issues"], [])
                self.assertEqual(r["horse_id_map"]["2"], "2020000002")
                self.assertEqual(r["scratched_nums"], {"2": word})
                self.assertEqual(r["odds_status"]["2"], "scratched")
                self.assertNotIn("2", r["odds_map"])
                self.assertNotIn("2", r["pop_map"])
                self.assertNotIn("2", active_horse_ids(r))
                self.assertEqual(len(active_horse_ids(r)), 10)

    def test_word_in_name_is_not_scratch(self):
        """行内の文字列ではなく取消セルで判定する（馬名に「除外」等が含まれても取消扱いしない）"""
        r = parse(full_field(9, {4: {"name": "ジョガイノホシ除外"}}))
        self.assertEqual(r["scratched_nums"], {})

    def test_unsold_odds_distinguished_from_missing_id(self):
        r = parse(page([row_html(i, 10, odds="---.-", pop="") for i in range(1, 11)], 10))
        self.assertEqual(r["map_issues"], [])  # 未発売は対応の異常ではない
        self.assertEqual(len(r["horse_id_map"]), 10)
        self.assertEqual(r["odds_map"], {})
        self.assertEqual(r["pop_map"], {})
        self.assertEqual(set(r["odds_status"].values()), {"unsold"})

    def test_missing_horse_id_is_an_issue(self):
        r = parse(full_field(10, {7: {"hid": ""}}))
        self.assertIn("missing_horse_id:7", r["map_issues"])

    def test_missing_umaban_cell_is_an_issue_not_fallback(self):
        r = parse(full_field(10, {4: {"umaban_cell": False}}))
        self.assertIn("row_without_umaban", r["map_issues"])
        self.assertIn("umaban_not_contiguous:4", r["map_issues"])
        # 枠番（4番は3枠）へのフォールバックで別キーに入っていないこと
        self.assertNotIn("4", r["horse_id_map"])
        self.assertEqual(r["horse_id_map"]["3"], "2020000003")

    def test_tail_rows_missing_detected_by_field_size(self):
        """末尾欠落（1〜8番だけ残る）は連番検査では見えない → 頭数表示との照合で検出"""
        r = parse(page([row_html(i, 14) for i in range(1, 9)], 14))
        self.assertIn("row_count_mismatch:8/14", r["map_issues"])

    def test_field_size_unknown_is_an_issue(self):
        r = parse(page([row_html(i, 8) for i in range(1, 9)], None))
        self.assertIn("field_size_unknown", r["map_issues"])

    def test_missing_vs_horseicon_detected(self):
        rows = [row_html(i, 14, odds="5.0") for i in range(1, 15) if i not in (11, 12, 13, 14)]
        r = parse(page(rows, 14), expected_nums={str(i) for i in range(1, 15)})
        self.assertIn("missing_vs_horseicon:11,12,13,14", r["map_issues"])

    def test_duplicate_umaban_and_horse_id_detected(self):
        rows = [row_html(i, 9) for i in range(1, 10)] + [row_html(9, 9, hid="2020000001")]
        self.assertIn("dup_umaban:9", parse(page(rows, 9))["map_issues"])
        rows = [row_html(i, 9) for i in range(1, 9)] + [row_html(9, 9, hid="2020000002")]
        self.assertIn("dup_horse_id:2,9", parse(page(rows, 9))["map_issues"])

    def test_empty_table_is_an_issue(self):
        self.assertIn("no_rows", parse(page([], 0))["map_issues"])


class ScoringGateTest(unittest.TestCase):
    """採点の共通入口（score_horses）で異常データを拒否する。全実行経路がここを通る"""

    def test_rejects_invalid_map(self):
        bad = shutuba_data(parse(full_field(10, {7: {"hid": ""}})))
        with self.assertRaises(ShutubaMapError):
            score_horses([{"馬番": 1, "馬名": "テストホース1"}], bad,
                         {"pickup_nums": set(), "analysis_hits": {}})

    def test_scratched_horse_not_scored(self):
        ok = shutuba_data(parse(full_field(11, {2: {"cancel": "取消"}})))
        scored = score_horses([{"馬番": 1, "馬名": "テストホース1"}, {"馬番": 2, "馬名": "テストホース2"}],
                              ok, {"pickup_nums": set(), "analysis_hits": {}})
        self.assertEqual([h["馬番"] for h in scored], [1])

    def test_old_saved_data_without_issues_still_scores(self):
        """rescore.py が使う保存データ（map_issues キーなし）は従来どおり採点できる"""
        saved = {"position_nums": set(), "top3_hits": {}, "horse_id_map": {}, "pop_map": {}, "odds_map": {}}
        scored = score_horses([{"馬番": 3, "馬名": "x"}], saved, {"pickup_nums": set(), "analysis_hits": {}})
        self.assertEqual(len(scored), 1)


if __name__ == "__main__":
    unittest.main()
