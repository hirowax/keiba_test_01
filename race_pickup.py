#!/usr/bin/env python3
"""
レース別 注目馬ピックアップ
(A) 3指数重複馬 × AI展開予測ポジション × データ上位馬 でスコアリング
"""

import json
import time
import random
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

COOKIES_FILE = Path("cookies.json")

# スコアリングバージョン: pickup_scores.json に埋め込む
# v1: 初期版  v2: 巻き返し廃止・ピックアップ+2→+1
# v3: 前走指数1位+1→+2・前走好走追加  v4: 中4週以内+1→+2(2026-05-04)
# v5: ポジション廃止・上位3頭上限2pt・前走指数合算上限3pt・逃げ馬Sペース連動(2026-05-04)
# v6: 3指数すべて1位+1・距離延長>50m -1・前走10着以下-1・中4週+2→+1(2026-06-11, docs/factor_audit_202606.md)
# v5に戻し: v6はクリーンデータN不足で検証未完。クリーン窓90日蓄積後に再投入予定(2026-06-14)
# v5.1: v5 の点数はそのまま。前走指数レース内1位は、比較に必要な値が分からない馬（取得失敗（指数の「**」を含む）・
#       期限後の取得・馬ID無し）がいれば保留（加点しない）。前走なしの馬は比較の対象外。比較相手も対象日ごとに取得
#       (2026-11-05, docs/prev_cache_fix_plan_202611.md。判定は prev_inputs.race_top1)
# v5.2: 点数の式は v5.1 と同じ。出走馬分析・データ分析ピックアップ・各データ上位3頭の読み取りを直した（DATA_TOP_READER_VERSION）。
#       出走馬分析は、v5.1 まで見出しを1つ前の表のものと取り違え、1つの表から最初の1頭しか数えていなかったので、
#       点数の分布が変わる。v5.1 の🔥（9点）・買い目の軸（8点）の基準は v5.2 に使わない
#       (2026-11-07, docs/data_top_reader_fix_plan_202611.md)
SCORING_VERSION = "v5.2"

# 今の🔥の基準（threshold_config.json の ev_threshold）と、ペーパートレードの買い目の軸の基準（8点）を決めた点数の版。
# これ以外の版（v5.2〜）の点数には、🔥を付けず、買い目も作らない（新しい基準はタスク10の手順で決める。
# docs/data_top_reader_fix_plan_202611.md 3.5）。index.html にも同じ一覧がある（FIRE_SCORING_VERSIONS）。
FIRE_RULE_SCORING_VERSIONS = ("v5", "v5.1")

# 3項目（各データ上位3頭・データ分析ピックアップ・出走馬分析）の読み方の版。quality.data_top_reader に記録する。
# 読み方を変えたら値を変える。
DATA_TOP_READER_VERSION = "表の中の見出しと馬番の要素で読み出馬表と照合_20261107"

# 出走馬分析で数える表（表の中の見出し。2026-11-07 に保存した5レースで見た6種類）
ANALYSIS_HORSE_TITLES = (
    "このコースが得意な馬", "この距離が得意な馬", "この競馬場が得意な馬", "今回の馬場状態が得意な馬",
    "今回のレース間隔で実績がある馬", "今回の調教評価で実績がある馬",
)
# 出走馬分析の欄にある、馬ではないと分かっている表（数えない）
ANALYSIS_OTHER_TITLES = (
    "このコースで有利な枠順", "このコースで有利な脚質", "このコースが得意な騎手", "このコースが得意な調教師",
    "このコースに実績がある種牡馬", "このコースに実績がある母父", "今回のオッズで期待できる騎手",
    "今回の調教師と相性の良い騎手", "今回の馬主と相性の良い騎手",
)
# 各データ上位3頭で、項目が騎手の表（数字は騎手が乗る馬の馬番。騎手名は出馬表で略されているので名前では照らさない）
TOP3_JOCKEY_TITLES = ("騎手",)


# ─── スコアリング定義 ────────────────────────────────────────────
SCORE_POSITION      = 0   # 推定ポジション有利馬（4コーナーAI）(N=68 勝率11.8% 単勝回収率34.6% → 廃止)
SCORE_TOP3_EACH     = 1   # shutuba 各データ上位3頭 カテゴリー登場1回
SCORE_PICKUP        = 1   # data_top データ分析ピックアップ3頭 (旧2→1: 組合せ依存、単体回収率66%)
SCORE_ANALYSIS      = 1   # data_top 出走馬分析 カテゴリー登場1回
SCORE_PREV_IDX_HIGH = 2   # 前走タイム指数90以上
SCORE_PREV_IDX_MID  = 1   # 前走タイム指数70〜89
SCORE_PREV_IDX_TOP1 = 2   # 前走タイム指数がレース内1位 (旧1→2: N=73勝率29%回収146%)
SCORE_RECENT_RACE   = 2   # 前走から28日以内（中4週以内）(v5: +2。v6で+1に降格したが検証不足で戻し)
SCORE_FRONT_RUNNER  = 1   # 逃げ馬（コーナー通過順履歴から推定）
SCORE_REVIVAL       = 0   # 前走1-3番人気かつ凡走(4着以下) 巻き返し馬 (旧2→0: N=83勝率2%回収10%)
SCORE_PREV_GOOD     = 2   # 前走1-6番人気かつ1-3着（好走確認）
SCORE_SAME_DIST     = 1   # 前走と同距離（±50m以内）

