"""
レース単位のデータ品質の記録と判定（2026-10-30 レビュー指摘: 品質を収集版の有無だけで判定しない）。

pickup_scores.json の各レースに "quality" を保存し、研究・閾値調整・再採点はこれで判定する。
- umaban:        馬番・馬IDの取り方（race_pickup.COLLECTOR_VERSION）。無ければ修正前（枠番を馬番として読んでいた）
- index_reader:  タイム指数の読み方（scraper.INDEX_READER_VERSION）。無ければ 2026-10-30 夜の修正前
                 （隠し値の読み取り・0とマイナスと「*」の扱いの誤りがあり得る）
- index_sources: 指数の種類ごとの取得経路（"type=rank" / "代わり(shutuba_submenu)" / "なし" / "取得異常"）
- 無効化:        output/{date}/invalidated.json に載ったレース（買い目・採点ファイルは書き換えない）

既知の未修正の問題（判定には入れていない。docs/incident_umaban_20261030.md 追記2）:
近走平均の「*」付きの値が3指数の候補選びから外れている。
"""
import json
from pathlib import Path

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


def build_quality(collector_version: str, index_sources: dict, label: str) -> dict:
    """採点時に pickup_scores.json の各レースへ保存する quality"""
    race = index_sources.get("races", {}).get(label, {})
    return {
        "umaban": collector_version,
        "index_reader": index_sources.get("index_reader_version") if race else None,
        "index_sources": race.get("modes", {}),
        "index_read_errors": race.get("read_errors", {}),
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
    return issues
