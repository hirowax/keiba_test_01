#!/usr/bin/env python3
"""
血統プランA: 血統辞典条件（gushiken_umapro note記事）の過去データ照合
CLAUDE.md「血統辞典検証」節・プランA参照

父(sire) または 母父(dam_sire) が競馬場×距離×芝/ダごとの推奨血統に該当する馬を
「血統推奨馬」として抽出し、race_results.jsonの着順・オッズと突き合わせて
単勝回収率・複勝回収率・3着内率を集計する。

出典URL・条件一覧はCLAUDE.mdに転記済み（このスクリプトのDICTと同一内容）。

usage: python3 analyze_pedigree_dictionary.py
"""
import json
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

# (距離, 芝/ダ, [推奨父系], [推奨母父系])
DICT = {
    "札幌": [
        (1200, "芝", ["ロードカナロア", "キズナ"], ["ディープインパクト", "ハーツクライ"]),
        (1500, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (1800, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト", "ハーツクライ"]),
        (2000, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2600, "芝", ["キズナ"], ["ディープインパクト"]),
        (1000, "ダート", ["ヘニーヒューズ", "サウスヴィグラス"], ["キングカメハメハ"]),
        (1700, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ", "ゴールドアリュール"]),
    ],
    "函館": [
        (1200, "芝", ["ロードカナロア", "ビッグアーサー"], ["ディープインパクト"]),
        (1800, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2000, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2600, "芝", ["キズナ"], ["ディープインパクト"]),
        (1000, "ダート", ["ヘニーヒューズ", "サウスヴィグラス"], ["キングカメハメハ"]),
        (1700, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
    ],
    "福島": [
        (1200, "芝", ["ロードカナロア", "ダイワメジャー"], ["ディープインパクト"]),
        (1800, "芝", ["エピファネイア", "キズナ"], ["ディープインパクト", "ハーツクライ"]),
        (2000, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (1150, "ダート", ["ヘニーヒューズ", "サウスヴィグラス"], ["キングカメハメハ"]),
        (1700, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
    ],
    "新潟": [
        (1000, "芝", ["ロードカナロア"], ["ディープインパクト"]),
        (1200, "芝", ["ロードカナロア", "ビッグアーサー"], ["ディープインパクト"]),
        (1400, "芝", ["ロードカナロア", "キズナ"], ["ディープインパクト"]),
        (1600, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト", "ハーツクライ"]),
        (1800, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2000, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2200, "芝", ["キズナ"], ["ディープインパクト"]),
        (1200, "ダート", ["ヘニーヒューズ", "サウスヴィグラス"], ["キングカメハメハ"]),
        (1800, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
    ],
    "東京": [
        (1400, "芝", ["ロードカナロア", "ダイワメジャー"], ["ディープインパクト"]),
        (1600, "芝", ["エピファネイア", "キズナ"], ["ディープインパクト", "ハーツクライ"]),
        (1800, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2000, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2400, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト", "ハーツクライ"]),
        (2500, "芝", ["キズナ"], ["ディープインパクト"]),
        (1300, "ダート", ["ヘニーヒューズ", "サウスヴィグラス"], ["キングカメハメハ"]),
        (1400, "ダート", ["ヘニーヒューズ"], ["キングカメハメハ"]),
        (1600, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
        (2100, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ", "ゴールドアリュール"]),
    ],
    "中山": [
        (1200, "芝", ["ロードカナロア", "ダイワメジャー"], ["ディープインパクト"]),
        (1600, "芝", ["エピファネイア", "キズナ"], ["ディープインパクト", "ハーツクライ"]),
        (1800, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2000, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2200, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2500, "芝", ["キズナ"], ["ディープインパクト"]),
        (1200, "ダート", ["ヘニーヒューズ", "サウスヴィグラス"], ["キングカメハメハ"]),
        (1800, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
    ],
    "京都": [
        (1200, "芝", ["ロードカナロア", "ビッグアーサー"], ["ディープインパクト"]),
        (1400, "芝", ["ロードカナロア", "キズナ"], ["ディープインパクト"]),
        (1600, "芝", ["エピファネイア", "キズナ"], ["ディープインパクト", "ハーツクライ"]),
        (1800, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2000, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2200, "芝", ["キズナ"], ["ディープインパクト"]),
        (3000, "芝", ["キズナ"], ["ディープインパクト"]),
        (3200, "芝", ["キズナ"], ["ディープインパクト"]),
        (1400, "ダート", ["ヘニーヒューズ", "サウスヴィグラス"], ["キングカメハメハ"]),
        (1800, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
        (1900, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
    ],
    "阪神": [
        (1200, "芝", ["ロードカナロア", "ダイワメジャー"], ["ディープインパクト"]),
        (1400, "芝", ["ロードカナロア", "キズナ"], ["ディープインパクト"]),
        (1600, "芝", ["エピファネイア", "キズナ"], ["ディープインパクト", "ハーツクライ"]),
        (1800, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2000, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2200, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2400, "芝", ["キズナ"], ["ディープインパクト"]),
        (1200, "ダート", ["ヘニーヒューズ", "サウスヴィグラス"], ["キングカメハメハ"]),
        (1400, "ダート", ["ヘニーヒューズ"], ["キングカメハメハ"]),
        (1800, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
        (2000, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ", "ゴールドアリュール"]),
    ],
    "中京": [
        (1200, "芝", ["ロードカナロア", "ビッグアーサー"], ["ディープインパクト"]),
        (1400, "芝", ["ロードカナロア", "キズナ"], ["ディープインパクト"]),
        (1600, "芝", ["エピファネイア", "キズナ"], ["ディープインパクト", "ハーツクライ"]),
        (2000, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (1400, "ダート", ["ヘニーヒューズ", "サウスヴィグラス"], ["キングカメハメハ"]),
        (1800, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
        (1900, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
    ],
    "小倉": [
        (1200, "芝", ["ロードカナロア", "ダイワメジャー"], ["ディープインパクト"]),
        (1800, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2000, "芝", ["キズナ", "エピファネイア"], ["ディープインパクト"]),
        (2600, "芝", ["キズナ"], ["ディープインパクト"]),
        (1000, "ダート", ["ヘニーヒューズ", "サウスヴィグラス"], ["キングカメハメハ"]),
        (1700, "ダート", ["ヘニーヒューズ", "パイロ"], ["キングカメハメハ"]),
    ],
}

VENUES = list(DICT.keys())
BET_UNIT = 100


def venue_of(label: str):
    for v in VENUES:
        if label.startswith(v):
            return v
    return None


def lookup(venue: str, distance: int, surface: str):
    if venue not in DICT or distance is None or surface not in ("芝", "ダート"):
        return None
    for d, s, sires, damsires in DICT[venue]:
        if d == distance and s == surface:
            return sires, damsires
    return None


def collect():
    recs = []  # 血統推奨馬の (date, label, venue, distance, surface, name, rank, odds, place_odds, matched_by)
    n_races_covered = 0
    n_horses_seen = 0
    n_horses_unknown_pedigree = 0

    dates = sorted(p.name for p in OUTPUT_DIR.iterdir() if p.is_dir() and p.name.isdigit())
    for date in dates:
        cond_path = OUTPUT_DIR / date / "race_conditions.json"
        res_path = OUTPUT_DIR / date / "race_results.json"
        if not cond_path.exists() or not res_path.exists():
            continue
        cond = json.load(open(cond_path, encoding="utf-8"))
        res = json.load(open(res_path, encoding="utf-8"))

        for label, c in cond.items():
            venue = venue_of(label)
            if venue is None:
                continue
            entry = lookup(venue, c.get("distance"), c.get("surface"))
            if entry is None:
                continue
            n_races_covered += 1
            sires, damsires = entry
            for h in res.get(label, []):
                n_horses_seen += 1
                sire = h.get("sire") or ""
                dam_sire = h.get("dam_sire") or ""
                if not sire and not dam_sire:
                    n_horses_unknown_pedigree += 1
                    continue
                matched_by = []
                if sire in sires:
                    matched_by.append(f"父:{sire}")
                if dam_sire in damsires:
                    matched_by.append(f"母父:{dam_sire}")
                if not matched_by:
                    continue
                recs.append({
                    "date": date, "label": label, "venue": venue,
                    "distance": c.get("distance"), "surface": c.get("surface"),
                    "name": h.get("name"), "rank": h.get("rank"),
                    "odds": h.get("odds"), "place_odds": h.get("place_odds"),
                    "matched_by": "+".join(matched_by),
                })

    return recs, {
        "n_dates": len(dates), "n_races_covered": n_races_covered,
        "n_horses_seen": n_horses_seen, "n_horses_unknown_pedigree": n_horses_unknown_pedigree,
    }


def summarize(recs, label):
    n = len(recs)
    if n == 0:
        return None
    n_place = sum(1 for r in recs if r["rank"] <= 3)
    n_win = sum(1 for r in recs if r["rank"] == 1)
    tan_ret = sum(BET_UNIT * r["odds"] for r in recs if r["rank"] == 1)
    # 複勝回収率: place_odds欠損の的中馬は集計から除外し、欠損数を別途報告
    fuku_recs = [r for r in recs if r["rank"] <= 3]
    fuku_missing = sum(1 for r in fuku_recs if r["place_odds"] is None)
    fuku_ret = sum(BET_UNIT * r["place_odds"] for r in fuku_recs if r["place_odds"] is not None)
    return {
        "label": label, "n": n,
        "win_rate": n_win / n * 100, "place_rate": n_place / n * 100,
        "tansho_roi": tan_ret / (n * BET_UNIT) * 100,
        "fukusho_roi": fuku_ret / (n * BET_UNIT) * 100,
        "fukusho_missing": fuku_missing,
    }


def print_row(s):
    if s is None:
        return
    suffix = f"  (複勝欠損{s['fukusho_missing']})" if s["fukusho_missing"] else ""
    print(f"  {s['label']:<20} N={s['n']:>5}  勝率{s['win_rate']:>5.1f}%  3着内率{s['place_rate']:>5.1f}%"
          f"  単勝回収率{s['tansho_roi']:>6.1f}%  複勝回収率{s['fukusho_roi']:>6.1f}%{suffix}")


def main():
    recs, meta = collect()
    print("=" * 70)
    print("血統プランA: 血統辞典条件の過去データ照合（中間集計・パイロット版）")
    print("=" * 70)
    print(f"対象日数: {meta['n_dates']}  条件一致レース: {meta['n_races_covered']}")
    print(f"延べ出走頭数: {meta['n_horses_seen']}  うち血統未取得(父母父とも空): {meta['n_horses_unknown_pedigree']}")
    print("⚠️  血統キャッシュが全体の約48%のみのパイロット集計。未取得馬は判定不能として除外しており、")
    print("   キャッシュがどの馬から埋まったかに依存する選択バイアスの可能性がある。参考値として扱うこと。")

    print("\n【全体】")
    print_row(summarize(recs, "血統推奨馬 全体"))

    print("\n【競馬場別】")
    by_venue = defaultdict(list)
    for r in recs:
        by_venue[r["venue"]].append(r)
    for v in VENUES:
        print_row(summarize(by_venue[v], v))

    print("\n【芝/ダート別】")
    for s in ("芝", "ダート"):
        print_row(summarize([r for r in recs if r["surface"] == s], s))

    print("\n【距離帯別】")
    bins = [(0, 1300, "〜1300m"), (1301, 1800, "1301〜1800m"), (1801, 9999, "1801m〜")]
    for lo, hi, name in bins:
        print_row(summarize([r for r in recs if lo <= (r["distance"] or 0) <= hi], name))

    print("\n【マッチ根拠別（父系のみ/母父系のみ/両方）】")
    both = [r for r in recs if "+" in r["matched_by"]]
    sire_only = [r for r in recs if "+" not in r["matched_by"] and r["matched_by"].startswith("父")]
    dam_only = [r for r in recs if "+" not in r["matched_by"] and r["matched_by"].startswith("母父")]
    print_row(summarize(sire_only, "父系のみ一致"))
    print_row(summarize(dam_only, "母父系のみ一致"))
    print_row(summarize(both, "父系・母父系とも一致"))


if __name__ == "__main__":
    main()
