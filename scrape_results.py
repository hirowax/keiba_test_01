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
import os
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
from scraper import human_sleep, human_browse, load_cookies, login, load_env, COOKIES_FILE, get_race_ids, is_ip_blocked

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

RESULT_URL = "https://race.netkeiba.com/race/result.html?race_id={race_id}"
HORSE_PAGE_URL = "https://db.netkeiba.com/horse/{horse_id}/"
PEDIGREE_FILE = OUTPUT_DIR / "horse_pedigree.json"


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


def parse_place_odds(soup) -> dict:
    """払い戻し表から複勝オッズを取得 → {馬番: 複勝オッズ}
    netkeiba の複勝表示: 馬番は個別セル（7, 12, 13）、
    金額は1つのセルに連結（"240円110円170円"）、その後に人気（5人気1人気2人気）。
    """
    result = {}

    # ページ全テキストから複勝〜次賭式の区間を抽出
    full_text = soup.get_text("\n")
    m = re.search(
        r"複勝\s*([\s\S]*?)(?:枠連|馬連|ワイド|馬単|3連複|3連単)",
        full_text
    )
    if not m:
        return result

    segment = m.group(1)

    # 金額が最初に出現する位置より前の部分から馬番を抽出
    # → 人気数字（"1人気"の"1"等）との衝突を回避
    first_yen = re.search(r"\d{2,5}円", segment)
    pre_amounts = segment[:first_yen.start()] if first_yen else segment

    nums = re.findall(r"(?<!\d)(\d{1,2})(?!\d)", pre_amounts)
    # 金額: XXX円 の形式（複数連結されている場合も含む）を抽出
    amounts = re.findall(r"(\d{2,5})円", segment)

    for num, amount in zip(nums, amounts):
        try:
            result[num] = round(int(amount) / 100, 1)
        except ValueError:
            pass

    return result


def fetch_horse_pedigree(page, horse_id: str) -> dict:
    """馬ページの血統表から父・母父を取得"""
    url = HORSE_PAGE_URL.format(horse_id=horse_id)
    try:
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(random.uniform(2.0, 4.0))
    except PlaywrightTimeoutError:
        log.warning(f"血統取得タイムアウト: {horse_id}")
        return {}

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    result = {}

    blood_table = soup.find("table", class_="blood_table")
    if not blood_table:
        log.warning(f"血統表が見つかりません: {horse_id}")
        return result

    # blood_table 構造: rowspan=2 の td が 父(1番目)・母(2番目)
    # 同じ tr 内の rowspan なし td が 父の父(父の行) / 母の父(母の行)
    rowspan2_tds = blood_table.find_all("td", attrs={"rowspan": "2"})
    if rowspan2_tds:
        a = rowspan2_tds[0].find("a")
        if a:
            result["sire"] = a.get_text(strip=True)
    if len(rowspan2_tds) >= 2:
        # 母と同じ tr にある rowspan なし td = 母の父 (dam's sire)
        dam_tr = rowspan2_tds[1].parent
        for td in dam_tr.find_all("td"):
            if not td.has_attr("rowspan"):
                a = td.find("a")
                if a:
                    result["dam_sire"] = a.get_text(strip=True)
                break

    return result


def scrape_race_result(page, race_id: str, race_label: str) -> Optional[dict]:
    """1レースの結果をスクレイプ"""
    url = RESULT_URL.format(race_id=race_id)
    try:
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(random.uniform(3.0, 7.0))
    except PlaywrightTimeoutError:
        log.warning(f"タイムアウト: {race_id}")
        return None

    return parse_result_html(page.content(), race_label)


# 着順が数字でない行の書き方（2026-11-02 に実ページで確認したのは「取消」だけ。
# 除外・中止・失格は netkeiba の表記として想定したもの。これ以外は「不明」として元の文字を残す）
NON_FINISH_STATUSES = ("取消", "除外", "中止", "失格")


def _corner_cells(tr):
    """コーナー通過順のセル。実ページ（tests/fixtures/result_202605041106_torikeshi.html）で td.PassageRate と確認（2026-11-12）。
    クラス名を明示して取る（位置やあいまいな正規表現で探さない）。見つかったセルをすべて返す（2個以上は形の異常。2026-11-13〜）"""
    return tr.find_all("td", class_="PassageRate")


