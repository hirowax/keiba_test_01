"""
指数の表（近走平均・当該距離・当該コース）が出走馬とそろっているかの検査（2026-11-06 外部レビュー指摘）。

背景: type=rank のプリフライトは表に「1位」があれば通り、3頭だけの表でも通る（レビューで再現）。
過去には馬番1〜3だけの表を6日分取っていた（docs/incident_umaban_20261030.md タスク3。原因は仮説）。
CLAUDE.md の「頭数はページの N頭と一致すること」を、指数の表について実装する。

照合の相手は出馬表（本番の前には結果が無いため）。race_pickup.scrape_shutuba の horse_id_map
（馬番 → 馬ID。取消・除外の馬も残る）・horse_name_full（正式名）・scratched_nums（取消・除外）。

規則（指数の表ごとに判定する。3表をまとめて判定しない）:
- 表が無い・読めない（scraper.ranked_horses の取得異常） → 問題
- 出馬表の馬（取消・除外を除く）が表に無い（欠け） → 問題
- 表に、出馬表に無い馬番がある（余分） → 問題
- 同じ馬番が2回以上（重複。ranked_horses の取得異常に入る） → 問題
- 馬名が出馬表の正式名と違う（空白の違いだけ許す）・出馬表の馬名が無い → 問題
- 取消・除外の馬は、表にあってもなくてもよい（あれば記録だけ）
問題があるレースは data_quality.race_quality_issues で問題ありになり、買い目を作らず、サイトでは理由を出して
🔥・最注目馬から外す。

限界: 照合は採点するレース（3指数の候補がいるレース）だけ。表の欠けのせいで候補が出なかったレースは見つけられない。
→ 2026-11-13〜: 結果を取った後に、その日の全レースの表を結果と照合する（下の check_day_against_results。verify_results.py が呼ぶ）。
  後から見つけるための検査で、当日の公開は防がない。
サマリー（各指数のトップ5・単指数1位）は照合していない。
"""
import re
from pathlib import Path

import pandas as pd

INDEX_TABLE_CHECK_VERSION = "出馬表と馬番馬名を指数ごとに照合_20261106"
MODE_KEYS = {"近走平均": "average", "当該距離": "distance", "当該コース": "course"}
SPACES = re.compile(r"[\s　]+")


def same_name(a, b) -> bool:
    return SPACES.sub("", str(a)) == SPACES.sub("", str(b))


def load_index_tables(path: Path) -> dict:
    """scraper.py が保存した xlsx を {レース名: {mode_key: DataFrame}} に戻す（type=rank の DataFrame と同じ形）。
    各ブロックは「■レース名 指数名」の行、見出しの行、馬の行（空行か次の■まで）。"""
    races = {}
    for _, raw in pd.read_excel(path, sheet_name=None, header=None, dtype=str).items():
        raw = raw.fillna("")
        i = 0
        while i < len(raw):
            c0 = raw.iat[i, 0].strip()
            if not c0.startswith("■"):
                i += 1
                continue
            label, mode = c0[1:].split()
            hdr = [str(x) for x in raw.iloc[i + 1]]
            j = i + 2
            rows = []
            while j < len(raw) and raw.iat[j, 0].strip() != "" and not raw.iat[j, 0].startswith("■"):
                rows.append(list(raw.iloc[j]))
                j += 1
            races.setdefault(label, {})[MODE_KEYS[mode]] = pd.DataFrame(rows, columns=hdr)
            i = j
    return races


