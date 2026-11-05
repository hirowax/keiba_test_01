"""
前走データ（馬ページの成績表）の取得・入力の記録・採点への受け渡し。

設計: docs/prev_cache_fix_plan_202611.md（2026-11-05 決定）
- 取得と採点を分ける。入力の記録 output/{日}/prev_inputs/{実行ID}.json を唯一の正本にする。
- 各馬の状態: 取れた / 前走なし / 指数が空（着順が数字でない） / 取得失敗 / 期限後の取得。
  取得異常を空の値や0点にしない。
- 「指数が空（着順が数字でない）」は 2026-11-05 の実ページ確認で足した（競走中止の行は指数が空欄。
  例: 2023105659 の 2026/11/01 京都4R）。着順が数字なのに指数が空なら取得失敗。
- 指数の「**」は取得失敗（2026-11-05 決定）。ログインしていない状態の表示だった（プレミアム登録の案内へのリンク。
  ログインし直すと同じ行が数字になった）。案Aの状態「指数が「**」」はやめた。
- 再利用は「同じ対象日・同じ取得の版・期限内・状態が取得失敗以外」の値だけ。取得失敗は前の値に戻らない。
- 前走指数レース内1位: 比較に必要な値が分からない馬（取得失敗・期限後の取得・馬ID無し・入力が無い）がいれば保留（加点しない）。
  正常と確かめた前走なしの馬と、指数が空（着順が数字でない）の馬は比較の対象外。
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

PREV_FETCH_VERSION = "見出しで列を特定_対象日ごとに取得_20261105"
TOP1_RULE = "比較値が不明なら保留_前走なしと中止等は対象外_20261105"

OK = "取れた"
NO_PREV = "前走なし"
IDX_EMPTY = "指数が空（着順が数字でない）"   # 競走中止など。指数が無いことが分かっている（正常）
FAILED = "取得失敗"
AFTER_DEADLINE = "期限後の取得"   # 期限（対象日 00:00 JST）の後に取り終えた値。採点にも再利用にも使わない
REUSABLE_STATUSES = {OK, NO_PREV, IDX_EMPTY}
USABLE_STATUSES = REUSABLE_STATUSES   # 品質の判定で「取れている」とする状態（data_quality.race_quality_issues）

JST = timezone(timedelta(hours=9))
IDX_MAX = 200          # 指数の表と同じく、200以上は異常（docs/data_sources.md）
DATE_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
INT_RE = re.compile(r"^-?\d+$")
RACE_LINK_RE = re.compile(r"/race/(\d{12})/?")

# 馬ページ table.db_h_race_results の見出しの文字（2026-11-05 に実ページ4頭で確認。tests/fixtures/horse_*.html）
# - 見出しは画面に見えている文字で比べる。指数の見出しの中にある切り替えの一覧（div.TimeIndexChangeList、
#   非表示）の文字は除く（_header_text）。
# - 「ﾀｲﾑ指数」（半角カナ）が通常のタイム指数。隣の「ﾀｲﾑ指数M」はタイム指数マスターで、見出しも各行のセルも
#   非表示（style="display:none"）。旧コードの添字21（prev_idx_m）はこの列で、4頭の全行で空か「**」だった。
# - 指数のセルには見出しと同じ class TimeIndexHeadCell01 が付いている。これも確かめる（IDX_CELL_CLASS）。
HEADERS = {
    "date": "日付",
    "race_name": "レース名",
    "pop": "人気",
    "rank": "着順",
    "dist": "距離",
    "idx": "ﾀｲﾑ指数",
}
IDX_CELL_CLASS = "TimeIndexHeadCell01"


class FetchError(Exception):
    """取得失敗（理由を持つ）"""


def now_jst() -> datetime:
    return datetime.now(JST)


def deadline_of(target_date: str) -> datetime:
    """取得の期限: 対象日の 00:00:00（日本時間）。これより前に取り終えた値だけを期限内とする"""
    return datetime.strptime(target_date, "%Y%m%d").replace(tzinfo=JST)


def within_deadline(fetched_at: str, target_date: str) -> bool:
    try:
        t = datetime.fromisoformat(fetched_at)
    except (TypeError, ValueError):
        return False
    if t.tzinfo is None:
        return False
    return t < deadline_of(target_date)


# ── 馬ページの表の読み取り（純粋な関数。テストは tests/test_prev_inputs.py）──
def _cell_text(td) -> str:
    return td.get_text(strip=True)


def _header_text(th) -> str:
    """見出しの、画面に見えている文字。指数の見出しの中の切り替えの一覧（非表示）を除く"""
    import copy
    th = copy.copy(th)
    for hidden in th.select(".TimeIndexChangeList"):
        hidden.decompose()
    return th.get_text(strip=True)


def parse_results_table(html: str, target_date: str) -> dict:
    """馬ページの HTML から、対象日より前の最新の行を選んで値を返す。
    戻り値: {"status", "reason", "headers", "row": {...元の文字...}, "values": {...変換後...}}
    状態の決め方は docs/prev_cache_fix_plan_202611.md 案A。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="db_h_race_results")
    if table is None:
        return _failed("成績の表が無い")
    header_cells = None
    for tr in table.find_all("tr"):
        ths = tr.find_all("th")
        if ths:
            header_cells = [_header_text(th) for th in ths]
            break
    if not header_cells:
        return _failed("見出し行が無い")
    if any(v is None for v in HEADERS.values()):
        return _failed("見出しの文字が未設定（実ページで確認してから設定する）", headers=header_cells)
    col = {}
    for key, name in HEADERS.items():
        idx = [i for i, h in enumerate(header_cells) if h == name]
        if len(idx) != 1:
            return _failed(f"見出し「{name}」が{len(idx)}個", headers=header_cells)
        col[key] = idx[0]

    rows = [tr for tr in table.find_all("tr") if tr.find_all("td")]
    parsed = []
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) != len(header_cells):
            return _failed(f"行の列数が見出しと違う（{len(tds)}列・見出し{len(header_cells)}列）", headers=header_cells)
        date_s = _cell_text(tds[col["date"]])
        m = DATE_RE.match(date_s)
        if not m:
            return _failed(f"日付を読めない行がある（{date_s!r}）", headers=header_cells)
        if IDX_CELL_CLASS not in (tds[col["idx"]].get("class") or []):
            return _failed(f"指数の列のセルに class {IDX_CELL_CLASS} が無い", headers=header_cells)
        parsed.append(("".join(m.groups()), tds))

    before = [(d, tds) for d, tds in parsed if d < target_date]
    if not before:
        return {"status": NO_PREV, "reason": f"対象日より前の行が無い（行{len(parsed)}件）",
                "headers": header_cells, "row": None, "values": None}
    d, tds = max(before, key=lambda x: x[0])
    if sum(1 for x, _ in before if x == d) > 1:
        return _failed(f"同じ日付の行が複数ある（{d}）", headers=header_cells)

    raw = {k: _cell_text(tds[i]) for k, i in col.items()}
    link = tds[col["race_name"]].find("a", href=RACE_LINK_RE)
    raw["race_id"] = RACE_LINK_RE.search(link["href"]).group(1) if link else ""

    values = {"date": d, "race_id": raw["race_id"]}
    for k in ("pop", "rank"):
        values[k] = int(raw[k]) if INT_RE.match(raw[k]) else None   # 中止・取消などは数字でない（正当）
    m = re.search(r"(\d{3,4})", raw["dist"])
    if not m:
        return _failed(f"距離を読めない（{raw['dist']!r}）", headers=header_cells, raw=raw)
    values["dist"] = int(m.group(1))
    values["surface_dist"] = raw["dist"]

    if raw["idx"] == "**":
        # ログインしていない状態の表示（2026-11-05 確認・決定）。取得失敗にして、再実行と品質の判定に乗せる
        a = tds[col["idx"]].find("a", href=True)
        if a:
            raw["idx_link"] = a["href"]
        return _failed("指数が「**」（ログインしていない状態の表示）", headers=header_cells, raw=raw)
    elif raw["idx"] == "" and values["rank"] is None:
        values["idx"] = None
        status, reason = IDX_EMPTY, f"指数が空で、着順が数字でない（{raw['rank']!r}）"
    elif INT_RE.match(raw["idx"]):
        v = int(raw["idx"])
        if v >= IDX_MAX:
            return _failed(f"指数が{IDX_MAX}以上（{v}）", headers=header_cells, raw=raw)
        values["idx"] = v
        status, reason = OK, ""
    else:
        return _failed(f"指数を読めない（{raw['idx']!r}）", headers=header_cells, raw=raw)
    return {"status": status, "reason": reason, "headers": header_cells, "row": raw, "values": values}