THRESHOLD_HIGH   = 5   # ★★★
THRESHOLD_MID    = 3   # ★★
THRESHOLD_LOW    = 1   # ★


def _load_cookies(context):
    if COOKIES_FILE.exists():
        context.add_cookies(json.loads(COOKIES_FILE.read_text()))


def _norm_name(s: str) -> str:
    """短縮名の空白・全半角を正規化"""
    return re.sub(r"\s+", "", s).strip()


def _is_rank1(v) -> bool:
    """トリプルCSVの順位フィールド（'1位' / '1位タイ' 等）が1位か"""
    return bool(re.match(r"^1位", str(v or "").strip()))


def _extract_race_id(url: str) -> str:
    m = re.search(r"race_id=(\d{12})", url)
    return m.group(1) if m else ""


# ─── shutuba 出馬表テーブルのパース ──────────────────────────────
class ShutubaMapError(ValueError):
    """出馬表の馬番↔馬ID対応が検証できない（採点・公開してはいけない）"""


# 収集ロジックの版: pickup_scores.json に埋め込む（スコア式の SCORING_VERSION とは別管理）
# collector_version が無い pickup_scores.json = 修正前（〜2026-10-30。枠番を馬番として読み、horse_id/オッズ/人気が別馬に付く）
# 取り方を変えたら値を変える（例: "修正後_YYYYMMDD_内容"）
COLLECTOR_VERSION = "修正後_20261030_馬番を正しく取得"

_UNSOLD_ODDS = {"", "-", "--", "---", "---.-", "**", "***"}


def parse_field_size(soup: BeautifulSoup):
    """RaceData02 の「15頭」から出走頭数（取消・除外を含む登録頭数）を返す。無ければ None"""
    rd2 = soup.find(class_="RaceData02")
    m = re.search(r"(\d+)頭", rd2.get_text(" ", strip=True)) if rd2 else None
    return int(m.group(1)) if m else None