def parse_result_html(html: str, race_label: str = "") -> dict:
    """結果ページの HTML を {"condition", "horses"(着順が数字の馬), "others"(着順が数字でない馬), "problems"} にする。
    2026-11-02 まで、着順が数字でない行（取消・除外・中止・失格）は捨てていた（docs/index_top3_definition_202611.md）。
    problems（2026-11-12〜）: ページの形の異常。空でなければ、結果の取得はそのレースを「取得の失敗」として扱い、
    前に保存した値に上書きしない。
    - 成績の表 table#All_Result_Table がちょうど1つでない
    - 行が見出し（tr.Header で td が無い）でも馬の行（tr.HorseList）でもない
    - 馬の行の要素（着順 td.Result_Num・枠番 td.Num.Waku{n}・馬番 td.Num.Txt_C・馬名 td.Horse_Info の /horse/ リンク）が
      ちょうど1つでない・読めない（2026-11-13 まで、馬番・馬名・枠番・着順は列の位置で読んでいた。値は72ページで同じと確かめた）
    - 着順のある行に通過順のセル td.PassageRate が無い・どの行でも td.PassageRate が2個以上ある（2026-11-13〜。それまでは最初の1つを読んでいた）
    - 馬の行の数が、ページの「N頭」（.RaceData02）と違う・「N頭」が無い
    - 馬番・馬IDの重複、馬IDが無い馬、着順のある馬が0頭
    実ページ（2026-11-12 取得の72ページ）の成績の表の行は、tr.Header と tr.HorseList（取消は Torikeshi も付く）だけだった"""
    soup = BeautifulSoup(html, "html.parser")

    condition = parse_race_condition(soup)
    place_odds_map = parse_place_odds(soup)

    horses = []
    others = []  # 着順が数字でない馬（取消・除外・中止・失格・不明）
    problems = []

    tables = soup.find_all("table", id="All_Result_Table")
    rows = []
    if len(tables) != 1:
        problems.append("成績の表が無い" if not tables else f"成績の表が {len(tables)} 個ある")
    else:
        rows = tables[0].find_all("tr")

    n_horse_rows = 0
    for tr in rows:
        cls = tr.get("class") or []
        tds = tr.find_all("td")
        if "Header" in cls and not tds:
            continue
        if "HorseList" not in cls:
            problems.append(f"知らない形の行がある（class={cls}）")
            continue
        n_horse_rows += 1

        def one(sel, what):
            found = tr.select(sel)
            if len(found) != 1:
                problems.append(f"馬の行の{what}（{sel}）が {len(found)} 個（{n_horse_rows} 行目）")
                return None
            return found[0]

        rank_cell = one("td.Result_Num", "着順")
        gate_cell = one('td.Num[class*="Waku"]', "枠番")
        num_cell = one("td.Num.Txt_C", "馬番")
        name_links = [a for td in tr.select("td.Horse_Info") for a in td.find_all("a", href=re.compile(r"/horse/\d+"))]
        if len(name_links) != 1:
            problems.append(f"馬の行の馬名のリンクが {len(name_links)} 個（{n_horse_rows} 行目）")
        if rank_cell is None or gate_cell is None or num_cell is None or len(name_links) != 1:
            continue

        # 着順。数字でない行は others に分けて残す
        rank_text = re.sub(r"\s+", "", rank_cell.get_text())
        finished = bool(re.match(r"^\d{1,2}$", rank_text))
        rank = int(rank_text) if finished else None
        gate = _text(gate_cell)
        num = _text(num_cell)
        a = name_links[0]
        name = re.sub(r"\s+", "", a.get_text())
        horse_id = re.search(r"/horse/(\d+)", a.get("href", "")).group(1)

        if not name or not num.isdigit():
            log.warning(f"  {race_label}: 着順「{rank_text}」の行の馬番・馬名が読めない")
            problems.append(f"馬番・馬名が読めない行がある（着順「{rank_text}」）")
            continue

        corner_cells = _corner_cells(tr)
        if len(corner_cells) > 1:
            problems.append(f"馬の行の通過順（td.PassageRate）が {len(corner_cells)} 個（{num}番）")
            continue
        corner_cell = corner_cells[0] if corner_cells else None
        corners_text = _text(corner_cell) if corner_cell else None
        corners = parse_corner_order(corners_text) if corners_text else []

        if not finished:
            status = rank_text if rank_text in NON_FINISH_STATUSES else "不明"
            if status == "不明":
                log.warning(f"  {race_label}: 着順の書き方が未知「{rank_text}」（{num}番 {name}）")
            others.append({"status": status, "rank_text": rank_text, "gate": gate,
                           "num": num, "name": name, "horse_id": horse_id,
                           "corners_text": corners_text, "corners": corners})
            continue
        if corner_cell is None:
            problems.append(f"着順のある行に通過順のセル（td.PassageRate）が無い（{num}番）")

        # 性齢: 専用クラスがなく td.Horse_Info.Txt_C（馬名セルは Horse_Info のみ）
        sex_age = ""
        sa_cell = tr.find("td", class_=re.compile(r"[Bb]arei|Sex|sex_age")) or tr.select_one("td.Horse_Info.Txt_C")
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

        # 上がり3F: 後3Fセルに専用クラスはなく class "Time"（ハイライト時 BgBlue02 等が付く）。
        # 行内の Time セル群の末尾（タイム→着差→後3F の順）を取り、妥当範囲のみ採用
        last3f = 0.0
        time_cells = [td for td in tds if "Time" in (td.get("class") or [])]
        if time_cells:
            v = _float(_text(time_cells[-1]))
            if 25.0 <= v <= 50.0:
                last3f = v

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
            "place_odds":    place_odds_map.get(num),
            "last3f":        last3f,
            "corners":       corners,
            "corners_text":  corners_text,
            "horse_weight":  hw,
            "horse_weight_diff": hw_diff,
        })

    if not horses:
        problems.append("着順のある馬が0頭")
    # 頭数: ページの「N頭」と馬の行の数（取消・除外を含む）
    d2 = soup.find(class_="RaceData02")
    m = re.search(r"(\d+)頭", d2.get_text(" ", strip=True)) if d2 else None
    if not m:
        problems.append("ページの「N頭」の表示が無い")
    elif int(m.group(1)) != n_horse_rows:
        problems.append(f"馬の行の数 {n_horse_rows} がページの「{m.group(1)}頭」と違う")
    everyone = horses + others
    nums = [h["num"] for h in everyone]
    ids = [h["horse_id"] for h in everyone]
    if len(set(nums)) != len(nums):
        problems.append(f"馬番が重複している: {sorted(n for n in set(nums) if nums.count(n) > 1)}")
    if not all(ids):
        problems.append("馬IDが無い馬がいる")
    elif len(set(ids)) != len(ids):
        problems.append("馬IDが重複している")
    condition["num_horses"] = len(horses)
    log.info(f"  {race_label}: {len(horses)}頭 馬場={condition.get('condition','?')} "
             f"距離={condition.get('distance','?')}m 天気={condition.get('weather','?')}"
             + (f" 着順なし={[(o['num'], o['status']) for o in others]}" if others else ""))

    return {"condition": condition, "horses": horses, "others": others, "problems": problems}


