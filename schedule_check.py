"""
その日の予定（開催場とレース）を確定できるかの判定（2026-11-12〜。docs/horse_style_design_202611.md 2章）。
脚質の作成（build_horse_style.py）と結果の取得（scrape_results.py）の両方がこの関数を使う（判定を1か所にまとめる）。

- 主の条件: netkeiba のレース一覧（{レース名: レースID}）と、JRA 公式の結果の一覧（output/jra_cname_cache.json の
  srl＝開催場・日、sde＝各レースの結果のページ）で、開催場の集合とレースIDの集合が両方とも一致すること。
- 補助の条件: 開催場ごとに 1R〜12R がそろっていること。そろわなければ、両方の一覧が一致していても、
  中止・変更を確かめられないので「予定を確定できない」（JRA 公式の一覧は結果のあるレースの一覧で、中止の記録ではない）。
- レース名とレースIDの対応（開催場のコード・レース番号）も確かめる。
"""
import re

JRA_CACHE_PATH = "output/jra_cname_cache.json"
VENUE_CODES = {"01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
               "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉"}
CONFIRMED = "確定"
UNCONFIRMED = "予定を確定できない"


def jra_lists(date: str, jra_cache: dict) -> tuple:
    """JRA 公式の一覧 (開催場コードの集合, レースIDの集合)"""
    venues = {k.split("_")[1] for k in (jra_cache.get("srl") or {}) if k.startswith(f"{date}_")}
    ids = {rid for rid, v in (jra_cache.get("sde") or {}).items() if (v or {}).get("date") == date}
    return venues, ids


def day_schedule(date: str, race_ids: dict, jra_cache: dict) -> dict:
    """{"status": 確定 | 予定を確定できない, "reason", "netkeiba_race_ids", "jra_race_ids", "per_venue_1to12"}"""
    nk_ids = set(race_ids.values())
    jra_venues, jra_ids = jra_lists(date, jra_cache)
    out = {"status": UNCONFIRMED, "reason": None, "netkeiba_race_ids": sorted(nk_ids),
           "jra_race_ids": sorted(jra_ids), "per_venue_1to12": {}}
    if not nk_ids:
        out["reason"] = "netkeiba のレース一覧が空"
        return out
    if len(nk_ids) != len(race_ids):
        out["reason"] = "netkeiba のレース一覧に同じレースIDが2回ある"
        return out
    for label, rid in race_ids.items():
        m = re.fullmatch(r"(\D+)(\d+)R", label)
        if not (m and re.fullmatch(r"\d{12}", str(rid)) and VENUE_CODES.get(rid[4:6]) == m.group(1)
                and int(rid[10:12]) == int(m.group(2))):
            out["reason"] = f"レース名とレースIDが合わない: {label} {rid}"
            return out
    if not jra_ids:
        out["reason"] = "JRA 公式の結果の一覧が無い"
        return out
    nk_venues = {rid[4:6] for rid in nk_ids}
    if nk_venues != jra_venues or {rid[4:6] for rid in jra_ids} != jra_venues:
        out["reason"] = (f"開催場が一致しない（netkeiba {sorted(VENUE_CODES[v] for v in nk_venues)}・"
                         f"JRA 公式 {sorted(VENUE_CODES.get(v, v) for v in jra_venues)}）")
        return out
    if nk_ids != jra_ids:
        out["reason"] = (f"レースIDが一致しない（netkeiba だけ {sorted(nk_ids - jra_ids)}・"
                         f"JRA 公式だけ {sorted(jra_ids - nk_ids)}）")
        return out
    bad = []
    for v in sorted(nk_venues):
        nums = sorted(int(rid[10:12]) for rid in nk_ids if rid[4:6] == v)
        ok = nums == list(range(1, 13))
        out["per_venue_1to12"][VENUE_CODES[v]] = ok
        if not ok:
            bad.append(f"{VENUE_CODES[v]} {nums}")
    if bad:
        out["reason"] = ("開催場のレースが 1R〜12R そろわない（両方の一覧は一致。中止・変更の記録は確かめていない）: "
                         + "・".join(bad))
        return out
    out["status"] = CONFIRMED
    return out
