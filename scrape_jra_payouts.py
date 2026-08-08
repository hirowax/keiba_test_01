"""
JRA公式サイト（jra.go.jp）からレース払戻金を取得する。

使い方:
    python3 scrape_jra_payouts.py YYYYMMDD    # 1日分
    python3 scrape_jra_payouts.py --all       # race_results.jsonがあり payouts_jra.json がない全日付（--max-days件まで）
    python3 scrape_jra_payouts.py --probe     # 月別ページを202510まで遡れるか確認
    python3 scrape_jra_payouts.py --verify    # 既知の払戻値と照合

重要: JRA公式のcnameパラメータ末尾のチェックサム（CS）は計算式が未解明。
絶対に自前で生成せず、実際に取得したページからリンクとして収集したものだけを使うこと。
誤ったCSでアクセスすると「パラメータエラー」ページが返る。

対象日は事前に scrape_results.py で netkeiba 側のレース情報（race_conditions.json）を
取得済みであることが前提（そこから各レースの race_id を取得し、JRA側は該当cnameを探すだけにする）。
"""
import argparse
import json
import logging
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
CACHE_PATH = OUTPUT_DIR / "jra_cname_cache.json"

BASE_URL = "https://www.jra.go.jp/JRADB/accessS.html"
ENTRY_CNAME = "pw01skl00999999/B3"
PROBE_TARGET_MONTH = "202510"

# scraper.py:226-231 と同一の場コード対応
VENUE_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
    "05": "東京", "06": "中山", "07": "中京", "08": "京都",
    "09": "阪神", "10": "小倉",
}

# <li class="..."> → 出力キー（2026-08-08に実HTML確認済み: refund_area内のli要素）
BET_CLASS_MAP = {
    "win": "tansho",
    "place": "fukusho",
    "wakuren": "wakuren",
    "wide": "wide",
    "umaren": "umaren",
    "umatan": "umatan",
    "trio": "trio",
    "tierce": "tierce",
}
# 着順のまま保持する（馬番昇順にソートしない）券種
UNSORTED_BET_TYPES = {"umatan", "tierce"}

SKL_PATTERN = re.compile(r"pw01skl(?:00|10)\d{6}/[0-9A-Fa-f]{2}")
SRL_PATTERN = re.compile(r"pw01srl(?:00|10)\d{18}/[0-9A-Fa-f]{2}")
SDE_PATTERN = re.compile(r"pw01sde\d{22}/[0-9A-Fa-f]{2}")