SCRAPE_VERSION = "全レース_要素を明示_通過順のセルも1つ_頭数と重複の照合_形の異常は取得の失敗_レースごとに置き換え_20261113b"
RESULTS_ALL_NAME = "race_results_all.json"
FETCH_LOG_NAME = "results_fetch_log.json"
MAX_CONSECUTIVE_FAIL = 5          # rescrape_results_full.py と同じ（IPブロックなどを疑って止める）
RETRY_SCHEDULE_DAYS = 14          # 予定を確定できなかった日の、JRA 公式の一覧だけの取り直しを試す範囲


def _now_iso() -> str:
    from prev_inputs import now_jst
    return now_jst().isoformat(timespec="seconds")


def _load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json_atomic(path: Path, data, indent=2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)   # 書けなかったときは元のファイルのまま。一時ファイルを残さない
        raise


def refresh_jra_lists(date: str, cache: dict) -> None:
    """JRA 公式の結果の一覧（月ごとのページ → 開催場・日のページ → 各レース）を取り直してキャッシュに足す。
    scrape_jra_payouts.py の関数とキャッシュ（output/jra_cname_cache.json）を使う。失敗は例外"""
    import scrape_jra_payouts as J
    month_cname, reached = J.crawl_month_chain(date[:6], cache)
    if month_cname is None:
        raise RuntimeError(f"JRA 公式の {date[:6]} の月ごとのページに着けない（最古 {reached}）")
    J.find_srl_links_for_month(month_cname, cache)
    keys = sorted(k for k in cache.get("srl", {}) if k.startswith(f"{date}_"))
    if not keys:
        raise RuntimeError(f"JRA 公式に {date} の開催場・日のページが無い")
    for k in keys:
        J.find_sde_links_for_day(cache["srl"][k]["cname"], cache)
    J.save_cache(cache)


def schedule_check_for(date: str, race_id_map: dict, cache: dict, jra_error, extra_reason=None) -> dict:
    from schedule_check import day_schedule, UNCONFIRMED
    sc = day_schedule(date, race_id_map, cache)
    sc["jra_checked_at"] = _now_iso()
    sc["jra_error"] = jra_error
    if jra_error and sc["status"] != UNCONFIRMED:
        sc.update(status=UNCONFIRMED, reason=f"JRA 公式の一覧を取り直せなかった: {jra_error}")
    if extra_reason:
        sc.update(status=UNCONFIRMED, reason=extra_reason)
    return sc


def merge_all_results(existing: dict, date: str, race_id_map: dict, fetched: dict) -> tuple:
    """race_results_all.json をレースごとに置き換える（計画 2.1 の 2026-11-12 変更）。
    今回取れたレースだけ、着順・着順の無い馬・条件を一組で置き換え、取れなかったレースは前の値を残す。
    戻り値 (新しい中身, {前の値を残したレース: 前の fetched_at})"""
    data = existing or {"date": date, "complete": False, "races": {}}
    data["date"] = date
    data["race_ids"] = dict(race_id_map)
    races = data.setdefault("races", {})
    kept_old = {}
    for label, rid in race_id_map.items():
        if label in fetched:
            races[label] = fetched[label]
        elif label in races:
            kept_old[label] = races[label].get("fetched_at")
    # complete: 今回の取得の成否ではなく、保存済みのレースの集合が一覧とそろっていること
    data["complete"] = set(races) == set(race_id_map)
    return data, kept_old


