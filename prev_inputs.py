"""
前走データ（馬ページの成績表）の取得・入力の記録・採点への受け渡し。

設計: docs/prev_cache_fix_plan_202611.md（2026-11-05 決定）
- 取得と採点を分ける。入力の記録 output/{日}/prev_inputs/{実行ID}.json を唯一の正本にする。
- 各馬の状態: 取れた / 前走なし / 指数が空（着順が数字でない） / 取得失敗 / 期限後の取得。
  取得異常を空の値や0点にしない。
- 「指数が空（着順が数字でない）」は 2026-11-05 の実ページ確認で足した（競走中止の行は着順「中」・指数が空欄。
  例: 2023105659 の 2026/11/01 京都4R）。着順が数字なのに指数が空なら取得失敗。
  正常とする数字でない着順は、実ページで確かめた表記だけ（CONFIRMED_NONNUMERIC_RANKS）。空・未知の文字は取得失敗
  （2026-11-05 外部レビュー指摘）。着順が数字の行は、人気・指数も数字でなければ取得失敗。
- 指数の「**」は取得失敗（2026-11-05 決定）。ログインしていない状態の表示だった（プレミアム登録の案内へのリンク。
  ログインし直すと同じ行が数字になった）。案Aの状態「指数が「**」」はやめた。
- 再利用は、馬ごとの最新の取得結果が「同じ対象日・同じ取得の版・期限内・状態が取得失敗以外」のときだけ。
  後の取得が失敗したら前の値に戻らない。
- 前走指数レース内1位: 比較に必要な値が分からない馬（取得失敗・期限後の取得・馬ID無し・入力が無い）がいれば保留（加点しない）。
  正常と確かめた前走なしの馬と、指数が空（着順が数字でない）の馬は比較の対象外。
- 前走は「正式な発走を受けた直近のレース」（2026-11-06 決定。docs/prev_excluded_rank_plan_202611.md 案1）。
  着順が「除」（競走除外。発走していない）の行は前走に選ばず飛ばす（続けて何行でも）。飛ばした次の行は今までと同じ規則で読み、
  知らない表記・読めない行なら取得失敗（さらに古い行へは進まない）。飛ばした後に行が無ければ、前走なしとは確かめられないので
  取得失敗。飛ばした行は skipped_rows、選んだ理由は selected_reason に残す。「中」（競走中止）は発走しているので今までどおり前走。
- 2026-11-12（実ページの確認 research/prev_rows_check_20261112/ と外部レビュー）:
  - 着順「取」（出走取消）も「除」と同じく飛ばす。結果の記録で「取消」の中央の馬5頭で、馬ページの同じレースの行がすべて「取」だった。
  - 前走が障害戦（距離が「障」で始まる）で、着順・人気が数字、指数のセルが空白だけの行は、状態「指数が空（障害戦）」。
    ページが正常なときだけ（JUMP_PAGE_CHECKS）。指数だけを使わず（前走指数の加点・レース内1位の比較の対象外）、
    日付・人気・着順・距離は採点に渡す（中4週・前走好走・同距離は今の規則どおり）。正常と確かめられなければ取得失敗のまま。
  - 初出走: 成績の表が無く、成績の欄に「競走データがありません」が1つだけあり、ページの馬ID・馬名・見出しが正常なら「前走なし」。
    「競走データがありません」は存在しない馬IDのページにも出るので、その文字だけでは前走なしにしない。
  - 対象日より前の行が、確かめた発走していない表記（取・除）だけの馬も、ページが正常なら「前走なし」（実際に出走した行が無い）。
  - ページが正常かの確かめ（page_identity）は、この3つの「正常な値なし」のときだけ行う（取れた値は今までどおり）。
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 20261113: 成績の表があって対象日より前の行が無い場合も、ページの馬ID・馬名・見出しを確かめてから前走なし（前走なしの全経路で同じ確かめ）
PREV_FETCH_VERSION = "見出しで列を特定_対象日ごとに取得_着順は確かめた表記だけ_除外取消の行は飛ばす_障害戦の指数の空_初出走_前走なしは全経路でページを確かめる_20261113"
TOP1_RULE = "比較値が不明なら保留_前走なしと中止等と障害戦は対象外_20261112"

OK = "取れた"
NO_PREV = "前走なし"
IDX_EMPTY = "指数が空（着順が数字でない）"   # 競走中止など。指数が無いことが分かっている（正常）
JUMP_IDX_EMPTY = "指数が空（障害戦）"   # 前走が障害戦で指数のセルが空（ページが正常なときだけ。2026-11-12）
FAILED = "取得失敗"
AFTER_DEADLINE = "期限後の取得"   # 期限（対象日 00:00 JST）の後に取り終えた値。採点にも再利用にも使わない
SCRATCHED = "取消・除外（出馬表）"   # 候補が出馬表で取消・除外。前走データを取らず、品質の判定の対象外
REUSABLE_STATUSES = {OK, NO_PREV, IDX_EMPTY, JUMP_IDX_EMPTY}
IDX_UNAVAILABLE_STATUSES = {NO_PREV, IDX_EMPTY, JUMP_IDX_EMPTY}   # 指数が無いと分かっている（レース内1位の比較の対象外）
USABLE_STATUSES = REUSABLE_STATUSES   # 採点に使える状態
# 品質の判定（data_quality.race_quality_issues）で、候補の状態として問題なしとするもの。
# 出馬表で取消・除外の候補は採点もしないので対象外（問題にしない）
QUALITY_OK_STATUSES = USABLE_STATUSES | {SCRATCHED}

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
# 正常とする数字でない着順（表記 → 意味）。実ページで確かめた表記だけを入れる。
# - 「中」: 競走中止（2026-11-05、tests/fixtures/horse_2023105659_results_20261105.html。人気は数字、指数は空欄）
# 取消・失格などの馬ページでの表記はまだ確かめていない（取得失敗になる。実例を見てから足す）
CONFIRMED_NONNUMERIC_RANKS = {"中": "競走中止"}
# 前走に選ばず飛ばす着順（表記 → 意味）。発走していないレース。実ページで確かめた表記だけを入れる。
# - 「除」: 競走除外（2026-11-06、tests/fixtures/horse_2020103876_20261106.html・horse_2021104929_20261106.html。
#   人気・指数は空。自前の結果の状態「除外」と一致し、1頭は JRA 公式の払戻ページでも確かめた）
# - 「取」: 出走取消（2026-11-12、research/prev_rows_check_20261112/。結果の記録で「取消」の中央の馬5頭の馬ページで、
#   同じレース（レースIDで照合）の行がすべて「取」、人気・オッズ・タイム・指数は空。地方の1件（浦和）も同じ表示）
SKIP_RANKS = {"除": "競走除外", "取": "出走取消"}
# 初出走の馬のページの成績の欄の表示（2026-11-12 確認。存在しない馬IDのページにも出るので、これだけでは決めない）
NO_RESULTS_TEXT = "競走データがありません"
HORSE_URL = "https://db.netkeiba.com/horse/{}/"


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


def _norm_name(s) -> str:
    return re.sub(r"\s+", "", s or "")


def page_identity(soup, horse_id, horse_name) -> list:
    """ページがその馬の正常なページかを確かめ、問題の一覧を返す（空なら正常）。2026-11-12 実ページで確認した形:
    canonical の URL が https://db.netkeiba.com/horse/{馬ID}/、.horse_title h1 が馬名、#horse_results_box の見出しが
    「{馬名}の競走成績」。存在しない馬IDのページは、馬名が空・見出し「の競走成績」・canonical が /horse//。
    馬名は出馬表の馬名と照らす（許す違いは空白だけ）。"""
    if not horse_id or not horse_name:
        return ["馬ID・出馬表の馬名が渡されていない"]
    issues = []
    canon = soup.find_all("link", rel="canonical")
    want = HORSE_URL.format(horse_id)
    if len(canon) != 1 or canon[0].get("href") != want:
        issues.append(f"canonical の URL が {want} 1つ、ではない（{[c.get('href') for c in canon]}）")
    h1 = soup.select(".horse_title h1")
    name = h1[0].get_text(strip=True) if len(h1) == 1 else None
    if len(h1) != 1:
        issues.append(f"馬名の要素が{len(h1)}個")
    elif not name:
        issues.append("馬名が空")
    elif _norm_name(name) != _norm_name(horse_name):
        issues.append(f"ページの馬名「{name}」が出馬表の馬名「{horse_name}」と違う")
    boxes = soup.select("#horse_results_box")
    if len(boxes) != 1:
        issues.append(f"成績の欄が{len(boxes)}個")
    else:
        h2 = boxes[0].find_all("h2")
        if len(h2) != 1 or (name and h2[0].get_text(strip=True) != f"{name}の競走成績"):
            issues.append(f"成績の欄の見出しが「{name}の競走成績」1つ、ではない（{[x.get_text(strip=True) for x in h2]}）")
    return issues


def _no_results_marks(soup) -> int:
    """ページの中の「競走データがありません」の数"""
    return len(soup.find_all(string=lambda t: t and NO_RESULTS_TEXT in t))


def _no_table(soup, horse_id, horse_name) -> dict:
    """成績の表が無いページ。初出走（正常なページで「競走データがありません」が成績の欄に1つだけ）なら前走なし"""
    issues = page_identity(soup, horse_id, horse_name)
    msgs = soup.select("#horse_results_box div.contents")
    if not (len(msgs) == 1 and msgs[0].get_text(strip=True) == NO_RESULTS_TEXT and _no_results_marks(soup) == 1):
        issues.append(f"成績の欄に「{NO_RESULTS_TEXT}」が1つだけ、ではない")
    if issues:
        return _failed("成績の表が無い（" + "; ".join(issues) + "）")
    return {"status": NO_PREV, "reason": f"初出走: 成績の表が無く「{NO_RESULTS_TEXT}」（ページの馬ID・馬名・見出しを確かめた）",
            "headers": None, "row": None, "values": None}


def _jump_page_issues(soup, horse_id, horse_name, parsed, col) -> list:
    """障害戦の行の指数の空を正常とするための、ページの確かめ（2026-11-12 外部レビュー）。問題の一覧を返す。
    - ページの馬ID・馬名・見出しが正常（page_identity）。列の対応は parse_results_table で確かめ済み。
    - ログインした状態の証拠: 指数の列に「**」（ログインしていない状態の表示）が1つも無く、かつ最新の行（日付が最も新しい行）
      より前の行に数字の指数が1つ以上ある（ログインしていない状態では、最新の行より前の指数は「**」になる。2026-11-05 確認）。
      最新の行より前に数字の指数が無い馬（障害戦しか走っていない馬など）は、ログインの状態を確かめられないので取得失敗のまま。"""
    issues = page_identity(soup, horse_id, horse_name)
    texts = [(d, _cell_text(tds[col["idx"]])) for d, tds in parsed]
    if any(t == "**" for _, t in texts):
        issues.append("指数の列に「**」がある（ログインしていない状態の表示）")
    newest = max(d for d, _ in texts)
    if not any(INT_RE.match(t) for d, t in texts if d != newest):
        issues.append("最新の行より前の行に数字の指数が無い（ログインした状態を確かめられない）")
    return issues


def parse_results_table(html: str, target_date: str, horse_id: str = None, horse_name: str = None) -> dict:
    """馬ページの HTML から、対象日より前の最新の行を選んで値を返す。
    戻り値: {"status", "reason", "headers", "row": {...元の文字...}, "values": {...変換後...}}
    状態の決め方は docs/prev_cache_fix_plan_202611.md 案A。
    horse_id・horse_name（出馬表の馬名）は、正常な値なし（初出走・発走していない行だけ・障害戦の指数の空）のときの
    ページの確かめに使う。渡されなければ、それらは取得失敗のまま（2026-11-12）。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table", class_="db_h_race_results")
    if len(tables) > 1:
        return _failed(f"成績の表が{len(tables)}個")
    if not tables:
        return _no_table(soup, horse_id, horse_name)
    table = tables[0]
    if _no_results_marks(soup):
        return _failed(f"成績の表と「{NO_RESULTS_TEXT}」の両方がある")
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
        # 2026-11-13〜: ほかの前走なしの経路（初出走・発走していない行だけ）と同じく、ページを確かめてから前走なしにする
        issues = page_identity(soup, horse_id, horse_name)
        if issues:
            return _failed(f"対象日より前の行が無い（行{len(parsed)}件）が、ページを確かめられない（前走なしとは確かめられない）: "
                           f"{'; '.join(issues)}", headers=header_cells)
        return {"status": NO_PREV, "reason": f"対象日より前の行が無い（行{len(parsed)}件。ページの馬ID・馬名・見出しを確かめた）",
                "headers": header_cells, "row": None, "values": None}
    skipped = []
    chosen = None
    for d, tds in sorted(before, key=lambda x: x[0], reverse=True):
        if sum(1 for x, _ in before if x == d) > 1:
            return _with_skipped(_failed(f"同じ日付の行が複数ある（{d}）", headers=header_cells), skipped)
        rank_raw = _cell_text(tds[col["rank"]])
        if rank_raw in SKIP_RANKS:
            link = tds[col["race_name"]].find("a", href=RACE_LINK_RE)
            skipped.append({"date": d, "race_id": RACE_LINK_RE.search(link["href"]).group(1) if link else "",
                            "rank": rank_raw, "meaning": SKIP_RANKS[rank_raw]})
            continue
        chosen = (d, tds)
        break
    if chosen is None:
        kinds = "・".join(sorted({x["rank"] for x in skipped}))
        issues = page_identity(soup, horse_id, horse_name)
        if issues:
            return _with_skipped(_failed(f"発走していない行（{kinds}）を{len(skipped)}行飛ばした後に行が無く、"
                                         f"ページを確かめられない（前走なしとは確かめられない）: {'; '.join(issues)}",
                                         headers=header_cells), skipped)
        e = {"status": NO_PREV, "reason": f"対象日より前に実際に出走した行が無い（発走していない行（{kinds}）だけ{len(skipped)}行。"
                                          f"ページの馬ID・馬名・見出しを確かめた）",
             "headers": header_cells, "row": None, "values": None}
        e = _with_skipped(e, skipped)
        e["selected_reason"] = "対象日より前に実際に出走した行が無い"
        return e
    d, tds = chosen
    return _with_skipped(_read_row(d, tds, col, header_cells,
                                   lambda: _jump_page_issues(soup, horse_id, horse_name, parsed, col)), skipped)