VERIFY_CASES = [
    ("20260404", "阪神10R", "umaren", "900"),
    ("20260404", "阪神10R", "wide", "320"),
    ("20260125", "中山6R", "wide", "1230"),
    ("20260328", "中京9R", "wide", "1380"),
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ---------- HTTP ----------

def polite_sleep():
    time.sleep(random.uniform(1.0, 2.0))


def fetch_post(cname: str) -> str:
    polite_sleep()
    r = requests.post(BASE_URL, data={"cname": cname}, timeout=20)
    r.raise_for_status()
    return r.content.decode("shift_jis", errors="replace")


def is_param_error_page(html: str) -> bool:
    m = re.search(r"<title>(.*?)</title>", html)
    return bool(m and "パラメータエラー" in m.group(1))


def extract_links(html: str, pattern: re.Pattern) -> list:
    return sorted(set(pattern.findall(html)))


# ---------- cname キャッシュ ----------

def load_cache() -> dict:
    if CACHE_PATH.exists():
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ---------- cname パース ----------

def month_key(cname: str):
    body = cname.split("/")[0]
    if body.startswith("pw01skl00"):
        return body[len("pw01skl00"):]
    if body.startswith("pw01skl10"):
        return body[len("pw01skl10"):]
    return None


def parse_srl_components(cname: str):
    body = cname.split("/")[0]
    if body.startswith("pw01srl00"):
        digits = body[len("pw01srl00"):]
    elif body.startswith("pw01srl10"):
        digits = body[len("pw01srl10"):]
    else:
        raise ValueError(f"srl形式ではありません: {cname}")
    vv, yyyy, kk, dd, yyyymmdd = digits[0:2], digits[2:6], digits[6:8], digits[8:10], digits[10:18]
    return vv, yyyy, kk, dd, yyyymmdd


def parse_sde_components(cname: str):
    body = cname.split("/")[0]
    digits = body[len("pw01sde"):]
    vv, yyyy, kk, dd, rr, yyyymmdd = (
        digits[2:4], digits[4:8], digits[8:10], digits[10:12], digits[12:14], digits[14:22]
    )
    race_id = f"{yyyy}{vv}{kk}{dd}{rr}"
    return race_id, yyyymmdd, vv, rr


# ---------- BFSクロール ----------

def crawl_month_chain(target_yyyymm: str, cache: dict):
    """月別ページを遡り target_yyyymm の cname を探す。見つからなければ (None, 到達できた最古月) を返す。"""
    monthly = cache.setdefault("monthly", {})
    if target_yyyymm in monthly:
        return monthly[target_yyyymm]["cname"], target_yyyymm

    if not monthly:
        html = fetch_post(ENTRY_CNAME)
        for link in extract_links(html, SKL_PATTERN):
            ym = month_key(link)
            if ym and ym != "999999" and ym not in monthly:
                monthly[ym] = {"cname": link}
        save_cache(cache)

    queue = list(monthly.keys())
    visited = set()
    oldest = min(monthly.keys()) if monthly else None
    fetch_count = 0

    while queue:
        ym = queue.pop(0)
        if ym in visited:
            continue
        visited.add(ym)
        if oldest is None or ym < oldest:
            oldest = ym
        if ym == target_yyyymm:
            return monthly[ym]["cname"], ym

        html = fetch_post(monthly[ym]["cname"])
        fetch_count += 1
        new_found = False
        for link in extract_links(html, SKL_PATTERN):
            lym = month_key(link)
            if lym and lym != "999999" and lym not in monthly:
                monthly[lym] = {"cname": link}
                queue.append(lym)
                new_found = True
        if new_found and fetch_count % 5 == 0:
            save_cache(cache)

    save_cache(cache)
    return None, oldest


def find_srl_links_for_month(month_cname: str, cache: dict) -> None:
    srl_cache = cache.setdefault("srl", {})
    html = fetch_post(month_cname)
    changed = False
    for link in extract_links(html, SRL_PATTERN):
        vv, yyyy, kk, dd, yyyymmdd = parse_srl_components(link)
        key = f"{yyyymmdd}_{vv}"
        if key not in srl_cache:
            srl_cache[key] = {"cname": link}
            changed = True
    if changed:
        save_cache(cache)


def find_sde_links_for_day(srl_cname: str, cache: dict) -> None:
    sde_cache = cache.setdefault("sde", {})
    html = fetch_post(srl_cname)
    changed = False
    for link in extract_links(html, SDE_PATTERN):
        race_id, yyyymmdd, vv, rr = parse_sde_components(link)
        if race_id not in sde_cache:
            sde_cache[race_id] = {"cname": link, "date": yyyymmdd}
            changed = True
    if changed:
        save_cache(cache)


def ensure_sde_cname(date: str, vv: str, race_id: str, cache: dict) -> str:
    sde_cache = cache.setdefault("sde", {})
    if race_id in sde_cache:
        return sde_cache[race_id]["cname"]

    srl_cache = cache.setdefault("srl", {})
    srl_key = f"{date}_{vv}"
    if srl_key not in srl_cache:
        month_cname, reached = crawl_month_chain(date[:6], cache)
        if month_cname is None:
            raise RuntimeError(f"{date[:6]} の月別ページに到達できません（最古到達: {reached}）")
        find_srl_links_for_month(month_cname, cache)

    if srl_key not in srl_cache:
        raise RuntimeError(f"{date} {vv}場 の日別ページが見つかりません")

    find_sde_links_for_day(srl_cache[srl_key]["cname"], cache)

    if race_id not in sde_cache:
        raise RuntimeError(f"{race_id} のsdeリンクが見つかりません")
    return sde_cache[race_id]["cname"]


# ---------- 払戻パース ----------

def normalize_combo(bet_type: str, num_text: str) -> str:
    if "-" not in num_text:
        return num_text
    parts = num_text.split("-")
    if bet_type in UNSORTED_BET_TYPES:
        return "-".join(parts)
    return "-".join(sorted(parts, key=lambda x: int(x)))


def parse_payouts(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("div", class_="refund_area")
    if container is None:
        return {}

    result = {}
    for li in container.find_all("li"):
        classes = li.get("class") or []
        bet_type = next((BET_CLASS_MAP[c] for c in classes if c in BET_CLASS_MAP), None)
        if bet_type is None:
            if li.find("div", class_="num") and li.find("div", class_="yen"):
                log.warning(f"未知の払戻クラス: {classes}")
            continue

        combos = {}
        for line in li.find_all("div", class_="line"):
            num_div = line.find("div", class_="num")
            yen_div = line.find("div", class_="yen")
            if num_div is None or yen_div is None:
                continue
            num_text = num_div.get_text(strip=True)
            yen_digits = re.sub(r"[^\d]", "", yen_div.get_text(strip=True))
            if not num_text or not yen_digits:
                # 出走頭数が少ない場合など、該当券種が発売されず欄が空になることがある
                continue
            combo = normalize_combo(bet_type, num_text)
            combos[combo] = int(yen_digits)
        if combos:
            result[bet_type] = combos
    return result


# ---------- 対象日レース一覧（netkeiba側データから） ----------

def load_known_races(date: str) -> dict:
    cond_path = OUTPUT_DIR / date / "race_conditions.json"
    if cond_path.exists():
        with open(cond_path, encoding="utf-8") as f:
            cond = json.load(f)
        races = {label: info["race_id"] for label, info in cond.items() if "race_id" in info}
        if races:
            return races

    res_path = OUTPUT_DIR / date / "race_results.json"
    if res_path.exists():
        with open(res_path, encoding="utf-8") as f:
            res = json.load(f)
        races = {}
        for label, horses in res.items():
            if isinstance(horses, list) and horses and "race_id" in horses[0]:
                races[label] = horses[0]["race_id"]
        if races:
            return races

    raise SystemExit(
        f"対象日 {date} の race_conditions.json / race_results.json が見つかりません。"
        f"先に python3 scrape_results.py {date} を実行してください。"
    )


# ---------- 日付単位ドライバ ----------

def scrape_date(date: str, cache: dict) -> dict:
    known = load_known_races(date)
    payouts = {}
    consecutive_fail = 0

    for label, race_id in known.items():
        vv = race_id[4:6]
        try:
            sde_cname = ensure_sde_cname(date, vv, race_id, cache)
            html = fetch_post(sde_cname)
            if is_param_error_page(html):
                raise RuntimeError("パラメータエラーページが返された")
            result = parse_payouts(html)
        except Exception as e:
            log.warning(f"{label}: 払戻取得失敗 ({e})")
            consecutive_fail += 1
            if consecutive_fail >= 5:
                log.warning(f"{date}: 連続{consecutive_fail}件失敗のため以降のレースを中断")
                break
            continue

        consecutive_fail = 0
        if not result:
            log.warning(f"{label}: 払戻データが空です")
        payouts[label] = result

    return payouts


def write_payouts(date: str, payouts: dict) -> None:
    out_dir = OUTPUT_DIR / date
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "payouts_jra.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payouts, f, ensure_ascii=False, indent=2)
    log.info(f"保存: {path} ({len(payouts)}レース)")
    for label, d in payouts.items():
        if not d:
            log.warning(f"{date} {label}: 払戻データが空です")


# ---------- プローブ・検証 ----------

def run_probe(cache: dict) -> bool:
    cname, reached = crawl_month_chain(PROBE_TARGET_MONTH, cache)
    if cname:
        log.info(f"プローブ成功: {PROBE_TARGET_MONTH} まで到達可能（さらに遡れる場合あり）")
        return True
    log.warning(f"プローブ: {PROBE_TARGET_MONTH} に到達できませんでした。到達できた最古月: {reached}")
    return False


def run_verify(cache: dict) -> bool:
    dates_needed = sorted({c[0] for c in VERIFY_CASES})
    results_by_date = {}
    for date in dates_needed:
        payouts_path = OUTPUT_DIR / date / "payouts_jra.json"
        if payouts_path.exists():
            with open(payouts_path, encoding="utf-8") as f:
                results_by_date[date] = json.load(f)
        else:
            payouts = scrape_date(date, cache)
            write_payouts(date, payouts)
            results_by_date[date] = payouts

    all_pass = True
    for date, label, bet_type, expected_yen in VERIFY_CASES:
        combos = results_by_date.get(date, {}).get(label, {}).get(bet_type, {})
        actual_values = [str(v) for v in combos.values()]
        ok = str(expected_yen) in actual_values
        log.info(f"[{'PASS' if ok else 'FAIL'}] {date} {label} {bet_type}: 期待{expected_yen}円 実際{combos}")
        all_pass = all_pass and ok
    return all_pass


# ---------- CLI ----------

def main():
    parser = argparse.ArgumentParser(description="JRA公式サイトからレース払戻金を取得する")
    parser.add_argument("date", nargs="?", default=None, help="対象日 YYYYMMDD（省略時は今日）")
    parser.add_argument("--all", action="store_true", help="race_results.jsonがあり payouts_jra.json がない日付を処理")
    parser.add_argument("--probe", action="store_true", help=f"{PROBE_TARGET_MONTH}まで遡れるか確認")
    parser.add_argument("--verify", action="store_true", help="既知の払戻値と照合する")
    parser.add_argument("--max-days", type=int, default=3, help="--all実行時の1回あたり処理日数上限（デフォルト3）")
    args = parser.parse_args()

    cache = load_cache()

    if args.probe:
        run_probe(cache)
        save_cache(cache)
        return

    if args.verify:
        ok = run_verify(cache)
        save_cache(cache)
        log.info("検証結果: " + ("全件PASS" if ok else "一部FAIL"))
        sys.exit(0 if ok else 1)

    if args.all:
        candidates = sorted(
            d.name for d in OUTPUT_DIR.iterdir()
            if d.is_dir() and d.name.isdigit()
            and (d / "race_results.json").exists()
            and not (d / "payouts_jra.json").exists()
        )
        target = candidates[: args.max_days]
        log.info(f"対象 {len(candidates)}日 中 {len(target)}日を処理します")
        for date in target:
            try:
                payouts = scrape_date(date, cache)
                write_payouts(date, payouts)
            except Exception as e:
                log.error(f"{date}: 処理失敗 ({e})")
        save_cache(cache)
        remaining = len(candidates) - len(target)
        log.info(f"完了: {len(target)}日処理。残り{remaining}日 — 再実行してください")
        return

    date = args.date or datetime.now().strftime("%Y%m%d")
    payouts = scrape_date(date, cache)
    write_payouts(date, payouts)
    save_cache(cache)


if __name__ == "__main__":
    main()