def append_fetch_log(out_dir: Path, attempt: dict) -> Path:
    path = out_dir / FETCH_LOG_NAME
    log_data = _load_json(path, {"attempts": []})
    log_data.setdefault("attempts", []).append(attempt)
    _write_json_atomic(path, log_data)
    return path


def retry_unconfirmed_schedules(today: str, cache: dict, output_dir: Path = OUTPUT_DIR, errors: list = None) -> list:
    """前の開催日で予定を確定できなかった日（schedule_check がある日だけ）について、JRA 公式の一覧だけを取り直す
    （netkeiba にはアクセスしない）。戻り値は書き換えたファイルのパス。
    日ごとに予期しない例外を受け、errors に「日付: 理由」を足して次の日へ進む（2026-11-13〜。それまでは例外で止まり、
    呼び出し元が CHANGED_FILE: を出せず、cron が保存済みのファイルを commit しなかった）。書き換えた後の例外でも、そのファイルは戻り値に入る"""
    from datetime import timedelta
    from schedule_check import CONFIRMED
    changed = []
    if errors is None:
        errors = []
    start = (datetime.strptime(today, "%Y%m%d") - timedelta(days=RETRY_SCHEDULE_DAYS)).strftime("%Y%m%d")
    for path in sorted(output_dir.glob(f"*/{RESULTS_ALL_NAME}")):
        d = path.parent.name
        if not (re.fullmatch(r"\d{8}", d) and start <= d < today):
            continue
        try:
            data = _load_json(path, {})
            sc = data.get("schedule_check")
            if not sc or sc.get("status") == CONFIRMED:
                continue
            err = None
            try:
                refresh_jra_lists(d, cache)
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            data["schedule_check"] = schedule_check_for(d, data.get("race_ids") or {}, cache, err)
            _write_json_atomic(path, data, indent=1)
            changed.append(path)
            logp = append_fetch_log(path.parent, {"at": _now_iso(), "scrape_version": SCRAPE_VERSION,
                                                  "kind": "JRA 公式の一覧だけ取り直し", "jra_error": err,
                                                  "schedule_status": data["schedule_check"]["status"]})
            changed.append(logp)
            log.info(f"{d}: JRA 公式の一覧を取り直した → {data['schedule_check']['status']}"
                     f"（{data['schedule_check'].get('reason')}）")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{d}: {type(e).__name__}: {e}")
            log.error(f"{d}: JRA 公式の一覧の取り直しで予期しない例外: {type(e).__name__}: {e}")
    return changed


class BlockedError(Exception):
    """IPブロック・アクセス拒否のページを検知した（その時点で追加の取得を止める）"""


def fetch_one_race(page, race_id: str, race_label: str) -> dict:
    """1レースの結果ページへ1回だけ移動して読む（2026-11-13〜。それまでは human_browse と scrape_race_result で
    同じページへ2回移動していた）。中間ページを経由することはある（human_browse。約35%）。
    ブロックを検知したら BlockedError。移動・読み取りの例外はそのまま上へ（呼び出し元がレース単位で失敗として扱う）"""
    human_browse(page, RESULT_URL.format(race_id=race_id))
    time.sleep(random.uniform(3.0, 7.0))
    if is_ip_blocked(page):
        raise BlockedError(f"IPブロック・アクセス拒否のページを検知（{page.url}）")
    return parse_result_html(page.content(), race_label)