def _with_skipped(e: dict, skipped: list) -> dict:
    """飛ばした行と選んだ理由を記録に足す"""
    e["skipped_rows"] = skipped
    e["selected_reason"] = (f"発走していない行を{len(skipped)}行飛ばした次の行" if skipped else "対象日より前の最新の行")
    return e


def _read_row(d, tds, col, header_cells, jump_issues=None) -> dict:
    """前走に選んだ1行を読む（2026-11-05 からの規則。2026-11-06 に parse_results_table から分けた）。
    jump_issues: 障害戦の行の指数の空を正常としてよいかを確かめる関数（問題の一覧を返す。2026-11-12）"""
    raw = {k: _cell_text(tds[i]) for k, i in col.items()}
    link = tds[col["race_name"]].find("a", href=RACE_LINK_RE)
    raw["race_id"] = RACE_LINK_RE.search(link["href"]).group(1) if link else ""

    values = {"date": d, "race_id": raw["race_id"]}
    values["rank"] = int(raw["rank"]) if INT_RE.match(raw["rank"]) else None
    values["pop"] = int(raw["pop"]) if INT_RE.match(raw["pop"]) else None
    if values["rank"] is None and raw["rank"] not in CONFIRMED_NONNUMERIC_RANKS:
        return _failed(f"着順が数字でなく、確かめた表記でもない（{raw['rank']!r}）", headers=header_cells, raw=raw)
    if values["rank"] is not None and values["pop"] is None:
        return _failed(f"着順が数字なのに人気が数字でない（{raw['pop']!r}）", headers=header_cells, raw=raw)
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
        status, reason = IDX_EMPTY, f"指数が空で、着順が「{raw['rank']}」（{CONFIRMED_NONNUMERIC_RANKS[raw['rank']]}）"
    elif values["rank"] is None:
        return _failed(f"着順が「{raw['rank']}」なのに指数が空でない（{raw['idx']!r}）", headers=header_cells, raw=raw)
    elif raw["idx"] == "" and raw["dist"].startswith("障"):
        # 障害戦の行で、着順・人気が数字（上で確かめ済み）、指数のセルが空白だけ（2026-11-12）
        issues = jump_issues() if jump_issues else ["ページを確かめる材料が無い"]
        if issues:
            return _failed("障害戦の行で指数が空だが、正常と確かめられない: " + "; ".join(issues),
                           headers=header_cells, raw=raw)
        values["idx"] = None
        status, reason = JUMP_IDX_EMPTY, "障害戦の行で指数が空（ページの馬ID・馬名・見出し・ログインした状態を確かめた）"
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
def fetch_entry(page, horse_id: str, target_date: str, browse, settle, horse_name: str = None) -> dict:
    """馬ページを開いて読み取る。browse(page, url) でページを開き、settle(page) で読み込みを待つ。
    horse_name: 出馬表の馬名（正常な値なしのときのページの確かめに使う。2026-11-12）"""
    url = HORSE_URL.format(horse_id)
    started = now_jst()
    try:
        browse(page, url)
        settle(page)
        html = page.content()
        e = parse_results_table(html, target_date, horse_id, horse_name)
    except Exception as ex:  # ページが開けない等
        e = _failed(f"ページを開けない（{type(ex).__name__}: {ex}）")
    e.update(horse_id=horse_id, horse_name_expected=horse_name, target_date=target_date, fetch_version=PREV_FETCH_VERSION,
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


class DateLock:
    """同じ対象日の実行（run_pickup_all.py / rerun_failed_pickup.py）の二重起動を止めるロック。
    output/{日}/prev_inputs/.lock（.gitignore 済み）に OS のファイルロック（fcntl.flock・排他・待たない）をかける。
    入力の読み取りから、採点結果・派生ファイルの保存が終わるまで持つ（2026-11-05 外部レビュー指摘）。
    - ロックはファイルの有無ではなく、開いているプロセスが持つ。プロセスが終われば（強制終了を含む）OS が外すので、
      古いロックを回収する処理は無い（2026-11-06 外部レビュー指摘: 回収の競合で二重起動を許した）。
    - .lock のファイル自体は消さない（消すと、別のプロセスが別のファイルにロックをかけられてしまう）。
      中身は最後にロックを取ったプロセスの番号と時刻（参考。判定には使わない）。
    - 同じ Mac の上での実行を前提にする（ネットワーク上のフォルダでは確かめていない）。"""

    def __init__(self, out_dir: Path):
        import fcntl
        d = inputs_dir(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / ".lock"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                holder = os.pread(fd, 200, 0).decode("utf-8", "replace").strip()
            finally:
                os.close(fd)
            raise RuntimeError(f"同じ日の実行が動いている（ロック {self.path}。最後に取ったプロセス: {holder!r}）")
        os.ftruncate(fd, 0)
        os.pwrite(fd, f"{os.getpid()} {now_jst().isoformat(timespec='seconds')}\n".encode(), 0)
        self._fd = fd
        self._released = False
        import atexit
        atexit.register(self.release)

    def release(self):
        if self._released:
            return
        self._released = True
        import fcntl
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)


def new_run_id(started: datetime) -> str:
    """実行ID: 開始時刻（マイクロ秒まで）＋ランダムな文字。名前の順が開始の順になり、同じ秒でも衝突しない"""
    import uuid
    return started.strftime("%Y%m%dT%H%M%S%f") + "_" + uuid.uuid4().hex[:8]


class RunRecord:
    """1回の実行の記録。output/{日}/prev_inputs/{実行ID}.json。
    書くたびに一時ファイルに書き終えてから正式な名前にする。中身の無い・途中の .json を置かない
    （2026-11-06 外部レビュー指摘: 空の .json で次の実行の load_runs が止まった）。
    最初の保存は os.link で、同じ名前の記録が既にあれば失敗する（上書きしない）。
    lock（DateLock）を持っている間だけ作る。
    finish() は記録を閉じるだけで、ロックは呼び出し元が結果の保存の後に外す。"""

    def __init__(self, out_dir: Path, target_date: str, program: str, scoring_version: str, lock: DateLock):
        if lock is None or lock._released:
            raise RuntimeError("ロック（DateLock）を持たずに記録を作ろうとした")
        self.dir = inputs_dir(out_dir)
        started = now_jst()
        self.run_id = new_run_id(started)
        self.path = self.dir / f"{self.run_id}.json"
        self._published = False
        self._closed = False
        self.data = {"run_id": self.run_id, "target_date": target_date, "program": program,
                     "started_at": started.isoformat(timespec="seconds"), "finished_at": None,
                     "commit": _git_head(), "fetch_version": PREV_FETCH_VERSION,
                     "scoring_version": scoring_version, "top1_rule": TOP1_RULE, "races": {}}
        self.save()
        import atexit
        atexit.register(self.finish)   # 最初の保存が成功した記録だけ、終了時に閉じる

    def add_race(self, race_label: str, race_id: str, entries: dict, top1: dict):
        self.data["races"][race_label] = {"race_id": race_id, "horses": entries, "top1": top1}
        self.save()

    def save(self):
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        if not self._published:
            os.link(tmp, self.path)   # 既にあれば FileExistsError（既存の記録を上書きしない）
            os.unlink(tmp)
            self._published = True
        else:
            os.replace(tmp, self.path)

    def finish(self):
        if self._closed:
            return
        self._closed = True
        self.data["finished_at"] = now_jst().isoformat(timespec="seconds")
        self.save()


def write_json_atomic(path: Path, data) -> None:
    """一時ファイル（同じフォルダ・名前は実行ごとに別）に書いてから名前を変える。途中で止まっても元のファイルは壊れない"""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)