def check_index_tables(dfs, shutuba_data: dict) -> dict:
    """1レースの3つの指数の表を、出馬表と指数ごとに照合する。
    戻り値: {"version", "ok", "modes": {mode: {...}}, "problems": [理由の文字]}"""
    import scraper
    ids = {str(k): v for k, v in (shutuba_data.get("horse_id_map") or {}).items()}
    scratched = {str(k) for k in (shutuba_data.get("scratched_nums") or {})}
    names = shutuba_data.get("horse_name_full") or {}
    active = set(ids) - scratched
    out = {"version": INDEX_TABLE_CHECK_VERSION, "ok": True, "modes": {}, "problems": []}
    if not ids:
        out["ok"] = False
        out["problems"].append("出馬表の馬番が無い")
        return out
    for mode in scraper.MODES:
        df = (dfs or {}).get(mode)
        rec = {"missing": [], "extra": [], "name_mismatch": [], "errors": [], "scratched_in_table": []}
        if df is None:
            rec["errors"].append("表が無い")
        else:
            horses, errors, _notes = scraper.ranked_horses(df)
            rec["errors"] = list(errors)
            nums = [h["馬番"] for h in horses]
            rec["missing"] = sorted(active - set(nums), key=_num_key)
            rec["extra"] = sorted(set(nums) - set(ids), key=_num_key)
            rec["scratched_in_table"] = sorted(set(nums) & scratched, key=_num_key)
            for h in horses:
                n = h["馬番"]
                if n not in active:
                    continue
                full = names.get(n)
                if not full:
                    rec["name_mismatch"].append({"馬番": n, "表": h["馬名"], "出馬表": None})
                elif not same_name(h["馬名"], full):
                    rec["name_mismatch"].append({"馬番": n, "表": h["馬名"], "出馬表": full})
        bad = {k: rec[k] for k in ("errors", "missing", "extra", "name_mismatch") if rec[k]}
        if bad:
            out["ok"] = False
            out["problems"].append(f"{mode}: {bad}")
        out["modes"][mode] = rec
    return out


def _num_key(n):
    return int(n) if str(n).isdigit() else 999


# ── 結果を取った後の、その日の全レースの照合（2026-11-13〜。docs/incident_umaban_20261030.md タスク3の残り）──
# 本番の照合（check_index_tables）は採点するレース（候補がいるレース）だけなので、表の欠けで候補が出なかったレースを
# 見つけられない。結果を全レース分取るようになった（scrape_results.py。2026-11-12〜）ので、結果の後に全レースの表を
# 結果と照合する。後から見つけるための検査で、当日の公開を防ぐ検査ではない。
# 照合の規則は check_index_tables と同じ（相手を出馬表から結果に替えるだけ）:
#   結果の馬（着順のある馬と、着順が数字でない馬）から 馬番→馬ID・馬名、取消・除外の馬を作る。
# 表が無い指数の扱い（取得の時点で表なしの理由を記録していないので、表なしを正常とは確定しない。2026-11-13 外部レビュー）:
#   - 取得の記録（index_sources.json）が「取得異常」・記録にレースが無い・表があるのに記録が「なし」（食い違い） → 問題
#   - 「なし」で新馬戦（結果のクラス名に「新馬」） → 新馬戦のため表なしが想定されるが、取得の状態は未確認
#   - それ以外の「なし」（障害戦・初めてのコースなど） → 正常か取得失敗か未確認
#   どちらの未確認も正常の件数に入れず、件数を要約に出す（個別には知らせない）。
#   「なし」には、ページに表が無い・表を待つ時間切れ・ランキングでないページ・取得中の例外が入る（scraper.py）。
#   正常な表なしと確定するのは、取得の時点でページの表示などの証拠を記録するようにしてから。
# 検査できない（検査不能。問題とは分けるが、知らせる）:
#   - 必要なファイル（race_results_all.json・index_sources.json・{日}.xlsx）が無い
#   - 予定の照合の記録（race_results_all.json の schedule_check）が無い・予定を確定できない
#   - 予定のレース（race_results_all.json の race_ids）の結果が無い
ALL_RACES_CHECK_VERSION = "結果の後に全レースの指数の表を結果と照合_表なしは未確認_検査不能を分ける_20261113"
NEW_HORSE_CLASS_TEXT = "新馬"
NOT_STARTED = ("取消", "除外")


def result_as_shutuba(race: dict) -> dict:
    """race_results_all.json の1レースを、check_index_tables が受け取る出馬表の形にする"""
    horses = list(race.get("horses") or []) + list(race.get("others") or [])
    return {"horse_id_map": {str(h["num"]): h.get("horse_id") for h in horses},
            "horse_name_full": {str(h["num"]): h.get("name") for h in horses},
            "scratched_nums": {str(h["num"]): h.get("status") for h in (race.get("others") or [])
                               if h.get("status") in NOT_STARTED}}