def fetch_races(page, race_id_map: dict, attempt: dict, fetched: dict = None) -> dict:
    """全レースを取る。失敗はレース単位で attempt["failed"] に理由付きで残し、続ける。
    連続 MAX_CONSECUTIVE_FAIL 回の失敗・ブロックの検知では止め、残りを「止めたので取っていない」にする。
    取れたレース（形の異常が無いものだけ）は、呼び出し元から渡された辞書 fetched に1レースずつ足す
    （2026-11-13〜。途中で予期しない例外が出ても、呼び出し元に取れた分が残る。それまでは戻り値だけで、例外で失っていた）。
    戻り値も同じ辞書"""
    if fetched is None:
        fetched = {}
    fails = 0
    for label, race_id in sorted(race_id_map.items()):
        log.info(f"スクレイプ中: {label} ({race_id})")
        t = _now_iso()
        try:
            result = fetch_one_race(page, race_id, label)
            if result["problems"]:
                attempt["failed"][label] = "形の異常: " + "・".join(result["problems"])
        except BlockedError as e:
            attempt["failed"][label] = str(e)
            attempt["stopped"] = f"{e} → 追加の取得を止めた"
            log.error(attempt["stopped"])
            break
        except Exception as e:  # noqa: BLE001  移動のタイムアウトなどもレース単位の失敗にする
            attempt["failed"][label] = f"取れない: {type(e).__name__}: {e}"
        if label in attempt["failed"]:
            fails += 1
            log.warning(f"  {label}: 取得の失敗（連続 {fails}）: {attempt['failed'][label]}")
            if fails >= MAX_CONSECUTIVE_FAIL:
                attempt["stopped"] = f"連続 {fails} 回の失敗で止めた（IPブロックなどを疑う）"
                log.error(attempt["stopped"])
                break
            human_sleep(10.0, 20.0)
            continue
        fails = 0
        result["condition"]["race_id"] = race_id
        fetched[label] = {"race_id": race_id, "condition": result["condition"], "horses": result["horses"],
                          "others": result["others"], "fetched_at": t, "scrape_version": SCRAPE_VERSION}
        attempt["fetched"].append(label)
        human_sleep(4.0, 9.0)
    for label in sorted(race_id_map):
        if label not in fetched and label not in attempt["failed"]:
            attempt["failed"][label] = "止めたので取っていない"
    return fetched


def fetch_pedigrees(page, fetched: dict, sub_labels: set, pedigree_cache: dict, attempt: dict) -> None:
    """血統（採点したレースの馬で、キャッシュにない馬のみ）。止めた回は取らない"""
    new_horse_ids = sorted({h["horse_id"] for label, r in fetched.items() if label in sub_labels
                            for h in r["horses"] if h["horse_id"] and h["horse_id"] not in pedigree_cache})
    if not new_horse_ids or attempt["stopped"]:
        return
    log.info(f"血統取得: {len(new_horse_ids)}頭（初回は時間がかかります）")
    consecutive_fail = 0
    for i, horse_id in enumerate(new_horse_ids, 1):
        ped = fetch_horse_pedigree(page, horse_id)
        if is_ip_blocked(page):
            attempt["stopped"] = "血統の取得中に IPブロック・アクセス拒否のページを検知 → 止めた"
            log.error(attempt["stopped"])
            break
        if ped:
            pedigree_cache[horse_id] = ped
            consecutive_fail = 0
        else:
            consecutive_fail += 1
            if consecutive_fail >= 10:
                log.error(f"⚠️  血統取得が{consecutive_fail}連続で失敗。ブロックを疑い中断します")
                break
        if i % 20 == 0:
            with open(PEDIGREE_FILE, "w", encoding="utf-8") as f:
                json.dump(pedigree_cache, f, ensure_ascii=False, indent=2)
            log.info(f"  血統 {i}/{len(new_horse_ids)}頭 取得・キャッシュ保存")
        human_sleep(2.0, 5.0)


def _write_save_marker(out_dir: Path, state: str, errors=None) -> None:
    from data_quality import RESULTS_SAVE_MARKER
    _write_json_atomic(out_dir / RESULTS_SAVE_MARKER, {"state": state, "at": _now_iso(), "scrape_version": SCRAPE_VERSION,
                                                       "errors": list(errors or [])})


SCORED_FILE_NAMES = ("race_results.json", "race_conditions.json", "race_nonfinishers.json")
RESULT_KEYS = ("rank", "num", "name", "horse_id", "pop", "odds", "place_odds", "jockey", "trainer",
               "weight_carried", "horse_weight", "horse_weight_diff", "time", "margin", "last3f",
               "corners", "sex_age", "gate")


def save_results(date: str, out_dir: Path, race_id_map: dict, fetched: dict, attempt: dict, scored_labels,
                 pedigree_cache: dict, cache: dict, jra_error) -> dict:
    """取れた分を保存する（途中で止まった回も必ず通す）。戻り値は race_results_all.json の中身。
    race_results_all.json を今回取れたレースで置き換え、採点したレースの3ファイルは保存済みの全レースの結果から作る
    （save_scored_set。印を置く→保存→読み直して一致を確かめる→印を消す）。
    印を置けない・race_results_all.json を書けないときは例外（何も書き換えていない）。3ファイルの失敗は attempt["save_errors"] に残す。
    取得の記録（results_fetch_log.json）だけの失敗は、結果の一式には関わらないので印に関わらない（save_errors には残す）"""
    extra = None
    if scored_labels and scored_labels - set(race_id_map):
        extra = f"採点したレースがレース一覧に無い: {sorted(scored_labels - set(race_id_map))}"
    all_path = out_dir / RESULTS_ALL_NAME
    data, kept_old = merge_all_results(_load_json(all_path, None), date, race_id_map, fetched)
    data["schedule_check"] = schedule_check_for(date, race_id_map, cache, jra_error, extra)
    attempt["kept_old"] = kept_old
    attempt["complete"] = data["complete"]
    attempt["schedule_status"] = data["schedule_check"]["status"]
    attempt["schedule_reason"] = data["schedule_check"].get("reason")
    attempt["save_errors"] = []
    save_scored_set(out_dir, data, scored_labels, pedigree_cache, attempt, write_all=True)
    try:
        append_fetch_log(out_dir, attempt)
    except Exception as e:  # noqa: BLE001
        attempt["save_errors"].append(f"取得の記録（{FETCH_LOG_NAME}）を保存できない: {type(e).__name__}: {e}")
        log.error(attempt["save_errors"][-1])
    log.info(f"保存完了: {all_path}（complete={data['complete']}・予定 {data['schedule_check']['status']}）")
    log.info(f"取得レース数: {len(fetched)} / {len(race_id_map)}")
    return data


