"""
タスク8の試し（docs/rebuild_trial_plan_202611.md）: 保存したページ（fetch_pages.py）と保存した入力から、6レースの点数を作り直して並べる。
2026-11-07。ページは取りに行かない。成績・着順・払戻は見ない（結果のファイルからは馬番・馬ID・馬名・状態だけを読む）。

コード: 採点は今の race_pickup.score_horses（11/7 の本番のコミット 7b65ee7 から変わっていない。git diff で確認）。
前走の読み取りは、今の版（prev_inputs.parse_results_table。「除」の行を飛ばす）と、11/7 の本番の版（7b65ee7 の prev_inputs.py を
別の名前で読み込む）の両方を使う。出馬表・データ分析は、今の race_pickup.scrape_shutuba・scrape_data_top に、保存した HTML を
返すだけの代わりのページを渡して読む（書き写さない）。脚質は build_horse_style.build を、対象日より前の日の race_results.json だけを
置いた一時フォルダで呼ぶ（本番の horse_style.json と同じ入力の種類。今の horse_style.json は 2026-06-14 に作ったもの）。

対照（11/7 の2レース）は計画の 3.2 の順に1項目ずつ置き換える:
  S1 保存した入力・本番の版（reproduce_control.py と同じ） → (a) 前走を取り直したページから本番の版で読む → (b) 前走の読み取りを今の版に
  → (c) 脚質を対象日より前の結果で作り直す → (d) 3項目（上位3頭・ピックアップ・出走馬分析）を取り直したページの値に
ほかの4レース: 前走は今の版、脚質は作り直し、予測ペースは保存値で、3項目を (i) 保存値 (ii) 取り直した値 (iii) 入れない、の3通り。
  10/30 以前のレースは、馬番→馬IDを結果（race_results_all）から取り、結果に取消・除外がいれば「当時の採点を再現できない」とし、
  点数は取消・除外を外した参考の計算として出す（計画の 2.1）。

実行: python3 research/rebuild_trial_202611/rebuild_trial.py <7b65ee7 の worktree のフォルダ>
出力: このフォルダの rebuild_trial_result.json・rebuild_trial_result.txt
"""
import ast
import gzip
import hashlib
import importlib.util
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
WT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)

import pandas as pd  # noqa: E402
import build_horse_style  # noqa: E402
import prev_inputs as prev_new  # noqa: E402
import race_pickup  # noqa: E402
import scraper  # noqa: E402
from index_table_check import load_index_tables  # noqa: E402

spec = importlib.util.spec_from_file_location("prev_inputs_7b65ee7", WT / "prev_inputs.py")
prev_old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prev_old)
race_pickup.time.sleep = lambda *a, **k: None   # 代わりのページを読むだけなので待たない
PAGES = HERE / "pages"
FIXED_FROM = "20261031"
LOG = []


def out(*a):
    LOG.append(" ".join(str(x) for x in a))
    print(*a)


class SavedPage:
    """保存した HTML を返すだけの代わりのページ（goto は何もしない）"""
    def __init__(self, html):
        self.html = html

    def goto(self, *a, **k):
        return None

    def content(self):
        return self.html


def gz(path):
    return gzip.open(path, "rt", encoding="utf-8").read()


def parse_set(v):
    if v is None or v == "set()":
        return set()
    if isinstance(v, str):
        return {str(x) for x in ast.literal_eval(v)}
    return {str(x) for x in v}


def style_as_of(date):
    """対象日より前の日の race_results.json だけで build_horse_style.build を呼ぶ"""
    tmp = Path(tempfile.mkdtemp())
    try:
        for d in sorted(p.name for p in (ROOT / "output").iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8):
            src = ROOT / "output" / d / "race_results.json"
            if d < date and src.exists():
                (tmp / d).mkdir()
                shutil.copy(src, tmp / d / "race_results.json")
        old = (build_horse_style.OUTPUT_DIR, build_horse_style.STYLE_PATH)
        build_horse_style.OUTPUT_DIR, build_horse_style.STYLE_PATH = tmp, tmp / "horse_style.json"
        try:
            return build_horse_style.build()
        finally:
            build_horse_style.OUTPUT_DIR, build_horse_style.STYLE_PATH = old
    finally:
        shutil.rmtree(tmp)


def refetched_race(rid):
    sh = race_pickup.scrape_shutuba(SavedPage(gz(PAGES / "race" / f"{rid}_shutuba.html.gz")), rid)
    dt = race_pickup.scrape_data_top(SavedPage(gz(PAGES / "race" / f"{rid}_data_top.html.gz")), rid, sh["horse_map"])
    return sh, dt


def entries_from_pages(active, date, mod):
    ent = {}
    for num, hid in active.items():
        f = PAGES / "horse" / f"{hid}.html.gz"
        if not f.exists():
            ent[hid] = {"status": "取得失敗", "reason": "ページが無い", "values": {}}
            continue
        e = mod.parse_results_table(gz(f), date)
        e.update(horse_id=hid, target_date=date)
        ent[hid] = e
    return ent