def check_day_against_results(date: str, output_dir: Path) -> dict:
    """その日の全レースの指数の表を結果と照合する。ファイルは読むだけ。
    戻り値: {"version", "races", "tables", "problems": [文字], "uncheckable": [文字],
            "no_table_new_horse_unconfirmed": [...], "no_table_unconfirmed": [...]}"""
    import json
    import scraper
    from schedule_check import CONFIRMED
    out = {"version": ALL_RACES_CHECK_VERSION, "races": 0, "tables": 0, "problems": [], "uncheckable": [],
           "no_table_new_horse_unconfirmed": [], "no_table_unconfirmed": []}
    d = output_dir / date
    allp, srcp, xlsx = d / "race_results_all.json", d / "index_sources.json", output_dir / f"{date}.xlsx"
    missing = [why for p, why in ((allp, "全レースの結果（race_results_all.json）が無い"),
                                  (srcp, "指数の取得の記録（index_sources.json）が無い"),
                                  (xlsx, f"指数の表のファイル（{xlsx.name}）が無い")) if not p.exists()]
    if missing:
        out["uncheckable"] += [f"（その日）{w}" for w in missing]
        return out
    all_data = json.loads(allp.read_text(encoding="utf-8"))
    results = all_data.get("races") or {}
    planned = all_data.get("race_ids") or {}
    sources = json.loads(srcp.read_text(encoding="utf-8")).get("races") or {}
    tables = load_index_tables(xlsx)
    sc = all_data.get("schedule_check")
    if not sc:
        out["uncheckable"].append("（その日）予定の照合の記録（schedule_check）が無いので、予定のレースがそろっているか確かめられない")
    elif sc.get("status") != CONFIRMED:
        out["uncheckable"].append(f"（その日）予定を確定できない（{sc.get('reason')}）")
    if not planned:
        out["uncheckable"].append("（その日）予定のレースの一覧（race_ids）が無い")
    # 予定・結果・取得の記録・表の対応（どれかから抜けたレースを見逃さない）
    for label in sorted(set(planned) - set(results)):
        out["uncheckable"].append(f"{label}: 予定のレースの結果が無い")
    for label in sorted(set(results) - set(planned)):
        out["problems"].append(f"{label}: 結果にあるレースが、予定のレースの一覧に無い")
    for label in sorted(set(planned) - set(sources)):
        out["problems"].append(f"{label}: 予定のレースが、指数の取得の記録に無い")
    for label in sorted(set(sources) - set(planned)):
        out["problems"].append(f"{label}: 指数の取得の記録にあるレースが、予定のレースの一覧に無い")
    for label in sorted(set(tables) - set(sources)):
        out["problems"].append(f"{label}: 指数の表があるのに、取得の記録にレースが無い")
    for label in sorted(set(results) & set(sources)):
        race, src = results[label], sources[label]
        if planned.get(label) and src.get("race_id") and str(src["race_id"]) != str(planned[label]):
            out["problems"].append(f"{label}: レースIDが違う（取得の記録 {src['race_id']} / 予定 {planned[label]}）")
        new_horse = NEW_HORSE_CLASS_TEXT in str((race.get("condition") or {}).get("class_name") or "")
        dfs = tables.get(label) or {}
        res = check_index_tables(dfs, result_as_shutuba(race))
        out["races"] += 1
        for mode_key, mode in scraper.MODES.items():
            recorded = (src.get("modes") or {}).get(mode)
            if mode_key in dfs:
                out["tables"] += 1
                if recorded in (None, "なし", "取得異常"):
                    out["problems"].append(f"{label} {mode}: 表があるのに取得の記録が「{recorded}」（食い違い）")
                rec = res["modes"][mode_key]
                bad = {k: rec[k] for k in ("errors", "missing", "extra", "name_mismatch") if rec[k]}
                if bad:
                    out["problems"].append(f"{label} {mode}: 結果とそろわない {bad}")
            elif recorded == "なし" and new_horse:
                out["no_table_new_horse_unconfirmed"].append(f"{label} {mode}")
            elif recorded == "なし":
                out["no_table_unconfirmed"].append(
                    f"{label} {mode}（{(race.get('condition') or {}).get('class_name') or 'クラス名なし'}）")
            else:
                out["problems"].append(f"{label} {mode}: 表が無く、取得の記録が「{recorded}」")
    return out