def _failed(reason, headers=None, raw=None):
    return {"status": FAILED, "reason": reason, "headers": headers, "row": raw, "values": None}


# ── 取得（ブラウザ）──
def fetch_entry(page, horse_id: str, target_date: str, browse, settle) -> dict:
    """馬ページを開いて読み取る。browse(page, url) でページを開き、settle(page) で読み込みを待つ。"""
    url = f"https://db.netkeiba.com/horse/{horse_id}/"
    started = now_jst()
    try:
        browse(page, url)
        settle(page)
        html = page.content()
        e = parse_results_table(html, target_date)
    except Exception as ex:  # ページが開けない等
        e = _failed(f"ページを開けない（{type(ex).__name__}: {ex}）")
    e.update(horse_id=horse_id, target_date=target_date, fetch_version=PREV_FETCH_VERSION,
             fetch_started_at=started.isoformat(timespec="seconds"),
             fetched_at=now_jst().isoformat(timespec="seconds"), source="取得")
    return mark_deadline(e)


def mark_deadline(e: dict) -> dict:
    """取得完了時刻が期限の後なら、読み取った状態を status_observed に残し、状態を「期限後の取得」にする"""
    if e.get("status") != AFTER_DEADLINE and not within_deadline(e.get("fetched_at"), e["target_date"]):
        e["status_observed"] = e["status"]
        e["status"] = AFTER_DEADLINE
    return e


