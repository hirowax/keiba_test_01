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
