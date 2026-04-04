#!/usr/bin/env python3
"""
レース別 注目馬ピックアップ
(A) 3指数重複馬 × AI展開予測ポジション × データ上位馬 でスコアリング
"""

import json
import time
import random
import re
from pathlib import Path
from typing import List, Dict

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

COOKIES_FILE = Path("cookies.json")


# ─── スコアリング定義 ────────────────────────────────────────────
SCORE_POSITION      = 1   # 推定ポジション有利馬（4コーナーAI）
SCORE_TOP3_EACH     = 1   # shutuba 各データ上位3頭 カテゴリー登場1回
SCORE_PICKUP        = 2   # data_top データ分析ピックアップ3頭
SCORE_ANALYSIS      = 1   # data_top 出走馬分析 カテゴリー登場1回
SCORE_PREV_IDX_HIGH = 2   # 前走タイム指数90以上
SCORE_PREV_IDX_MID  = 1   # 前走タイム指数70〜89
SCORE_REVIVAL       = 2   # 前走1-3番人気かつ凡走(4着以下) 巻き返し馬

THRESHOLD_HIGH   = 5   # ★★★
THRESHOLD_MID    = 3   # ★★
THRESHOLD_LOW    = 1   # ★


def _load_cookies(context):
    if COOKIES_FILE.exists():
        context.add_cookies(json.loads(COOKIES_FILE.read_text()))


def _norm_name(s: str) -> str:
    """短縮名の空白・全半角を正規化"""
    return re.sub(r"\s+", "", s).strip()


def _extract_race_id(url: str) -> str:
    m = re.search(r"race_id=(\d{12})", url)
    return m.group(1) if m else ""


# ─── shutuba ページのスクレイプ ──────────────────────────────────
def scrape_shutuba(page, race_id: str) -> dict:
    """
    Returns:
        position_nums: set of 馬番 (推定ポジション有利馬)
        top3_hits:     {馬番: カテゴリー数}
        horse_map:     {馬番: 馬名} (出馬表)
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
    horse_id_map: Dict[str, str] = {}  # 馬番 → horse_id
    for span in soup.select("span.HorseIcon[id^='Horse']"):
        horse_id = span.get("id", "")  # "Horse7" → "7"
        num = horse_id.replace("Horse", "")
        name_span = span.find(class_="HorseName")
        if name_span and num.isdigit():
            short_name = _norm_name(name_span.get_text(strip=True))
            if short_name:
                horse_map[num] = short_name

    # AI展開図に出ない馬はHorseInfoテーブルから補完 + horse_id取得 + 人気取得
    pop_map: Dict[str, str] = {}  # 馬番 → 人気順位
    for a in soup.select("td.HorseInfo a[href*='/horse/']"):
        name = _norm_name(a.get_text(strip=True))
        href = a.get("href", "")
        m_id = re.search(r"/horse/(\d+)", href)
        hid = m_id.group(1) if m_id else ""
        if not name:
            continue
        tr = a.find_parent("tr")
        if not tr:
            continue
        # 馬番セルを探す (class='Num' か最初の数字セル)
        num_td = tr.find("td", class_="Num") or tr.find("td", class_="num")
        if num_td:
            num = num_td.get_text(strip=True)
        else:
            tds = tr.find_all("td")
            num = next((t.get_text(strip=True) for t in tds if t.get_text(strip=True).isdigit()), "")
        if num.isdigit():
            if num not in horse_map:
                horse_map[num] = name[:4]  # 短縮名で補完
            if hid and num not in horse_id_map:
                horse_id_map[num] = hid
            # 人気（Popular class）
            if num not in pop_map:
                pop_td = tr.find("td", class_=lambda c: c and "Popular" in c)
                if pop_td:
                    val = re.sub(r"\s+", "", pop_td.get_text(strip=True))
                    if val.isdigit():
                        pop_map[num] = val

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

    # ── 各データ上位3頭 ──
    top3_hits: Dict[str, int] = {}  # 馬番 → カテゴリー登場数
    top3_section = soup.find(class_="top3data")
    if top3_section:
        # 構造: [カテゴリー名 | 馬番 | 馬名 | 馬番 | 馬名 | 馬番 | 馬名 | link] × n
        # テキストノードをフラットに取得してパース
        items = [t.strip() for t in top3_section.get_text(separator="|").split("|") if t.strip()]
        i = 0
        while i < len(items):
            tok = items[i]
            # カテゴリーと思われる行の次に 馬番(数字) が続くパターンを探す
            if not tok.isdigit() and i + 1 < len(items) and items[i + 1].isdigit():
                # カテゴリー開始: 次の3組を馬番・馬名として取得
                j = i + 1
                while j < len(items) and items[j].isdigit():
                    num = items[j]
                    top3_hits[num] = top3_hits.get(num, 0) + 1
                    j += 2  # 馬番・馬名をスキップ
                i = j
                continue
            i += 1

    return {
        "horse_map": horse_map,
        "horse_id_map": horse_id_map,
        "pop_map": pop_map,
        "position_nums": position_nums,
        "top3_hits": top3_hits,
    }


# ─── data_top ページのスクレイプ ─────────────────────────────────
def scrape_data_top(page, race_id: str, horse_map: Dict[str, str]) -> dict:
    """
    Returns:
        pickup_nums:   set of 馬番 (データ分析ピックアップ3頭)
        analysis_hits: {馬番: カテゴリー数} (出走馬分析テーブル)
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

    # 名前→馬番の逆引きマップ（短縮名でも照合できるように）
    name_to_num: Dict[str, str] = {}
    for num, name in horse_map.items():
        name_to_num[name] = num
        # 前方4文字の短縮名でも登録
        if len(name) >= 4:
            name_to_num[name[:4]] = num

    def name_to_num_fuzzy(short_name: str) -> str:
        sn = _norm_name(short_name)
        if sn in name_to_num:
            return name_to_num[sn]
        for full, num in name_to_num.items():
            if sn in full or full in sn:
                return num
        return ""

    # ── データ分析ピックアップ3頭 ──
    pickup_nums: set = set()
    pickup_wrap = soup.find(class_="DataPickupHorseWrap")
    if pickup_wrap:
        for a in pickup_wrap.find_all("a", class_="data_top_horse_link"):
            name = _norm_name(a.get_text(strip=True))
            num = name_to_num_fuzzy(name)
            if num:
                pickup_nums.add(num)

    # ── 出走馬分析テーブル ──
    analysis_hits: Dict[str, int] = {}
    # 各テーブルリスト内の「このコースが得意な馬」「この距離が得意な馬」等を取得
    for table in soup.find_all("table", class_=lambda c: c and "PickupHorseTable" in str(c)):
        # テーブルタイトルが「出走馬分析」系か確認
        title_div = table.find_previous(class_="PickupHorseTableTitle")
        if not title_div:
            continue
        title_txt = title_div.get_text(strip=True)
        # コース・距離・競馬場・馬場状態・調教評価等の出走馬系のみ対象
        is_horse_category = any(kw in title_txt for kw in [
            "得意な馬", "実績がある馬", "調教評価", "クッション", "レース間隔"
        ])
        if not is_horse_category:
            continue
        for tr in table.find_all("tr"):
            for td in tr.find_all("td"):
                txt = td.get_text(strip=True)
                # 「9コッツ」のように馬番+短縮名が連結されているパターン
                m = re.match(r"^(\d+)(.+)$", txt)
                if m:
                    num, name_part = m.group(1), m.group(2)
                    if num.isdigit() and int(num) <= 18:
                        analysis_hits[num] = analysis_hits.get(num, 0) + 1
                        continue
                # 数字のみ
                if txt.isdigit() and int(txt) <= 18:
                    analysis_hits[txt] = analysis_hits.get(txt, 0) + 1
                elif len(_norm_name(txt)) >= 2:
                    num = name_to_num_fuzzy(_norm_name(txt))
                    if num:
                        analysis_hits[num] = analysis_hits.get(num, 0) + 1

    return {
        "pickup_nums": pickup_nums,
        "analysis_hits": analysis_hits,
    }


