#!/usr/bin/env python3
"""
レース結果を包括的にスクレイプして保存するスクリプト。
レース当日の夜〜翌日に実行する。

保存先:
  output/{date}/race_results.json    全馬着順・人気・馬体重等（calibrate_threshold.pyと互換）
  output/{date}/race_conditions.json レース条件（馬場・天気・距離・クラス・頭数）

usage:
  python3 scrape_results.py 20260404
  python3 scrape_results.py          # 今日の日付
"""

import json
import logging
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from typing import Optional
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# scraper.py の共通ユーティリティを流用
from scraper import human_sleep, human_browse, load_cookies, login, load_env, COOKIES_FILE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

RESULT_URL = "https://race.netkeiba.com/race/result.html?race_id={race_id}"


def _text(el) -> str:
    return el.get_text(strip=True) if el else ""


def _int(s: str) -> int:
    m = re.search(r"-?\d+", str(s).replace(",", ""))
    return int(m.group()) if m else 0


def _float(s: str) -> float:
    m = re.search(r"[\d.]+", str(s))
    return float(m.group()) if m else 0.0


def parse_race_condition(soup) -> dict:
    """RaceData01 / RaceData02 からレース条件を取得"""
    info = {}

    d1 = soup.find(class_="RaceData01")
    if d1:
        text = d1.get_text(" ", strip=True)
        # 距離・馬場種別
        m = re.search(r"(\d{3,4})m", text)
        if m:
            info["distance"] = int(m.group(1))
        if "芝" in text:
            info["surface"] = "芝"
        elif "ダート" in text or "ダ" in text:
            info["surface"] = "ダート"
        elif "障" in text:
            info["surface"] = "障害"
        # 天気
        m = re.search(r"天気[:：]\s*(\S+)", text)
        if m:
            info["weather"] = m.group(1)
        # 馬場状態
        m = re.search(r"馬場[:：]\s*(\S+)", text)
        if m:
            info["condition"] = m.group(1)
        # 良/稍重/重/不良 直接パターン
        for cond in ["不良", "稍重", "重", "良"]:
            if cond in text and "condition" not in info:
                info["condition"] = cond
                break

    d2 = soup.find(class_="RaceData02")
    if d2:
        text = d2.get_text(" ", strip=True)
        info["class_name"] = text[:60]  # クラス名（長い場合は切る）

    # ペース (S/M/H)
    pace_el = soup.find(class_="RapPace_Title")
    if pace_el:
        m = re.search(r"[SMH]", pace_el.get_text())
        if m:
            info["pace"] = m.group()

    # ラップタイム（200m区間）
    lap_table = soup.find("table", class_="Race_HaronTime")
    if lap_table:
        rows = lap_table.find_all("tr")
        for tr in rows:
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            # 区間ラップ行: 小数点を含む短い数値が並ぶ
            if tds and all(re.match(r"^\d+\.\d$", t) for t in tds):
                try:
                    info["lap_times"] = [float(t) for t in tds]
                except ValueError:
                    pass
                break

    # 出走頭数はhorseリストから後で設定
    return info


def parse_corner_order(text: str) -> list:
    """'3-4-5-3' → [3,4,5,3] にパース"""
    parts = re.findall(r"\d+", str(text))
    return [int(p) for p in parts] if parts else []


def parse_horse_weight(text: str) -> tuple:
    """'456(-2)' → (456, -2)"""
    m = re.search(r"(\d+)\s*\(([+-]?\d+)\)", str(text))
    if m:
        return int(m.group(1)), int(m.group(2))
    m2 = re.search(r"(\d+)", str(text))
    if m2:
        return int(m2.group(1)), 0
    return 0, 0