def _git_head() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(Path(__file__).parent), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def load_runs(out_dir: Path) -> list:
    """その日の記録を、実行IDの順（＝開始の順）に返す"""
    d = inputs_dir(out_dir)
    if not d.exists():
        return []
    runs = []
    for p in d.glob("*.json"):
        with open(p, encoding="utf-8") as f:
            runs.append(json.load(f))
    return sorted(runs, key=lambda r: (r.get("started_at") or "", r.get("run_id") or ""))


def reusable_entries(out_dir: Path, target_date: str) -> dict:
    """再利用してよい値 {馬ID: entry}。元の実行IDを付ける。
    馬ごとに最新の取得結果（source=取得）を先に選び、それが再利用できるときだけ返す。
    後の取得が失敗・期限後なら、前の成功値には戻らない（2026-11-05 外部レビュー指摘）。"""
    latest = {}
    for run in load_runs(out_dir):
        for race in run.get("races", {}).values():
            for hid, e in race.get("horses", {}).items():
                if e.get("source") == "取得":
                    latest[hid] = (run["run_id"], e)
    return {hid: dict(e, origin_run=rid) for hid, (rid, e) in latest.items() if is_reusable(e, target_date)}


# ── 採点への受け渡し ──
def prev_db_entry(e: dict):
    """score_horses に渡す前走の値（今までの horse_db と同じ形）。取れた・指数が空（着順が数字でない・障害戦）だけ。
    指数が空の状態では prev_idx が空（指数の加点なし）。日付・人気・着順・距離はそのまま渡す"""
    if e is None or e.get("status") not in (OK, IDX_EMPTY, JUMP_IDX_EMPTY):
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
    比較に必要な値が分からない馬がいれば保留（race_max=None）。前走なし・指数が空（着順が数字でない・障害戦）は対象外。"""
    compared, excluded, unknown = {}, {}, {}
    for num, hid in active.items():
        e = entries.get(hid) if hid else None
        if not hid:
            unknown[num] = "馬IDが無い"
        elif e is None:
            unknown[num] = "入力が無い"
        elif e["status"] in IDX_UNAVAILABLE_STATUSES:
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


def prev_status_summary(candidate_nums, active: dict, entries: dict, top1: dict, scratched: dict = None) -> dict:
    """quality.prev_status に入れる要約。
    scratched: 出馬表で取消・除外と明示された馬番（shutuba_data["scratched_nums"]）。その候補は採点もしないので
    「取消・除外（出馬表）」として、品質の判定の対象から外す（2026-11-05 外部レビュー指摘）。
    出馬表に取消・除外の印が無いのに馬IDが無い候補は「馬IDが無い」（問題あり）。"""
    scratched = scratched or {}
    cand = {}
    for num in candidate_nums:
        if num in scratched:
            cand[num] = SCRATCHED
            continue
        if num not in active or not active.get(num):
            cand[num] = "馬IDが無い"
            continue
        e = entries.get(active[num])
        cand[num] = e["status"] if e else "入力が無い"
    others = {}
    for num, hid in active.items():
        if num in cand:
            continue
        e = entries.get(hid) if hid else None
        s = e["status"] if e else "入力が無い"
        others[s] = others.get(s, 0) + 1
    return {"candidates": cand, "others": others, "top1": top1["decision"]}