# ─── スコアリング ─────────────────────────────────────────────────
def score_horses(
    triple_horses: List[dict],
    shutuba_data: dict,
    data_top_data: dict,
    prev_db: dict = None,
) -> List[dict]:
    """
    triple_horses: [{馬番, 馬名, ...}]  ← (A) 3指数重複馬
    prev_db: {horse_id: {prev_pop, prev_rank, prev_idx, ...}}  ← horse_db.json
    """
    position_nums  = shutuba_data["position_nums"]
    top3_hits      = shutuba_data["top3_hits"]
    horse_id_map   = shutuba_data.get("horse_id_map", {})
    pop_map        = shutuba_data.get("pop_map", {})
    pickup_nums    = data_top_data["pickup_nums"]
    analysis_hits  = data_top_data["analysis_hits"]

    results = []
    for horse in triple_horses:
        num  = str(horse.get("馬番", "")).strip()
        name = horse.get("馬名", "")
        score = 0
        breakdown = []

        # ① 推定ポジション有利馬
        if num in position_nums:
            score += SCORE_POSITION
            breakdown.append({"label": "推定ポジション有利馬", "pts": SCORE_POSITION})

        # ② 各データ上位3頭（shutuba）
        cnt = top3_hits.get(num, 0)
        if cnt > 0:
            pts = cnt * SCORE_TOP3_EACH
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

        # ⑤ 前走タイム指数 / ⑥ 巻き返し馬（prev_db使用）
        if prev_db is not None:
            hid = horse_id_map.get(num, "")
            prev = prev_db.get(hid, {}) if hid else {}

            # ⑤ 前走タイム指数
            try:
                prev_idx = float(prev.get("prev_idx", ""))
                if prev_idx >= 90:
                    score += SCORE_PREV_IDX_HIGH
                    breakdown.append({"label": f"前走指数{prev_idx:.0f}(90以上)", "pts": SCORE_PREV_IDX_HIGH})
                elif prev_idx >= 70:
                    score += SCORE_PREV_IDX_MID
                    breakdown.append({"label": f"前走指数{prev_idx:.0f}(70以上)", "pts": SCORE_PREV_IDX_MID})
            except (ValueError, TypeError):
                pass

            # ⑥ 巻き返し馬: 前走1-3番人気 かつ 前走4着以下
            try:
                prev_pop_val  = int(prev.get("prev_pop", 99))
                prev_rank_val = int(prev.get("prev_rank", 99))
                if prev_pop_val <= 3 and prev_rank_val >= 4:
                    score += SCORE_REVIVAL
                    breakdown.append({"label": f"巻き返し馬(前走{prev_pop_val}人気{prev_rank_val}着)", "pts": SCORE_REVIVAL})
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
        data_top_data = scrape_data_top(page, race_id, shutuba_data["horse_map"])

        browser.close()

    scored = score_horses(triple_horses, shutuba_data, data_top_data, prev_db=prev_db)

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
        "position_nums": list(shutuba_data["position_nums"]),
        "top3_hits": shutuba_data["top3_hits"],
        "pickup_nums": list(data_top_data["pickup_nums"]),
        "analysis_hits": data_top_data["analysis_hits"],
        "scored": scored,
        "advice": advice,
    }