def parse_shutuba_rows(soup: BeautifulSoup, field_size=None, expected_nums: set = frozenset()) -> dict:
    """
    出馬表（td.HorseInfo を含む行）から 馬番 → 馬名/horse_id/単勝オッズ/人気 を同一行由来で取得する。
    馬番は td.Umaban* のみから取る（td.Waku* は枠番。行内の最初の数字は枠番なので使わない）。

    map_issues が空でなければ対応が信頼できないため、そのレースは採点しない（score_horses が拒否する）。
    field_size: ページ上部の「N頭」（取消・除外を含む。2026-10-30 実ページで表の行数と一致を確認）。
                馬番の集合が 1..N と完全一致することを要求する（末尾欠落も検出）。不明なら異常扱い。
    expected_nums: AI展開図（HorseIcon）の馬番集合。出馬表行にない馬番があれば欠損として検出する。
    取消・除外: 行の class "Cancel" または td.Cancel_Txt（実ページで確認）。scratched_nums に入れ、対応表には残す。
    """
    horse_map: Dict[str, str] = {}
    horse_id_map: Dict[str, str] = {}
    waku_map: Dict[str, str] = {}   # 馬番 → 枠番（td.Waku*。3項目の照合に使う。2026-11-07〜）
    odds_map: Dict[str, float] = {}
    pop_map: Dict[str, str] = {}
    odds_status: Dict[str, str] = {}  # 馬番 → ok / unsold（未発売）/ scratched（取消・除外）/ no_cell / unparsable
    scratched_nums: Dict[str, str] = {}  # 馬番 → "取消" / "除外" 等（Cancel_Txt の文字）
    issues: List[str] = []

    seen_hids: Dict[str, str] = {}
    for tr in soup.find_all("tr"):
        info = tr.find("td", class_="HorseInfo")
        if not info:
            continue
        num_td = tr.find("td", class_=re.compile(r"^Umaban"))
        num = num_td.get_text(strip=True) if num_td else ""
        if not num.isdigit():
            issues.append("row_without_umaban")
            continue
        if num in horse_map:
            issues.append(f"dup_umaban:{num}")
            continue

        a = info.find("a", href=re.compile(r"/horse/\d+"))
        name = _norm_name(a.get_text(strip=True) if a else info.get_text(strip=True))
        horse_map[num] = name
        waku_td = tr.find("td", class_=re.compile(r"^Waku\d"))
        if waku_td and waku_td.get_text(strip=True).isdigit():
            waku_map[num] = waku_td.get_text(strip=True)
        m_id = re.search(r"/horse/(\d+)", a.get("href", "")) if a else None
        hid = m_id.group(1) if m_id else ""
        if not hid:
            issues.append(f"missing_horse_id:{num}")
        elif hid in seen_hids:
            issues.append(f"dup_horse_id:{seen_hids[hid]},{num}")
        else:
            seen_hids[hid] = num
            horse_id_map[num] = hid

        # 単勝オッズ: class="Txt_R Popular"（"Popular_Ninki" は含まない）
        # ※class_=lambda は個別クラスにもマッチしDOM順依存になるため、クラスリスト全体で判定する
        cancel_td = tr.find("td", class_="Cancel_Txt")
        scratched = "Cancel" in (tr.get("class") or []) or cancel_td is not None
        if scratched:
            scratched_nums[num] = cancel_td.get_text(strip=True) if cancel_td else "取消"
        odds_td = tr.find(lambda t: t.name == "td"
                          and "Popular" in (t.get("class") or [])
                          and "Popular_Ninki" not in (t.get("class") or []))
        if scratched:
            odds_status[num] = "scratched"
        elif not odds_td:
            odds_status[num] = "no_cell"
        else:
            val = re.sub(r"\s+", "", odds_td.get_text(strip=True))
            try:
                odds_map[num] = float(val)
                odds_status[num] = "ok"
            except ValueError:
                odds_status[num] = "unsold" if val in _UNSOLD_ODDS else "unparsable"
        # 人気: class に "Popular_Ninki" を持つセルのみ（オッズセルと誤取得しないよう限定）
        pop_td = tr.find(lambda t: t.name == "td"
                         and "Popular_Ninki" in (t.get("class") or []))
        if pop_td and not scratched:
            val = re.sub(r"\s+", "", pop_td.get_text(strip=True))
            if val.isdigit():
                pop_map[num] = val

    nums = {int(n) for n in horse_map}
    if not nums:
        issues.append("no_rows")
    elif nums != set(range(1, max(nums) + 1)):
        issues.append("umaban_not_contiguous:" + ",".join(
            str(n) for n in sorted(set(range(1, max(nums) + 1)) - nums)))
    if field_size is None:
        issues.append("field_size_unknown")
    elif nums != set(range(1, field_size + 1)):
        issues.append(f"row_count_mismatch:{len(nums)}/{field_size}")
    missing_expected = sorted((set(expected_nums) - set(horse_map)), key=int)
    if missing_expected:
        issues.append("missing_vs_horseicon:" + ",".join(missing_expected))

    return {
        "horse_map": horse_map,
        "horse_id_map": horse_id_map,
        "waku_map": waku_map,
        "odds_map": odds_map,
        "pop_map": pop_map,
        "odds_status": odds_status,
        "scratched_nums": scratched_nums,
        "map_issues": issues,
    }



# ─── 3項目（各データ上位3頭・データ分析ピックアップ・出走馬分析）の読み取り（2026-11-07〜。DATA_TOP_READER_VERSION）───
# 要素をクラス名で明示して読み、数字（馬番）は出馬表の行（馬番・枠番・正式名・馬ID）と照らす。照らせない項目は数えず、
# 理由を issues に入れる（issues があるレースは data_quality で品質の問題になる）。
# 状態: 馬あり / 推奨なし / 欄がない（ページにその欄・表が無い）/ 取得異常（issues に理由）。
# 根拠: docs/data_top_reader_fix_plan_202611.md、research/reading_check_3items_20261107/

def _cls_num(tag, prefix: str):
    """class の中の「prefix + 数字」（例 WakuBan3・Waku5）の数字"""
    for c in tag.get("class") or []:
        m = re.fullmatch(prefix + r"(\d+)", c)
        if m:
            return m.group(1)
    return None


def _check_against_rows(ref: dict, num: str, waku, short: str, kind: str, horse_id: str = None):
    """
    ページの1項目が出馬表の行と合うか。合えば None、合わなければ理由。
    ref: horse_name_full（馬番→正式名）・waku_map・horse_id_map（scrape_shutuba の戻り値）
    kind: "horse"（名前を照らす）/ "jockey"（騎手の表。騎手名は出馬表で略されているので、馬番と枠だけ照らす）
    名前は、ページの短い名前が正式名の先頭と同じで、同じ先頭の名前の別の馬がいないこと（馬IDがあれば馬IDで照らす）。
    """
    names = ref.get("horse_name_full") or {}
    if not num or num not in names:
        return f"馬番{num}の行が出馬表に無い"
    wk = (ref.get("waku_map") or {}).get(num)
    if waku is None or wk is None or waku != wk:
        return f"馬番{num}の枠が出馬表と違う（ページ {waku}・出馬表 {wk}）"
    if kind == "jockey":
        return None
    if horse_id is not None:
        if horse_id != (ref.get("horse_id_map") or {}).get(num):
            return f"馬番{num}の馬IDが出馬表と違う（ページ {horse_id}）"
        return None
    sn = _norm_name(short or "")
    if not sn or not names[num].startswith(sn):
        return f"馬番{num}の名前が出馬表と合わない（ページ {short}・出馬表 {names[num]}）"
    others = [n for n, nm in names.items() if n != num and nm.startswith(sn)]
    if others:
        return f"馬番{num}の名前「{sn}」で始まる別の馬がいる（馬番 {','.join(others)}）"
    return None