def is_reusable(entry: dict, target_date: str) -> bool:
    return (entry.get("status") in REUSABLE_STATUSES
            and entry.get("target_date") == target_date
            and entry.get("fetch_version") == PREV_FETCH_VERSION
            and within_deadline(entry.get("fetched_at"), target_date))


# ── 入力の記録（正本）──
def inputs_dir(out_dir: Path) -> Path:
    return out_dir / "prev_inputs"


class RunRecord:
    """1回の実行の記録。output/{日}/prev_inputs/{実行ID}.json。
    書くたびに一時ファイルから名前を変える。同じ日の二重起動はロックファイルで止める。"""

    def __init__(self, out_dir: Path, target_date: str, program: str, scoring_version: str):
        self.dir = inputs_dir(out_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = self.dir / ".lock"
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(f"同じ日の取得が実行中か、前の実行が途中で止まった（{self.lock} を確かめてから消す）")
        os.write(fd, f"{os.getpid()} {now_jst().isoformat(timespec='seconds')}\n".encode())
        os.close(fd)
        started = now_jst()
        self.run_id = started.strftime("%Y%m%dT%H%M%S") + f"_{os.getpid()}"
        self.path = self.dir / f"{self.run_id}.json"
        self._closed = False
        import atexit
        atexit.register(self.close)   # 例外・sys.exit でもロックを外す（強制終了のときだけ残る）
        self.data = {"run_id": self.run_id, "target_date": target_date, "program": program,
                     "started_at": started.isoformat(timespec="seconds"), "finished_at": None,
                     "commit": _git_head(), "fetch_version": PREV_FETCH_VERSION,
                     "scoring_version": scoring_version, "top1_rule": TOP1_RULE, "races": {}}
        self.save()

    def add_race(self, race_label: str, race_id: str, entries: dict, top1: dict):
        self.data["races"][race_label] = {"race_id": race_id, "horses": entries, "top1": top1}
        self.save()

    def save(self):
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.data["finished_at"] = now_jst().isoformat(timespec="seconds")
        self.save()
        try:
            self.lock.unlink()
        except FileNotFoundError:
            pass


def _git_head() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(Path(__file__).parent), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def load_runs(out_dir: Path) -> list:
    """その日の記録を、実行IDの順に返す"""
    d = inputs_dir(out_dir)
    if not d.exists():
        return []
    runs = []
    for p in sorted(d.glob("*.json")):
        with open(p, encoding="utf-8") as f:
            runs.append(json.load(f))
    return runs


def reusable_entries(out_dir: Path, target_date: str) -> dict:
    """再利用してよい値 {馬ID: entry}（同じ馬が複数あれば、後の実行のもの）。元の実行IDを付ける"""
    out = {}
    for run in load_runs(out_dir):
        for race in run.get("races", {}).values():
            for hid, e in race.get("horses", {}).items():
                if e.get("source") == "取得" and is_reusable(e, target_date):
                    out[hid] = dict(e, origin_run=run["run_id"])
    return out


# ── 採点への受け渡し ──
def prev_db_entry(e: dict):
    """score_horses に渡す前走の値（今までの horse_db と同じ形）。取れた・指数が空（着順が数字でない）だけ"""
    if e is None or e.get("status") not in (OK, IDX_EMPTY):
        return None
    v = e["values"]
    d = v["date"]
    return {"prev_date": f"{d[:4]}/{d[4:6]}/{d[6:]}",
            "prev_pop": "" if v["pop"] is None else str(v["pop"]),
            "prev_rank": "" if v["rank"] is None else str(v["rank"]),
            "prev_idx": "" if v["idx"] is None else str(v["idx"]),
            "prev_dist": v["surface_dist"]}


def race_top1(active: dict, entries: dict) -> dict:
    """前走指数レース内1位の比較。active: {馬番: 馬ID}（取消・除外を除く）、entries: {馬ID: entry}
    比較に必要な値が分からない馬がいれば保留（race_max=None）。前走なし・指数が空（着順が数字でない）は対象外。"""
    compared, excluded, unknown = {}, {}, {}
    for num, hid in active.items():
        e = entries.get(hid) if hid else None
        if not hid:
            unknown[num] = "馬IDが無い"
        elif e is None:
            unknown[num] = "入力が無い"
        elif e["status"] in (NO_PREV, IDX_EMPTY):
            excluded[num] = e["status"]
        elif e["status"] == OK:
            compared[num] = e["values"]["idx"]
        else:
            unknown[num] = e["status"]
    if unknown:
        return {"decision": "保留", "race_max": None, "compared": compared,
                "excluded": excluded, "unknown": unknown, "rule": TOP1_RULE}
    race_max = max(compared.values()) if compared else None
    return {"decision": "判定", "race_max": race_max, "compared": compared,
            "excluded": excluded, "unknown": {}, "rule": TOP1_RULE}


def prev_status_summary(candidate_nums, active: dict, entries: dict, top1: dict) -> dict:
    """quality.prev_status に入れる要約"""
    cand = {}
    for num in candidate_nums:
        hid = active.get(num)
        e = entries.get(hid) if hid else None
        cand[num] = e["status"] if e else "入力が無い"
    others = {}
    for num, hid in active.items():
        if num in cand:
            continue
        e = entries.get(hid) if hid else None
        s = e["status"] if e else "入力が無い"
        others[s] = others.get(s, 0) + 1
    return {"candidates": cand, "others": others, "top1": top1["decision"]}