def _scored_entry(r: dict, pedigree_cache: dict) -> tuple:
    """race_results_all.json の1レースから、3ファイルのそのレースの値 (結果, 条件, 着順の無い馬)"""
    results = [{k: h[k] for k in RESULT_KEYS}
               | {"sire": pedigree_cache.get(h["horse_id"], {}).get("sire", ""),
                  "dam_sire": pedigree_cache.get(h["horse_id"], {}).get("dam_sire", "")}
               for h in r["horses"]]
    return results, r["condition"], r["others"]


def build_scored_set(out_dir: Path, all_data: dict, scored_labels, pedigree_cache: dict) -> tuple:
    """採点したレースの3ファイルの、書くべき中身を作る（書かない）。戻り値 (中身 {ファイル名: dict}, 作り直せたレース, 作り直せないレース)。
    作り直せるのは、race_results_all.json にこの取得処理（fetch_races）で保存した値（scrape_version がある）があるレースだけ。
    作り直せない採点したレースは、今のファイルの値を残す（2026-11-12 より前の取り方の値など）"""
    races = all_data.get("races") or {}
    sub_labels = scored_labels if scored_labels is not None else set(all_data.get("race_ids") or {})
    bodies = {n: _load_json(out_dir / n, {}) for n in SCORED_FILE_NAMES}
    rebuilt, not_rebuilt = [], []
    for label in sorted(sub_labels):
        r = races.get(label)
        if r and r.get("scrape_version"):
            vals = _scored_entry(r, pedigree_cache)
            for n, v in zip(SCORED_FILE_NAMES, vals):
                bodies[n][label] = v
            rebuilt.append(label)
        elif any(label in b for b in bodies.values()):
            not_rebuilt.append(label)
    return bodies, rebuilt, not_rebuilt


def save_scored_set(out_dir: Path, all_data: dict, scored_labels, pedigree_cache: dict, attempt: dict,
                    write_all: bool) -> bool:
    """通常の保存と作り直し（--rebuild-scored）で共通の手順（2026-11-13〜）:
    1. 「保存が不完全」の印を置く（置けなければ何も書かずに例外）
    2. write_all なら race_results_all.json を書く（書けなければ例外。前の回の印が無ければ印も消す。何も書き換えていない）
    3. 保存済みの全レースの結果（all_data）から3ファイルを作って書く
    4. 3ファイルを読み直し、書くべき中身と一致するか確かめる
    5. 一致し、かつ前の回の印があった日は作り直せないレースが3ファイルに残っていないときだけ、印を消す
    戻り値は印を消せたか。失敗の理由は attempt["save_errors"]、印にも入れる"""
    from data_quality import RESULTS_SAVE_MARKER
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = out_dir / RESULTS_SAVE_MARKER
    marker_before = marker.exists()
    _write_save_marker(out_dir, "保存中（途中で止まった）")
    if write_all:
        try:
            _write_json_atomic(out_dir / RESULTS_ALL_NAME, all_data, indent=1)
        except BaseException:
            if not marker_before:
                marker.unlink(missing_ok=True)
            raise
    errors = attempt.setdefault("save_errors", [])
    state = None
    try:
        bodies, rebuilt, not_rebuilt = build_scored_set(out_dir, all_data, scored_labels, pedigree_cache)
        attempt["scored_rebuilt_from_all_results"] = rebuilt
        attempt["kept_old_scored_files"] = not_rebuilt
        written = []
        for n in SCORED_FILE_NAMES:
            try:
                _write_json_atomic(out_dir / n, bodies[n])
            except Exception as e:
                raise RuntimeError(f"{n} を書けない（書けたもの: {written or 'なし'}）: {type(e).__name__}: {e}") from e
            written.append(n)
        differ = [n for n in SCORED_FILE_NAMES if _load_json(out_dir / n, None) != bodies[n]]
        if differ:
            state = "書いた後に読み直すと中身が違う"
            errors.append(f"採点したレースのファイルを読み直すと中身が違う: {differ}")
        elif marker_before and not_rebuilt:
            state = "前の回の保存が不完全で、作り直せないレースが残る"
            errors.append(f"前の回の保存が不完全で、{RESULTS_ALL_NAME} から作り直せないレースが残る: {not_rebuilt}")
    except Exception as e:  # noqa: BLE001
        state = "採点したレースのファイルの一部だけが新しい可能性"
        errors.append(f"採点したレースのファイル（race_results.json など）を保存できない: {type(e).__name__}: {e}")
    if state is None:
        try:
            marker.unlink(missing_ok=True)
            return True
        except Exception as e:  # noqa: BLE001
            errors.append(f"保存が不完全の印を消せない（読み手はその日の結果を使わない）: {type(e).__name__}: {e}")
            return False
    log.error(f"{out_dir.name}: {state}: {errors[-1]}")
    try:
        _write_save_marker(out_dir, state, errors)
    except Exception:  # noqa: BLE001  書けなくても「保存中」の印が残っている
        pass
    return False