def parse_top3data(soup: BeautifulSoup, ref: dict) -> dict:
    """
    出馬表の「各データ上位3頭」（section.top3data）。表（table.AnaBestTable）ごとに、表の中の見出しと、
    div.Kyaku_Type_box の span.Kyaku_Type_Num（馬番）・WakuBanN（枠）・span.UmaName（短い名前）を読む。
    戻り値: hits {馬番: 登場した表の数}、tables [{title, state, nums}]、state（欄がない / あり）、issues
    騎手の表も、今の定義（v5 から）どおり数える（数字は騎手が乗る馬の馬番）。
    """
    sec = soup.find("section", class_="top3data") or soup.find(class_="top3data")
    if sec is None:
        return {"hits": {}, "tables": [], "state": "欄がない", "issues": []}
    hits: Dict[str, int] = {}
    tables, issues = [], []
    for tb in sec.find_all("table", class_="AnaBestTable"):
        t = tb.find(class_="PickupHorseTableTitle")
        title = t.get_text(strip=True) if t else None
        if not title:
            issues.append("上位3頭: 見出しの無い表がある")
            continue
        kind = "jockey" if title in TOP3_JOCKEY_TITLES else "horse"
        nums, bad = [], False
        boxes = tb.find_all("div", class_="Kyaku_Type_box")
        for box in boxes:
            sp = box.find("span", class_="Kyaku_Type_Num")
            un = box.find("span", class_="UmaName")
            if sp is None or un is None:
                issues.append(f"上位3頭（{title}）: 読めない形の項目がある")
                bad = True
                continue
            num = sp.get_text(strip=True)
            why = _check_against_rows(ref, num, _cls_num(sp, "WakuBan"), un.get_text(strip=True), kind)
            if why:
                issues.append(f"上位3頭（{title}）: {why}")
                bad = True
                continue
            if num in nums:
                issues.append(f"上位3頭（{title}）: 馬番{num}が2回ある")
                bad = True
                continue
            nums.append(num)
        for num in nums:
            hits[num] = hits.get(num, 0) + 1
        state = "取得異常" if bad else ("馬あり" if nums else "空")
        if not boxes:
            issues.append(f"上位3頭（{title}）: 項目が無い")
        tables.append({"title": title, "state": state, "nums": nums})
    if not tables and not issues:
        issues.append("上位3頭: 欄はあるが表が無い")
    return {"hits": hits, "tables": tables, "state": "あり", "issues": issues}


