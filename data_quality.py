"""
レース単位のデータ品質の記録と判定（2026-10-30 レビュー指摘: 品質を収集版の有無だけで判定しない）。

pickup_scores.json の各レースに "quality" を保存し、研究・閾値調整・再採点はこれで判定する。
- umaban:        馬番・馬IDの取り方（race_pickup.COLLECTOR_VERSION）。無ければ修正前（枠番を馬番として読んでいた）
- index_reader:  タイム指数の読み方（scraper.INDEX_READER_VERSION）。無ければ 2026-10-30 夜の修正前
                 （隠し値の読み取り・0とマイナスと「*」の扱いの誤りがあり得る）
- index_sources: 指数の種類ごとの取得経路（"type=rank" / "代わり(shutuba_submenu)" / "なし" / "取得異常"）
- candidate_rule: 3指数の候補の選び方（scraper.CANDIDATE_RULE_VERSION）。無ければ 2026-11-02 の修正前
                 （近走平均の「*」付きの値を外し、同じ値の馬を並び順で切っていた。docs/index_top3_definition_202611.md）
- rank_notes:    ページの順位の食い違い（netkeiba のページ自体の不整合。記録のみ）
- prev_fetch:    前走データの取り方（prev_inputs.PREV_FETCH_VERSION）。無ければ 2026-11-05 の修正前
                 （別の対象日のために取った前走を使い回し、比較相手は取得日を見ないキャッシュ。docs/prev_cache_fix_plan_202611.md）
- prev_inputs_run: 採点に使った前走データの記録（output/{date}/prev_inputs/{実行ID}.json）
- prev_status:   候補ごとの前走データの状態・比較相手の状態ごとの頭数・前走指数レース内1位の判定（判定／保留）
- index_table_check: 指数の表ごとの、出馬表との照合（index_table_check.check_index_tables。2026-11-06〜）。
                 前走データの取り方が修正後のデータで、記録が無い・照合で問題があれば、問題あり
- 無効化:        output/{date}/invalidated.json に載ったレース（買い目・採点ファイルは書き換えない）
"""
import json
from pathlib import Path

from prev_inputs import QUALITY_OK_STATUSES as USABLE_PREV_STATUSES

OUTPUT_DIR = Path(__file__).parent / "output"
INDEX_SOURCES_FILE = "index_sources.json"


def _load(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_invalidated(date: str, output_dir: Path = None) -> dict:
    """無効レース {label: reason}"""
    inv = _load((output_dir or OUTPUT_DIR) / date / "invalidated.json") or {}
    return {label: r.get("reason", "") for label, r in inv.get("races", {}).items()}


def load_index_sources(date: str, output_dir: Path = None) -> dict:
    """scraper.py が保存した指数の取得記録 {"index_reader_version": ..., "races": {label: {...}}}"""
    return _load((output_dir or OUTPUT_DIR) / date / INDEX_SOURCES_FILE) or {}


def build_quality(collector_version: str, index_sources: dict, label: str, prev: dict = None,
                  table_check: dict = None) -> dict:
    """採点時に pickup_scores.json の各レースへ保存する quality。prev は前走データの版・記録・状態、
    table_check は指数の表と出馬表の照合（index_table_check.check_index_tables の戻り値）"""
    race = index_sources.get("races", {}).get(label, {})
    prev = prev or {}
    return {
        "index_table_check": table_check,
        "prev_fetch": prev.get("prev_fetch"),
        "prev_inputs_run": prev.get("prev_inputs_run"),
        "prev_status": prev.get("prev_status"),
        "umaban": collector_version,
        "index_reader": index_sources.get("index_reader_version") if race else None,
        "index_sources": race.get("modes", {}),
        "index_read_errors": race.get("read_errors", {}),
        "candidate_rule": index_sources.get("candidate_rule_version") if race else None,
        "rank_notes": race.get("rank_notes", {}),
    }


def race_quality_issues(rdata: dict, invalid_reason: str = None) -> list:
    """研究・閾値調整に使えない理由のリスト（空なら使える）"""
    issues = []
    if invalid_reason:
        issues.append(f"無効: {invalid_reason}")
    if "error" in rdata:
        issues.append(f"採点エラー: {rdata['error']}")
    q = rdata.get("quality") or {}
    if not (q.get("umaban") or rdata.get("collector_version")):
        issues.append("馬番の取り方が修正前")
    if not q.get("index_reader"):
        issues.append("指数の読み方が未記録（2026-10-30 夜の修正前）")
    else:
        bad = {m: s for m, s in q.get("index_sources", {}).items() if s != "type=rank" and s != "代わり(shutuba_submenu)"}
        if bad:
            issues.append(f"指数が取れていない種類がある: {bad}")
    if not q.get("candidate_rule"):
        issues.append("候補の選び方が修正前（「*」付きを外す・同じ値は並び順。2026-11-02 より前）")
    if not q.get("prev_fetch"):
        issues.append("前走データの取り方が修正前（2026-11-05 より前）")
    else:
        bad = {n: s for n, s in ((q.get("prev_status") or {}).get("candidates") or {}).items()
               if s not in USABLE_PREV_STATUSES}
        if bad:
            issues.append(f"候補の前走データが取れていない: {bad}")
        # 指数の表と出馬表の照合（2026-11-06〜）。記録が無いものも通さない
        tc = q.get("index_table_check")
        if not tc:
            issues.append("指数の表と出馬表の照合の記録が無い")
        elif not tc.get("ok"):
            issues.append(f"指数の表が出馬表とそろわない: {tc.get('problems')}")
    return issues