def scrape_race_result(page, race_id: str, race_label: str) -> Optional[dict]:
    """1レースの結果をスクレイプ"""
    url = RESULT_URL.format(race_id=race_id)
    try:
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(random.uniform(3.0, 7.0))
    except PlaywrightTimeoutError:
        log.warning(f"タイムアウト: {race_id}")
        return None

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    condition = parse_race_condition(soup)

    horses = []

    # メイン結果テーブル（.HorseList）
    table = soup.find("table", class_=re.compile(r"HorseList"))
    if not table:
        # フォールバック: <tbody>内の<tr>を探す
        table = soup.find("div", id="All_Result_Table") or soup.find("div", class_="ResultTableWrap")

    rows = []
    if table:
        rows = table.find_all("tr")
    else:
        # 最後の手段: page内の全trからそれらしい行を収集
        rows = soup.find_all("tr", class_=re.compile(r"HorseInfo|Horse\d"))

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue

        # 着順（数字でなければスキップ = ヘッダー行・取消等）
        rank_text = re.sub(r"\s+", "", tds[0].get_text())
        if not re.match(r"^\d{1,2}$", rank_text):
            continue
        rank = int(rank_text)

        # 枠番
        gate = _text(tds[1]) if len(tds) > 1 else ""

        # 馬番
        num_cell = tr.find("td", class_=re.compile(r"[Uu]maban|HorseNum|Num2"))
        num = _text(num_cell) if num_cell else (_text(tds[2]) if len(tds) > 2 else "")

        # 馬名 + horse_id
        name = ""
        horse_id = ""
        name_cell = tr.find("td", class_=re.compile(r"HorseName|horse_name"))
        if not name_cell:
            name_cell = tds[3] if len(tds) > 3 else None
        if name_cell:
            a = name_cell.find("a", href=re.compile(r"/horse/"))
            if a:
                name = re.sub(r"\s+", "", a.get_text())
                m = re.search(r"/horse/(\d+)", a.get("href", ""))
                if m:
                    horse_id = m.group(1)
            else:
                name = re.sub(r"\s+", "", name_cell.get_text())

        if not name or not num.isdigit():
            continue

        # 性齢
        sex_age = ""
        sa_cell = tr.find("td", class_=re.compile(r"[Bb]arei|Sex|sex_age"))
        if sa_cell:
            sex_age = _text(sa_cell)

        # 斤量: Jockey_Info クラス（騎手と別セル）、なければ数値パターンで探す
        weight_carried = 0.0
        wc_cell = tr.find("td", class_="Jockey_Info")
        if wc_cell:
            weight_carried = _float(_text(wc_cell))
        else:
            for td in tds:
                t = _text(td)
                if re.match(r"^\d{2}\.\d$", t):
                    weight_carried = float(t)
                    break

        # 騎手: class="Jockey" のリンクテキスト（Jockey_Info は斤量なので除外）
        jockey = ""
        for td in tr.find_all("td"):
            cls = td.get("class", [])
            if "Jockey" in cls and "Jockey_Info" not in cls:
                j_a = td.find("a")
                jockey = _text(j_a or td)
                break

        # 調教師
        trainer = ""
        t_cell = tr.find("td", class_=re.compile(r"[Tt]rainer"))
        if t_cell:
            trainer = _text(t_cell.find("a") or t_cell)

        # タイム
        time_text = ""
        time_cell = tr.find("td", class_=re.compile(r"[Tt]ime|Result_Time"))
        if time_cell:
            time_text = _text(time_cell)

        # 着差
        margin = ""
        margin_cell = tr.find("td", class_=re.compile(r"[Mm]argin|Chakusa"))
        if margin_cell:
            margin = _text(margin_cell)

        # 人気: class="Odds Txt_C" または "Odds BgYellow Txt_C"
        pop = 0
        for td in tr.find_all("td"):
            cls = td.get("class", [])
            if "Odds" in cls and "Txt_C" in cls:
                pop = _int(_text(td))
                break

        # 単勝オッズ: class="Odds Txt_R"
        odds = 0.0
        for td in tr.find_all("td"):
            cls = td.get("class", [])
            if "Odds" in cls and "Txt_R" in cls:
                odds = _float(_text(td))
                break

        # 上がり3F
        last3f = 0.0
        l3f_cell = tr.find("td", class_=re.compile(r"[Ll]ast3[Ff]|Agari|agari"))
        if l3f_cell:
            last3f = _float(_text(l3f_cell))

        # コーナー通過順
        corners = []
        corner_cell = tr.find("td", class_=re.compile(r"[Cc]orner|[Pp]assage"))
        if corner_cell:
            corners = parse_corner_order(_text(corner_cell))

        # 馬体重・増減
        hw, hw_diff = 0, 0
        hw_cell = tr.find("td", class_=re.compile(r"[Hh]orse[Ww]eight|Weight"))
        if hw_cell:
            hw, hw_diff = parse_horse_weight(_text(hw_cell))
        # フォールバック: テキストに "456(-2)" パターン
        if hw == 0:
            for td in reversed(tds):
                t = _text(td)
                if re.search(r"\d{3}\([+-]?\d+\)", t):
                    hw, hw_diff = parse_horse_weight(t)
                    break

        horses.append({
            "rank":          rank,
            "gate":          gate,
            "num":           num,
            "name":          name,
            "horse_id":      horse_id,
            "sex_age":       sex_age,
            "weight_carried": weight_carried,
            "jockey":        jockey,
            "trainer":       trainer,
            "time":          time_text,
            "margin":        margin,
            "pop":           pop,
            "odds":          odds,
            "last3f":        last3f,
            "corners":       corners,
            "horse_weight":  hw,
            "horse_weight_diff": hw_diff,
        })

    condition["num_horses"] = len(horses)
    log.info(f"  {race_label}: {len(horses)}頭 馬場={condition.get('condition','?')} "
             f"距離={condition.get('distance','?')}m 天気={condition.get('weather','?')}")

    return {"condition": condition, "horses": horses}