def parse_data_top(soup: BeautifulSoup, ref: dict) -> dict:
    """
    データ分析（data_top.html）。
    - ピックアップ: .DataPickupHorseWrap の dl ごとに span.Umaban_Num（馬番）・WakuN（枠）・
      a.data_top_horse_link（馬ID）を読み、出馬表の馬番・枠・馬IDと照らす。
    - 出走馬分析: table.PickupHorseTable* ごとに、表の中の見出しで種類を決める（ANALYSIS_HORSE_TITLES だけ数える。
      ANALYSIS_OTHER_TITLES は数えない。どちらでもない見出しは取得異常）。馬の表は div.Umaban_InBox ごとに
      span.Umaban_Num（馬番）・WakuN（枠）・短い名前を読み、出馬表と照らす。「推奨なし」は span.Txt の文字。
    戻り値: pickup_nums、pickup_state、analysis_hits {馬番: 登場した表の数}、analysis_tables [{title, state, nums}]、
            analysis_state、issues
    """
    issues = []
    # ピックアップ
    pickup_nums: set = set()
    wrap = soup.find(class_="DataPickupHorseWrap")
    if wrap is None:
        pickup_state = "欄がない"
    else:
        dls = wrap.find_all("dl")
        for dl in dls:
            sp = dl.find("span", class_="Umaban_Num")
            a = dl.find("a", class_="data_top_horse_link")
            m = re.search(r"/horse/(\d+)", a.get("href", "")) if a else None
            if sp is None or m is None:
                issues.append("ピックアップ: 読めない形の項目がある")
                continue
            num = sp.get_text(strip=True)
            why = _check_against_rows(ref, num, _cls_num(sp, "Waku"), a.get_text(strip=True), "horse", m.group(1))
            if why:
                issues.append(f"ピックアップ: {why}")
                continue
            if num in pickup_nums:
                issues.append(f"ピックアップ: 馬番{num}が2回ある")
                continue
            pickup_nums.add(num)
        if not dls:
            issues.append("ピックアップ: 欄はあるが項目が無い")
        pickup_state = "馬あり" if pickup_nums else "空"
    # 出走馬分析
    analysis_hits: Dict[str, int] = {}
    tables = []
    all_tables = soup.find_all("table", class_=lambda c: c and "PickupHorseTable" in str(c))
    for tb in all_tables:
        t = tb.find(class_="PickupHorseTableTitle")
        title = t.get_text(strip=True) if t else None
        if title in ANALYSIS_OTHER_TITLES:
            continue
        if title not in ANALYSIS_HORSE_TITLES:
            issues.append(f"出走馬分析: 知らない見出しの表がある（{title}）")
            continue
        nums, bad = [], False
        boxes = tb.find_all("div", class_="Umaban_InBox")
        for box in boxes:
            sp = box.find("span", class_="Umaban_Num")
            if sp is None or sp.find(class_="WakuTxt"):
                issues.append(f"出走馬分析（{title}）: 読めない形の項目がある")
                bad = True
                continue
            num = sp.get_text(strip=True)
            short = box.get_text(strip=True)[len(num):] if box.get_text(strip=True).startswith(num) else ""
            why = _check_against_rows(ref, num, _cls_num(sp, "Waku"), short, "horse")
            if why:
                issues.append(f"出走馬分析（{title}）: {why}")
                bad = True
                continue
            if num in nums:
                issues.append(f"出走馬分析（{title}）: 馬番{num}が2回ある")
                bad = True
                continue
            nums.append(num)
        txt = [x.get_text(strip=True) for x in tb.select("span.Txt")]
        if bad:
            state = "取得異常"
        elif nums:
            state = "馬あり"
        elif not boxes and txt == ["推奨なし"]:
            state = "推奨なし"
        else:
            state = "取得異常"
            issues.append(f"出走馬分析（{title}）: 馬も「推奨なし」も無い")
        for num in nums:
            analysis_hits[num] = analysis_hits.get(num, 0) + 1
        tables.append({"title": title, "state": state, "nums": nums})
    seen = [x["title"] for x in tables]
    dup = sorted({x for x in seen if seen.count(x) > 1})
    if dup:
        issues.append(f"出走馬分析: 同じ見出しの表が2つある（{dup}）")
    if not all_tables:
        analysis_state = "欄がない"
    else:
        analysis_state = "あり"
        for title in ANALYSIS_HORSE_TITLES:
            if title not in seen:
                tables.append({"title": title, "state": "欄がない", "nums": []})
    return {"pickup_nums": pickup_nums, "pickup_state": pickup_state, "analysis_hits": analysis_hits,
            "analysis_tables": tables, "analysis_state": analysis_state, "issues": issues}


def active_horse_ids(shutuba_data: dict) -> Dict[str, str]:
    """馬番 → horse_id のうち取消・除外を除いたもの（レース内比較・穴馬候補に使う）"""
    scratched = shutuba_data.get("scratched_nums") or {}
    return {n: h for n, h in (shutuba_data.get("horse_id_map") or {}).items() if n not in scratched}