def score(triple, shutuba, top3, pickup, analysis, entries, mod, style, date, pace, dist):
    sd = dict(shutuba, top3_hits=top3, map_issues=[])
    active = race_pickup.active_horse_ids(sd)
    top1 = mod.race_top1(active, entries)
    prev_db = {hid: v for hid, e in entries.items() if (v := mod.prev_db_entry(e)) is not None}
    sc = race_pickup.score_horses(triple, sd, {"pickup_nums": pickup, "analysis_hits": analysis}, prev_db=prev_db,
                                  race_max_prev_idx=top1["race_max"], race_date=date, horse_style_db=style,
                                  race_dist=dist, predicted_pace=pace)
    return {"scored": [{"num": h["馬番"], "score": h["score"], "breakdown": [f"{b['label']}({b['pts']})" for b in h["breakdown"]]}
                       for h in sc], "top1_race_max": top1["race_max"], "top1": {k: v for k, v in top1.items() if k != "race_max"}}


def brief(v):
    return [(x["num"], x["score"]) for x in v["scored"]]


def prev_view(entries, active):
    return {num: {"status": entries[h]["status"], "reason": entries[h].get("reason", ""),
                  "values": entries[h].get("values"), "skipped": len(entries[h].get("skipped_rows") or [])}
            for num, h in sorted(active.items(), key=lambda kv: int(kv[0])) if h in entries}


