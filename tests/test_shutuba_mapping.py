"""
出馬表パース（race_pickup.parse_shutuba_rows）の回帰テスト。

2026-10-30 発覚: 行内の最初の数字セル（td.Waku* = 枠番）を馬番として使い、
horse_id/オッズ/人気が別馬に付いていた（docs/incident_umaban_20261030.md）。
頭数が揃っていても対応がずれるケースを必ず検査する。

実行: python3 -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from race_pickup import parse_shutuba_rows  # noqa: E402


def gate_of(num: int, n: int) -> int:
    """JRAの枠割り（n頭立て・馬番num → 枠番）"""
    if n <= 8:
        return num
    doubles = n - 8  # 2頭以上入る枠の数（外枠から）
    singles = 8 - doubles
    if n > 16:
        # 17/18頭: 7枠・8枠（18頭は6枠も）が3頭
        triples = n - 16
        sizes = [2] * 8
        for g in range(8 - triples, 8):
            sizes[g] = 3
    else:
        sizes = [1] * singles + [2] * doubles
    acc = 0
    for g, size in enumerate(sizes, start=1):
        acc += size
        if num <= acc:
            return g
    raise ValueError


def row_html(num, n, hid=None, odds="10.0", pop=None, name=None, cancel=False,
             umaban_cell=True):
    """netkeiba shutuba.html の行構造（2026-10-30 実ページで確認）を模したHTML"""
    g = gate_of(num, n)
    hid = hid if hid is not None else f"20200{num:05d}"
    name = name or f"テストホース{num}"
    pop = pop if pop is not None else str(num)
    link = f'<a href="https://db.netkeiba.com/horse/{hid}">{name}</a>' if hid else name
    umaban = f'<td class="Umaban{g} Txt_C">{num}</td>' if umaban_cell else ""
    cls = "HorseList Cancel" if cancel else "HorseList"
    odds_txt = "取消" if cancel else odds
    return (f'<tr class="{cls}" id="tr_{num}">'
            f'<td class="Waku{g} Txt_C">{g}</td>{umaban}'
            f'<td class="CheckMark Horse_Select">--◎◯▲△☆消</td>'
            f'<td class="HorseInfo"><span class="HorseName">{link}</span></td>'
            f'<td class="Barei Txt_C">牡4</td><td class="Txt_C">57.0</td>'
            f'<td class="Jockey">騎手</td><td class="Trainer">栗東 調教</td>'
            f'<td class="Weight">480 (+2)</td>'
            f'<td class="Txt_R Popular">{odds_txt}</td>'
            f'<td class="Popular Popular_Ninki Txt_C">{"" if cancel else pop}</td></tr>')


def table(rows):
    return BeautifulSoup(f'<table class="Shutuba_Table"><tbody>{"".join(rows)}</tbody></table>',
                         "html.parser")


def full_field(n, overrides=None):
    return table([row_html(i, n, odds=f"{i}.{i % 10}", **(overrides or {}).get(i, {}))
                  for i in range(1, n + 1)])


class ShutubaMappingTest(unittest.TestCase):
    def test_every_row_maps_to_its_own_umaban(self):
        for n in [5, 8, 9, 12, 15, 16, 18]:
            with self.subTest(n=n):
                r = parse_shutuba_rows(full_field(n), expected_nums={str(i) for i in range(1, n + 1)})
                assert r["map_issues"] == []
                assert sorted(r["horse_id_map"], key=int) == [str(i) for i in range(1, n + 1)]
                for i in range(1, n + 1):
                    k = str(i)
                    # 馬番・馬ID・名前・オッズ・人気がすべて同じ行由来
                    assert r["horse_id_map"][k] == f"20200{i:05d}"
                    assert r["horse_map"][k] == f"テストホース{i}"
                    assert r["odds_map"][k] == float(f"{i}.{i % 10}")
                    assert r["pop_map"][k] == k
                    assert r["odds_status"][k] == "ok"

    def test_gate_number_never_used_as_umaban(self):
        """15頭: 枠番キー（旧バグ）なら 3→馬番4 の馬IDになる。頭数一致でも対応ずれを検出"""
        r = parse_shutuba_rows(full_field(15))
        assert r["horse_id_map"]["3"] == "2020000003"
        assert r["horse_id_map"]["14"] == "2020000014"
        assert len(r["horse_id_map"]) == 15

    def test_small_field_gate_equals_umaban(self):
        r = parse_shutuba_rows(full_field(7))
        assert r["map_issues"] == []
        assert len(r["horse_id_map"]) == 7

    def test_scratched_horse_is_kept_and_marked(self):
        r = parse_shutuba_rows(full_field(12, {5: {"cancel": True}}))
        assert r["map_issues"] == []
        assert r["horse_id_map"]["5"] == "2020000005"
        assert r["odds_status"]["5"] == "scratched"
        assert "5" not in r["odds_map"] and "5" not in r["pop_map"]

    def test_unsold_odds_distinguished_from_missing_id(self):
        r = parse_shutuba_rows(table([row_html(i, 10, odds="---.-", pop="") for i in range(1, 11)]))
        assert r["map_issues"] == []          # 未発売は対応の異常ではない
        assert len(r["horse_id_map"]) == 10
        assert r["odds_map"] == {} and r["pop_map"] == {}
        assert set(r["odds_status"].values()) == {"unsold"}

    def test_missing_horse_id_is_an_issue(self):
        r = parse_shutuba_rows(full_field(10, {7: {"hid": ""}}))
        assert "missing_horse_id:7" in r["map_issues"]

    def test_missing_umaban_cell_is_an_issue_not_fallback(self):
        r = parse_shutuba_rows(full_field(10, {4: {"umaban_cell": False}}))
        assert "row_without_umaban" in r["map_issues"]
        assert "umaban_not_contiguous:4" in r["map_issues"]
        # 枠番（4番は3枠）へのフォールバックで別キーに入っていないこと
        assert "4" not in r["horse_id_map"]
        assert r["horse_id_map"]["3"] == "2020000003"

    def test_partial_table_detected(self):
        rows = [row_html(i, 14, odds="5.0") for i in range(1, 15) if i not in (11, 12, 13, 14)]
        r = parse_shutuba_rows(table(rows), expected_nums={str(i) for i in range(1, 15)})
        assert any(x.startswith("missing_vs_horseicon:11,12,13,14") for x in r["map_issues"])

    def test_duplicate_umaban_and_horse_id_detected(self):
        rows = [row_html(i, 9) for i in range(1, 10)] + [row_html(9, 9, hid="2020000001")]
        r = parse_shutuba_rows(table(rows))
        assert "dup_umaban:9" in r["map_issues"]
        rows = [row_html(i, 9) for i in range(1, 9)] + [row_html(9, 9, hid="2020000002")]
        r = parse_shutuba_rows(table(rows))
        assert "dup_horse_id:2,9" in r["map_issues"]

    def test_empty_table_is_an_issue(self):
        assert "no_rows" in parse_shutuba_rows(table([]))["map_issues"]


if __name__ == "__main__":
    unittest.main()