# ─── shutuba ページのスクレイプ ──────────────────────────────────
def scrape_shutuba(page, race_id: str) -> dict:
    """
    Returns:
        position_nums: set of 馬番 (推定ポジション有利馬)
        top3_hits:     {馬番: カテゴリー数}
        horse_map:     {馬番: 馬名} (出馬表)
        odds_map:      {馬番: 単勝オッズ} (スコアリング時点。docs/roi_improvement_plan.md 柱1 Phase A)
    """
    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    # race.netkeiba トップを経由してから shutuba へ
    if random.random() < 0.5:
        try:
            page.goto("https://race.netkeiba.com/", wait_until="domcontentloaded")
            time.sleep(random.uniform(1.0, 3.0))
        except Exception:
            pass
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(random.uniform(2.5, 6.0))
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # ── 出馬表: 馬番 → 馬名 (AI展開図のHorseIconから取得が最も確実) ──
    horse_map: Dict[str, str] = {}
    for span in soup.select("span.HorseIcon[id^='Horse']"):
        horse_id = span.get("id", "")  # "Horse7" → "7"
        num = horse_id.replace("Horse", "")
        name_span = span.find(class_="HorseName")
        if name_span and num.isdigit():
            short_name = _norm_name(name_span.get_text(strip=True))
            if short_name:
                horse_map[num] = short_name

    # AI展開図に出ない馬はHorseInfoテーブルから補完 + horse_id取得 + 人気・オッズ取得
    rows = parse_shutuba_rows(soup, field_size=parse_field_size(soup), expected_nums=set(horse_map))
    # horse_map は表示・名前照合用（AI展開図の短縮名を優先、無い馬は正式名で補完）。正式名は horse_name_full
    for num, name in rows["horse_map"].items():
        horse_map.setdefault(num, name)
    horse_id_map = rows["horse_id_map"]
    pop_map: Dict[str, str] = rows["pop_map"]      # 馬番 → 人気順位
    odds_map: Dict[str, float] = rows["odds_map"]  # 馬番 → 単勝オッズ（スコアリング時点）

    # ── 推定ポジション有利馬 ──
    position_nums: set = set()
    pickup_area = soup.find(class_="PositionMapArea02")
    if pickup_area:
        # 馬番を取得: PositionPickupHorseWrap 内のテキストや horse link
        for tag in pickup_area.find_all(string=True):
            t = tag.strip()
            if t.isdigit():
                position_nums.add(t)
        # data_top_horse_link があれば名前でも照合
        for a in pickup_area.find_all("a", class_="data_top_horse_link"):
            name = _norm_name(a.get_text(strip=True))
            for num, hname in horse_map.items():
                if name in hname or hname in name:
                    position_nums.add(num)

    # ── 各データ上位3頭（出馬表と照らして読む。2026-11-07〜） ──
    ref = {"horse_name_full": rows["horse_map"], "waku_map": rows["waku_map"], "horse_id_map": horse_id_map}
    top3 = parse_top3data(soup, ref)
    top3_hits: Dict[str, int] = top3["hits"]  # 馬番 → 登場した表の数

    # ── 人気フォールバック: shutuba.html に Ninki がなければ speed page から取得 ──
    if not pop_map:
        try:
            speed_url = f"https://race.netkeiba.com/race/speed.html?race_id={race_id}&rf=shutuba_submenu"
            page.goto(speed_url, wait_until="domcontentloaded")
            time.sleep(random.uniform(2.0, 4.0))
            speed_soup = BeautifulSoup(page.content(), "html.parser")
            for tr in speed_soup.find_all("tr"):
                num_td  = tr.find(class_=re.compile(r"sk__umaban|UmaBan"))
                ninki_td = tr.find(class_="sk__ninki")
                if num_td and ninki_td:
                    num = num_td.get_text(strip=True)
                    val = re.sub(r"\s+", "", ninki_td.get_text(strip=True))
                    if num.isdigit() and val.isdigit():
                        pop_map[num] = val
        except Exception:
            pass

    # ── 予測ペース (S/M/H) ──
    predicted_pace = None
    pace_el = soup.find(class_="RacePace")
    if pace_el:
        m = re.search(r"[SMH]", pace_el.get_text())
        if m:
            predicted_pace = m.group()

    # ── レース距離・馬場種別（RaceData01 から） ──
    race_dist = None
    race_surface = None
    rd1 = soup.find(class_="RaceData01")
    if rd1:
        rd1_text = rd1.get_text(" ", strip=True)
        m = re.search(r"(\d{3,4})m", rd1_text)
        if m:
            race_dist = int(m.group(1))
        if "芝" in rd1_text:
            race_surface = "芝"
        elif "ダート" in rd1_text or re.search(r"ダ\d", rd1_text):
            race_surface = "ダート"
        elif "障" in rd1_text:
            race_surface = "障害"

    return {
        "horse_map": horse_map,
        "horse_id_map": horse_id_map,
        "pop_map": pop_map,
        "odds_map": odds_map,
        "odds_status": rows["odds_status"],
        "scratched_nums": rows["scratched_nums"],
        "horse_name_full": rows["horse_map"],
        "map_issues": rows["map_issues"],
        "position_nums": position_nums,
        "top3_hits": top3_hits,
        "top3_tables": top3["tables"],
        "top3_state": top3["state"],
        "top3_issues": top3["issues"],
        "waku_map": rows["waku_map"],
        "html": html,   # 採点に使ったページ（run_pickup_all が output/{日}/pages/ に保存する。pickup_scores には入れない）
        "predicted_pace": predicted_pace,
        "race_dist": race_dist,
        "race_surface": race_surface,
    }


# ─── data_top ページのスクレイプ ─────────────────────────────────
def scrape_data_top(page, race_id: str, shutuba_data: dict) -> dict:
    """
    データ分析のページを取り、parse_data_top で読む（出馬表と照らす。2026-11-07〜）。
    shutuba_data: scrape_shutuba の戻り値（horse_name_full・waku_map・horse_id_map を使う）
    Returns: pickup_nums、analysis_hits、pickup_state、analysis_tables、analysis_state、issues、html
    """
    url = f"https://race.netkeiba.com/race/data_top.html?race_id={race_id}&rf=race_submenu"
    # shutuba ページを経由してから data_top へ（自然な閲覧順）
    if random.random() < 0.4:
        try:
            via = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
            page.goto(via, wait_until="domcontentloaded")
            time.sleep(random.uniform(1.5, 4.0))
        except Exception:
            pass
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(random.uniform(2.5, 6.0))
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    res = parse_data_top(soup, shutuba_data)
    res["html"] = html
    return res