def main():
    date = sys.argv[1] if len(sys.argv) >= 2 else datetime.now().strftime("%Y%m%d")
    out_dir = OUTPUT_DIR / date
    pickup_path = out_dir / "pickup_scores.json"

    if not pickup_path.exists():
        log.error(f"pickup_scores.json が見つかりません: {pickup_path}")
        sys.exit(1)

    with open(pickup_path, encoding="utf-8") as f:
        pickup = json.load(f)

    # race_id マップを pickup から取得
    race_id_map = {
        label: rdata["race_id"]
        for label, rdata in pickup.get("races", {}).items()
        if rdata.get("race_id")
    }

    if not race_id_map:
        log.error("pickup_scores.json に race_id がありません")
        sys.exit(1)

    log.info(f"対象レース数: {len(race_id_map)}")

    email, password = load_env()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        if not load_cookies(context):
            log.error("❌ cookies.json が見つかりません。")
            log.error("   → python3 save_cookies.py を実行してログインしてください")
            browser.close()
            sys.exit(1)
        page.goto("https://www.netkeiba.com/", wait_until="domcontentloaded")
        human_sleep(1.0, 2.0)

        race_results = {}    # calibrate_threshold.py 互換: {label: [{rank,num,name,...}]}
        race_conditions = {} # レース条件: {label: {distance, surface, condition, ...}}

        for label, race_id in sorted(race_id_map.items()):
            log.info(f"スクレイプ中: {label} ({race_id})")
            human_browse(page, RESULT_URL.format(race_id=race_id))
            time.sleep(random.uniform(2.0, 5.0))

            result = scrape_race_result(page, race_id, label)
            if not result:
                log.warning(f"  {label}: 取得失敗")
                continue

            horses = result["horses"]
            condition = result["condition"]
            condition["race_id"] = race_id

            # calibrate_threshold.py 互換形式（rank/num/name を保持）
            race_results[label] = [
                {
                    "rank":              h["rank"],
                    "num":               h["num"],
                    "name":              h["name"],
                    "horse_id":          h["horse_id"],
                    "pop":               h["pop"],
                    "odds":              h["odds"],
                    "jockey":            h["jockey"],
                    "trainer":           h["trainer"],
                    "weight_carried":    h["weight_carried"],
                    "horse_weight":      h["horse_weight"],
                    "horse_weight_diff": h["horse_weight_diff"],
                    "time":              h["time"],
                    "margin":            h["margin"],
                    "last3f":            h["last3f"],
                    "corners":           h["corners"],
                    "sex_age":           h["sex_age"],
                    "gate":              h["gate"],
                }
                for h in horses
            ]
            race_conditions[label] = condition

            human_sleep(4.0, 9.0)

        browser.close()

    # 保存
    results_path = out_dir / "race_results.json"
    conditions_path = out_dir / "race_conditions.json"

    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(race_results, f, ensure_ascii=False, indent=2)
    with open(conditions_path, "w", encoding="utf-8") as f:
        json.dump(race_conditions, f, ensure_ascii=False, indent=2)

    log.info(f"保存完了: {results_path}")
    log.info(f"保存完了: {conditions_path}")
    log.info(f"取得レース数: {len(race_results)} / {len(race_id_map)}")


if __name__ == "__main__":
    main()