def rebuild_scored_files(date: str, output_dir: Path = OUTPUT_DIR) -> dict:
    """保存済みの race_results_all.json から、採点したレースの3ファイルを作り直す（取得しない）。
    通常の保存と同じ手順（save_scored_set）で、一致を確かめてから印を消す。消せなければ 1 で終わる。戻り値は取得の記録に足した中身"""
    out_dir = output_dir / date
    data = _load_json(out_dir / RESULTS_ALL_NAME, None)
    if not data or not isinstance(data.get("races"), dict):
        raise SystemExit(f"{date}: {RESULTS_ALL_NAME} が無い・形が違う → 作り直せない（取り直す）")
    pickup = _load_json(out_dir / "pickup_scores.json", None)
    scored_labels = ({l for l, r in pickup.get("races", {}).items() if r.get("race_id")} if pickup else None)
    pedigree_cache = _load_json(PEDIGREE_FILE, {})
    attempt = {"at": _now_iso(), "scrape_version": SCRAPE_VERSION,
               "kind": f"採点したレースのファイルを {RESULTS_ALL_NAME} から作り直し", "save_errors": []}
    ok = save_scored_set(out_dir, data, scored_labels, pedigree_cache, attempt, write_all=False)
    attempt["marker_cleared"] = ok
    append_fetch_log(out_dir, attempt)
    if ok:
        log.info(f"{date}: 採点したレースのファイルを作り直した（{len(attempt['scored_rebuilt_from_all_results'])}レース）。保存が不完全の印は無い")
    else:
        log.error(f"{date}: 作り直したが、保存が不完全の印を消せない: {attempt['save_errors']}")
    return attempt


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--rebuild-scored":
        att = rebuild_scored_files(sys.argv[2])
        print(f"CHANGED_FILE: {(OUTPUT_DIR / sys.argv[2]).resolve().relative_to(BASE_DIR.resolve())}")
        if not att["marker_cleared"]:
            print(f"RESULTS_FETCH_INCOMPLETE: {sys.argv[2]} 作り直したが保存が不完全の印が残る: {' / '.join(att['save_errors'])}")
            sys.exit(1)
        return
    date = sys.argv[1] if len(sys.argv) >= 2 else datetime.now().strftime("%Y%m%d")
    out_dir = OUTPUT_DIR / date
    pickup_path = out_dir / "pickup_scores.json"

    # 採点したレース（race_results.json などに入れるレース）。全レースは race_results_all.json に入れる
    scored_labels = None
    if pickup_path.exists():
        with open(pickup_path, encoding="utf-8") as f:
            pickup = json.load(f)
        scored_labels = {label for label, rdata in pickup.get("races", {}).items() if rdata.get("race_id")}
        log.info(f"pickup_scores.json の採点したレース: {len(scored_labels)}")

    # 血統キャッシュ（永続・馬IDをキーに父・母父を保存）
    pedigree_cache: dict = {}
    if PEDIGREE_FILE.exists():
        with open(PEDIGREE_FILE, encoding="utf-8") as f:
            pedigree_cache = json.load(f)
        log.info(f"血統キャッシュ: {len(pedigree_cache)}頭")

    load_env()
    attempt = {"at": _now_iso(), "scrape_version": SCRAPE_VERSION, "kind": "結果の取得",
               "fetched": [], "failed": {}, "kept_old": {}, "stopped": None}
    fetched, race_id_map = {}, {}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
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
                sys.exit(1)
            page.goto("https://www.netkeiba.com/", wait_until="domcontentloaded")
            human_sleep(1.0, 2.0)

            # レースIDは、採点ファイルがあってもレース一覧から全レースを取る（設計 2章）
            races = get_race_ids(page, date)
            if is_ip_blocked(page):
                print(f"RESULTS_FETCH_INCOMPLETE: {date} レース一覧で IPブロック・アクセス拒否のページを検知 → 何も取らずに止めた")
                sys.exit(1)
            race_id_map = {r["label"]: r["race_id"] for r in races}
            log.info(f"レース一覧ページから {len(race_id_map)} レース取得")
            if not race_id_map:
                log.error("レースが見つかりません")
                sys.exit(1)

            try:
                fetch_races(page, race_id_map, attempt, fetched)   # 途中の例外でも、取れた分は fetched に残る
                sub_labels = scored_labels if scored_labels is not None else set(race_id_map)
                fetch_pedigrees(page, fetched, sub_labels, pedigree_cache, attempt)
            except Exception as e:  # noqa: BLE001  予期しない例外でも、取れた分は下で保存する
                attempt["stopped"] = f"予期しない例外で止めた: {type(e).__name__}: {e}"
                log.error(attempt["stopped"])
                for label in race_id_map:
                    if label not in fetched and label not in attempt["failed"]:
                        attempt["failed"][label] = "止めたので取っていない"
        finally:
            browser.close()

    # JRA 公式の結果の一覧と照合（予定の確定。設計 2章）
    import scrape_jra_payouts as J
    cache = J.load_cache()
    jra_error = None
    try:
        refresh_jra_lists(date, cache)
    except Exception as e:  # noqa: BLE001
        jra_error = f"{type(e).__name__}: {e}"
        log.error(f"JRA 公式の一覧を取れない: {jra_error}")

    try:
        data = save_results(date, out_dir, race_id_map, fetched, attempt, scored_labels, pedigree_cache, cache, jra_error)
    except Exception as e:  # noqa: BLE001  元のファイルのまま。下で知らせる
        data = None
        attempt["save_errors"] = [f"{RESULTS_ALL_NAME} を保存できない（前のファイルのまま）: {type(e).__name__}: {e}"]
        log.error(attempt["save_errors"][0])

    # 保存の後の処理は、例外が出ても止めずに知らせる（2026-11-13〜）。保存したファイルの CHANGED_FILE: の行は先に出す
    # （cron は scrape_results.py が 0 以外で終わると commit しないので、保存の後は 0 で終わり RESULTS_FETCH_INCOMPLETE で知らせる）
    def changed_file(pth):
        print(f"CHANGED_FILE: {Path(pth).resolve().relative_to(BASE_DIR.resolve())}", flush=True)
    changed_file(out_dir)
    changed_file(J.CACHE_PATH)
    after_save_errors = list(attempt.get("save_errors") or [])
    try:
        with open(PEDIGREE_FILE, "w", encoding="utf-8") as f:
            json.dump(pedigree_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        after_save_errors.append(f"血統のキャッシュを保存できない: {type(e).__name__}: {e}")

    # 前の開催日で予定を確定できなかった日の、JRA 公式の一覧だけの取り直し
    retry_errors = []
    try:
        for pth in retry_unconfirmed_schedules(date, cache, errors=retry_errors):
            changed_file(pth)
    except Exception as e:  # noqa: BLE001
        retry_errors.append(f"{type(e).__name__}: {e}")
    after_save_errors += [f"前の開催日の JRA 公式の一覧の取り直し {e}" for e in retry_errors]
    for e in after_save_errors:
        log.error(e)

    # 知らせ: 今回の取得の失敗・止めた・予定を確定できない（complete: true でも残す）
    from schedule_check import CONFIRMED
    sc = data["schedule_check"] if data else {"status": "保存できないので照合の結果なし", "reason": None}
    if attempt["failed"] or attempt["stopped"] or sc["status"] != CONFIRMED or after_save_errors:
        print(f"RESULTS_FETCH_INCOMPLETE: {date} 取れた {len(fetched)}/{len(race_id_map)}・"
              f"失敗 {len(attempt['failed'])}・予定 {sc['status']}"
              + (f"・止めた: {attempt['stopped']}" if attempt["stopped"] else "")
              + (f"（{sc.get('reason')}）" if sc.get('reason') else "")
              + (f"・保存とその後の失敗: {' / '.join(after_save_errors)}" if after_save_errors else ""))

    # データ品質チェック（cronログで気づけるように WARNING を出す）
    all_horses = [h for r in fetched.values() for h in r["horses"]]
    if all_horses:
        if sum(1 for h in all_horses if h.get("corners")) == 0:
            log.warning(f"⚠️  コーナー通過順が全馬空です（掲載前の可能性）。後日 scrape_results.py {date} で再取得を推奨")
        if sum(1 for h in all_horses if h.get("last3f")) == 0:
            log.warning(f"⚠️  上がり3Fが全馬空です。ページ構造変更の可能性を確認してください")
    # 取れたレースが0件でも、取得の記録は保存したので 0 で終わる（cron が commit し、上の印で知らせる）


if __name__ == "__main__":
    main()