# ─── スコアリング ─────────────────────────────────────────────────
def score_horses(
    triple_horses: List[dict],
    shutuba_data: dict,
    data_top_data: dict,
    prev_db: dict = None,
    race_max_prev_idx: float = None,
    race_date: str = None,
    horse_style_db: dict = None,
    race_dist: int = None,
    predicted_pace: str = None,
) -> List[dict]:
    """
    triple_horses: [{馬番, 馬名, ...}]  ← (A) 3指数重複馬
    prev_db: {horse_id: {prev_pop, prev_rank, prev_idx, ...}}  ← horse_db.json

    出馬表の対応検証（map_issues）が NG のデータは採点しない（全実行経路の共通入口で拒否）。
    取消・除外の馬（scratched_nums）は採点候補から外す。
    """
    if shutuba_data.get("map_issues"):
        raise ShutubaMapError(f"出馬表の対応検証NG: {shutuba_data['map_issues']}")
    scratched_nums = shutuba_data.get("scratched_nums") or {}
    position_nums  = shutuba_data["position_nums"]
    top3_hits      = shutuba_data["top3_hits"]
    horse_id_map   = shutuba_data.get("horse_id_map", {})
    pop_map        = shutuba_data.get("pop_map", {})
    odds_map       = shutuba_data.get("odds_map", {})
    pickup_nums    = data_top_data["pickup_nums"]
    analysis_hits  = data_top_data["analysis_hits"]

    results = []
    for horse in triple_horses:
        num  = str(horse.get("馬番", "")).strip()
        if num in scratched_nums:
            continue
        name = horse.get("馬名", "")
        score = 0
        breakdown = []

        # ① 推定ポジション有利馬（廃止: SCORE_POSITION=0）
        if num in position_nums and SCORE_POSITION > 0:
            score += SCORE_POSITION
            breakdown.append({"label": "推定ポジション有利馬", "pts": SCORE_POSITION})

        # ② 各データ上位3頭（shutuba）上限2pt
        cnt = top3_hits.get(num, 0)
        if cnt > 0:
            pts = min(cnt * SCORE_TOP3_EACH, 2)
            score += pts
            breakdown.append({"label": f"各データ上位3頭 {cnt}カテゴリー", "pts": pts})

        # ③ データ分析ピックアップ3頭（data_top）
        if num in pickup_nums:
            score += SCORE_PICKUP
            breakdown.append({"label": "データ分析ピックアップ3頭", "pts": SCORE_PICKUP})

        # ④ 出走馬分析テーブル登場数（data_top）
        acnt = analysis_hits.get(num, 0)
        if acnt > 0:
            pts = acnt * SCORE_ANALYSIS
            score += pts
            breakdown.append({"label": f"出走馬分析 {acnt}条件該当", "pts": pts})

        # ⑨ 逃げ馬（Sペース予測時のみ加点）
        if horse_style_db is not None:
            hid_style = horse_id_map.get(num, "")
            style_entry = horse_style_db.get(hid_style, {}) if hid_style else {}
            if style_entry.get("style") == "逃げ" and style_entry.get("n_races", 0) >= 2:
                if predicted_pace == "S":
                    score += SCORE_FRONT_RUNNER
                    breakdown.append({"label": f"逃げ馬({style_entry['n_races']}走実績・Sペース)", "pts": SCORE_FRONT_RUNNER})

        # ⑤ 前走タイム指数 / ⑥ 巻き返し馬（prev_db使用）
        if prev_db is not None:
            hid = horse_id_map.get(num, "")
            prev = prev_db.get(hid, {}) if hid else {}

            # 汚染ガード: prev_date が対象日以降（=未来の前走）なら前走データを使わない
            if prev and race_date:
                _pd = (prev.get("prev_date") or "").replace("/", "")
                if len(_pd) == 8 and _pd.isdigit() and _pd >= race_date:
                    prev = {}

            # ⑤⑦ 前走タイム指数 + レース内1位（合算上限3pt）
            try:
                prev_idx = float(prev.get("prev_idx", ""))
                prev_idx_score = 0
                prev_idx_labels = []

                if prev_idx >= 90:
                    prev_idx_score += SCORE_PREV_IDX_HIGH
                    prev_idx_labels.append(f"前走指数{prev_idx:.0f}(90以上)")
                elif prev_idx >= 70:
                    prev_idx_score += SCORE_PREV_IDX_MID
                    prev_idx_labels.append(f"前走指数{prev_idx:.0f}(70以上)")

                if (race_max_prev_idx is not None and race_max_prev_idx > 0
                        and prev_idx == race_max_prev_idx):
                    prev_idx_score += SCORE_PREV_IDX_TOP1
                    prev_idx_labels.append(f"前走指数レース内1位({prev_idx:.0f})")

                if prev_idx_score > 3:
                    prev_idx_score = 3
                    prev_idx_labels.append("(合算上限3pt)")

                if prev_idx_score > 0:
                    score += prev_idx_score
                    breakdown.append({"label": " + ".join(prev_idx_labels), "pts": prev_idx_score})
            except (ValueError, TypeError):
                pass

            # ⑧ 前走から28日以内（中4週以内）
            if race_date:
                try:
                    prev_date_str = prev.get("prev_date", "")
                    if prev_date_str:
                        pd_dt = datetime.strptime(prev_date_str.replace("/", ""), "%Y%m%d")
                        rd_dt = datetime.strptime(race_date, "%Y%m%d")
                        days = (rd_dt - pd_dt).days
                        if 0 < days <= 28:
                            score += SCORE_RECENT_RACE
                            breakdown.append({"label": f"中4週以内({days}日)", "pts": SCORE_RECENT_RACE})
                except (ValueError, TypeError):
                    pass

            # ⑥ 巻き返し馬: 廃止 (N=83 勝率2% 回収10% → SCORE_REVIVAL=0)

            # ⑩ 前走好走: 前走1-6番人気 かつ 前走1-3着
            try:
                prev_pop_val  = int(prev.get("prev_pop", "") or 99)
                prev_rank_val = int(prev.get("prev_rank", "") or 99)
                if 1 <= prev_pop_val <= 6 and 1 <= prev_rank_val <= 3:
                    score += SCORE_PREV_GOOD
                    breakdown.append({"label": f"前走好走(前走{prev_pop_val}人気{prev_rank_val}着)", "pts": SCORE_PREV_GOOD})
            except (ValueError, TypeError):
                pass

            # ⑪ 同距離前走（±50m以内）
            if race_dist:
                try:
                    prev_dist_str = prev.get("prev_dist", "")
                    if prev_dist_str:
                        m = re.search(r"(\d{3,4})", prev_dist_str)
                        if m:
                            diff = race_dist - int(m.group(1))
                            if abs(diff) <= 50:
                                score += SCORE_SAME_DIST
                                breakdown.append({"label": f"同距離前走({m.group(1)}m→{race_dist}m)", "pts": SCORE_SAME_DIST})
                except (ValueError, TypeError):
                    pass

        # 星ランク
        if score >= THRESHOLD_HIGH:
            rank = "★★★"
        elif score >= THRESHOLD_MID:
            rank = "★★"
        elif score >= THRESHOLD_LOW:
            rank = "★"
        else:
            rank = "－"

        results.append({
            **horse,
            "score": score,
            "rank": rank,
            "breakdown": breakdown,
            "today_pop": pop_map.get(num, ""),
            "today_odds": odds_map.get(num, ""),
        })

    results.sort(key=lambda x: -x["score"])
    return results