def main():
    races = json.load(open(HERE / "selected_races.json", encoding="utf-8"))
    style_cache, result = {}, {}
    for race in races:
        d, lab, rid = race["date"], race["label"], race["race_id"]
        out(f"== {d} {lab} {rid}（{race['why']}）")
        pk = json.load(open(ROOT / "output" / d / "pickup_scores.json", encoding="utf-8"))["races"][lab]
        saved = {"top3": {str(k): v for k, v in pk["top3_hits"].items()}, "pickup": parse_set(pk.get("pickup_nums")),
                 "analysis": {str(k): v for k, v in pk["analysis_hits"].items()}, "pace": pk.get("predicted_pace"),
                 "dist": pk.get("race_dist"), "position": parse_set(pk.get("position_nums"))}
        sh, dt = refetched_race(rid)
        refetch = {"top3": sh["top3_hits"], "pickup": {str(x) for x in dt["pickup_nums"]}, "analysis": dt["analysis_hits"],
                   "pace": sh["predicted_pace"], "dist": sh["race_dist"], "map_issues": sh["map_issues"],
                   "scratched_nums": sh["scratched_nums"]}
        if d not in style_cache:
            style_cache[d] = style_as_of(d)
        style_new = style_cache[d]
        rec = {"race": race, "saved_inputs": {k: (sorted(v) if isinstance(v, set) else v) for k, v in saved.items()},
               "refetched_inputs": {k: (sorted(v) if isinstance(v, set) else v) for k, v in refetch.items()},
               "saved_scored": [(h["馬番"], h["score"]) for h in pk["scored"]]}
        not_reproducible = None
        if d >= FIXED_FROM:
            id_map = {str(k): v for k, v in pk["horse_id_map"].items()}
            scratched = {str(k): v for k, v in (pk.get("scratched_nums") or {}).items()}
            id_source = "pickup_scores の horse_id_map と scratched_nums（当時の出馬表）"
        else:
            ra = json.load(open(ROOT / "output" / d / "race_results_all.json", encoding="utf-8"))["races"][lab]
            id_map = {str(h["num"]): str(h["horse_id"]) for h in ra["horses"] + ra.get("others", [])}
            out_st = {str(h["num"]): h.get("status") for h in ra.get("others", []) if h.get("status") in ("取消", "除外")}
            scratched = out_st
            id_source = "race_results_all（結果）。当時の出馬表の取消の印は保存していない"
            if out_st:
                not_reproducible = f"結果に取消・除外 {out_st}。当時の出走予定の馬を復元できない（以下の点数は取消・除外を外した参考の計算）"
        rec["horse_id_source"], rec["not_reproducible"] = id_source, not_reproducible
        if not_reproducible:
            out("  当時の採点を再現できない:", not_reproducible)
        shutuba = {"position_nums": saved["position"], "horse_id_map": id_map, "scratched_nums": scratched,
                   "pop_map": pk.get("pop_map", {}), "odds_map": pk.get("odds_map", {})}
        active = race_pickup.active_horse_ids(dict(shutuba))
        # 候補
        tables = load_index_tables(ROOT / "output" / f"{d}.xlsx")
        triple_now = scraper.find_triple_top5_rows(lab, tables[lab])
        rec["candidates_now"] = sorted(int(h["馬番"]) for h in triple_now)
        rec["candidates_saved_scored"] = sorted(int(h["馬番"]) for h in pk["scored"])
        variants = {}
        ent_new = entries_from_pages(active, d, prev_new)
        if d == "20261107":
            tri = pd.read_csv(ROOT / "output" / d / "全場_3指数重複馬.csv", encoding="utf-8-sig")
            triple = tri[(tri["開催場"] + tri["レース番号"]) == lab].to_dict(orient="records")
            run = json.load(open(ROOT / "output" / d / "prev_inputs" / f"{pk['prev_inputs_run']}.json", encoding="utf-8"))
            ent_saved = run["races"][lab]["horses"]
            style_then = json.load(open(WT / "output" / "horse_style.json", encoding="utf-8"))
            ent_old = entries_from_pages(active, d, prev_old)
            s3 = (saved["top3"], saved["pickup"], saved["analysis"])
            r3 = (refetch["top3"], refetch["pickup"], refetch["analysis"])
            variants["S1 保存した入力・本番の版"] = score(triple, shutuba, *s3, ent_saved, prev_old, style_then, d, saved["pace"], saved["dist"])
            variants["(a) 前走を取り直し・本番の版で読む"] = score(triple, shutuba, *s3, ent_old, prev_old, style_then, d, saved["pace"], saved["dist"])
            variants["(b) 前走の読み取りを今の版に"] = score(triple, shutuba, *s3, ent_new, prev_new, style_then, d, saved["pace"], saved["dist"])
            variants["(c) 脚質を対象日より前の結果で作り直し"] = score(triple, shutuba, *s3, ent_new, prev_new, style_new, d, saved["pace"], saved["dist"])
            variants["(d) 3項目を取り直した値に"] = score(triple, shutuba, *r3, ent_new, prev_new, style_new, d, saved["pace"], saved["dist"])
            rec["prev_saved"] = prev_view(ent_saved, active)
            rec["prev_refetched_old_version"] = prev_view(ent_old, active)
            diffs = {num: (rec["prev_saved"].get(num, {}).get("values"), rec["prev_refetched_old_version"][num]["values"])
                     for num in rec["prev_refetched_old_version"]
                     if rec["prev_saved"].get(num, {}).get("values") != rec["prev_refetched_old_version"][num]["values"]
                     or rec["prev_saved"].get(num, {}).get("status") != rec["prev_refetched_old_version"][num]["status"]}
            rec["prev_saved_vs_refetched_diff"] = diffs
            out("  前走: 本番の記録と、取り直したページ（本番の版で読む）の違い", len(diffs), "頭", diffs if diffs else "")
        else:
            triple = triple_now
            variants["(i) 3項目は保存値"] = score(triple, shutuba, saved["top3"], saved["pickup"], saved["analysis"], ent_new, prev_new,
                                              style_new, d, saved["pace"], saved["dist"])
            variants["(ii) 3項目は取り直した値"] = score(triple, shutuba, refetch["top3"], refetch["pickup"], refetch["analysis"], ent_new,
                                                 prev_new, style_new, d, saved["pace"], saved["dist"])
            variants["(iii) 3項目を入れない（v5.1 の再現ではない）"] = score(triple, shutuba, {}, set(), {}, ent_new, prev_new, style_new, d,
                                                               saved["pace"], saved["dist"])
        rec["prev_now_version"] = prev_view(ent_new, active)
        rec["variants"] = variants
        out("  候補: 今の定義", rec["candidates_now"], "・当時採点した馬", rec["candidates_saved_scored"])
        out("  保存した点数", rec["saved_scored"])
        for k, v in variants.items():
            out(f"  {k}", brief(v), "レース内1位の最大", v["top1_race_max"])
        out("  3項目 保存値 上位3頭", saved["top3"], "ピックアップ", sorted(saved["pickup"]), "出走馬分析", saved["analysis"])
        out("  3項目 取り直し 上位3頭", refetch["top3"], "ピックアップ", sorted(refetch["pickup"]), "出走馬分析", refetch["analysis"])
        out("  予測ペース 保存", saved["pace"], "取り直し", refetch["pace"], "・距離 保存", saved["dist"], "取り直し", refetch["dist"],
            "・取り直した出馬表の対応検証", refetch["map_issues"])
        st = {k: sum(1 for e in ent_new.values() if e["status"] == k) for k in {e["status"] for e in ent_new.values()}}
        out("  前走（今の版）の状態", st)
        result[f"{d} {lab}"] = rec
    files = sorted(PAGES.rglob("*.html.gz"))
    meta = {"pages_sha256": {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
            "code_sha256": {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest()
                            for p in [__file__, ROOT / "race_pickup.py", ROOT / "prev_inputs.py", WT / "prev_inputs.py",
                                      ROOT / "build_horse_style.py", ROOT / "scraper.py"]}}
    (HERE / "rebuild_trial_result.json").write_text(json.dumps({"races": result, "meta": meta}, ensure_ascii=False, indent=1,
                                                               default=lambda o: sorted(o) if isinstance(o, set) else str(o)),
                                                    encoding="utf-8")
    (HERE / "rebuild_trial_result.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