# ─── メイン API ───────────────────────────────────────────────────
def analyze_race(shutuba_url: str, triple_horses: List[dict], prev_db: dict = None) -> dict:
    """
    shutuba_url:   出馬表URL (race_id入り)
    triple_horses: (A) この race の3指数重複馬リスト
    """
    race_id = _extract_race_id(shutuba_url)
    if not race_id:
        return {"error": "race_id が URL から取得できませんでした"}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        _load_cookies(context)
        page = context.new_page()

        shutuba_data = scrape_shutuba(page, race_id)
        time.sleep(random.uniform(3.0, 8.0))
        data_top_data = scrape_data_top(page, race_id, shutuba_data)

        browser.close()

    try:
        scored = score_horses(triple_horses, shutuba_data, data_top_data, prev_db=prev_db)
    except ShutubaMapError as e:
        return {"error": str(e), "race_id": race_id, "map_issues": shutuba_data.get("map_issues", [])}

    # スコア付き馬が1頭もいない場合のアドバイス
    has_any_bonus = any(h["score"] > 0 for h in scored)
    advice = None
    if not has_any_bonus:
        advice = (
            "3指数重複馬全員のボーナス点が0です。"
            "緩和案: データ上位3頭・データ分析ピックアップの対象を(A)以外にも広げる、"
            "または近走タイム指数のトップ5→トップ7に条件を広げてみてください。"
        )

    return {
        "race_id": race_id,
        "horse_map": shutuba_data["horse_map"],
        "horse_id_map": shutuba_data.get("horse_id_map", {}),
        "map_issues": shutuba_data.get("map_issues", []),
        "position_nums": list(shutuba_data["position_nums"]),
        "top3_hits": shutuba_data["top3_hits"],
        "pickup_nums": list(data_top_data["pickup_nums"]),
        "analysis_hits": data_top_data["analysis_hits"],
        "scored": scored,
        "advice": advice,
    }
