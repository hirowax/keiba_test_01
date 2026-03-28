#!/usr/bin/env python3
"""
netkeiba タイム指数スクレイパー
使い方: python scraper.py [YYYYMMDD]
"""

import sys
import json
import time
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

from dotenv import load_dotenv
import os
import pandas as pd
from openpyxl.styles import PatternFill
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup

YELLOW_FILL = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")

# ─── ログ設定 ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ─── 定数 ────────────────────────────────────────────────────
COOKIES_FILE = Path("cookies.json")
LOGIN_URL = "https://regist.netkeiba.com/account/?pid=login"
RACE_LIST_URL = "https://race.netkeiba.com/top/race_list.html?kaisai_date={date}"
SPEED_URL = "https://race.netkeiba.com/race/speed.html?race_id={race_id}&type=rank&mode={mode}"
MODES = {
    "average": "近走平均",
    "distance": "当該距離",
    "course": "当該コース",
}
ACCESS_INTERVAL = 3  # 秒


# ─── ユーティリティ ───────────────────────────────────────────
def load_env() -> tuple[str, str]:
    load_dotenv()
    email = os.getenv("NETKEIBA_EMAIL")
    password = os.getenv("NETKEIBA_PASSWORD")
    if not email or not password:
        raise ValueError(".env に NETKEIBA_EMAIL / NETKEIBA_PASSWORD が設定されていません")
    return email, password


def save_cookies(context) -> None:
    cookies = context.cookies()
    COOKIES_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
    logger.info(f"cookies を保存しました: {COOKIES_FILE}")


def load_cookies(context) -> bool:
    if not COOKIES_FILE.exists():
        return False
    cookies = json.loads(COOKIES_FILE.read_text())
    context.add_cookies(cookies)
    logger.info("既存の cookies を読み込みました")
    return True


def is_logged_in(page) -> bool:
    """ログイン済みかどうかをマイページリンクで確認"""
    page.goto("https://www.netkeiba.com/", wait_until="domcontentloaded")
    return page.locator("a[href*='mypage']").count() > 0 or \
           page.locator("a:has-text('ログアウト')").count() > 0


# ─── ログイン ─────────────────────────────────────────────────
def login(page, email: str, password: str) -> None:
    logger.info("ログインを試みます")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.fill("input[name='login_id']", email)
    page.fill("input[name='pswd']", password)
    page.click("input[type='submit'], button[type='submit']")
    try:
        page.wait_for_load_state("load", timeout=15000)
    except PlaywrightTimeoutError:
        pass
    time.sleep(2)
    logger.info("ログイン完了")


# ─── レース一覧取得 ───────────────────────────────────────────
def get_race_ids(page, date: str) -> List[dict]:
    """
    指定日の全レース情報を返す
    Returns: [{"race_id": "...", "label": "中山11R"}, ...]
    """
    url = RACE_LIST_URL.format(date=date)
    logger.info(f"レース一覧取得: {url}")
    page.goto(url, wait_until="domcontentloaded")

    # レース一覧テーブルが描画されるのを待つ
    try:
        page.wait_for_selector("dl.RaceList_DataList, .RaceList, a[href*='/race/result']", timeout=15000)
    except PlaywrightTimeoutError:
        logger.warning("レース一覧セレクタがタイムアウト。HTMLをそのままパース")

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    races = []
    seen = set()

    # race_id を href から抽出
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # /race/result.html?race_id=XXXXXX 形式
        m = re.search(r"race_id=(\d{12})", href)
        if not m:
            continue
        race_id = m.group(1)
        if race_id in seen:
            continue
        seen.add(race_id)

        # ラベル生成（競馬場名 + レース番号）
        label = _build_label(race_id, soup, a)
        races.append({"race_id": race_id, "label": label})

    logger.info(f"{len(races)} レースを検出")
    return races


def _build_label(race_id: str, soup: BeautifulSoup, a_tag) -> str:
    """race_id と周辺HTMLからラベル（例: 中山11R）を生成"""
    # race_id の構造: YYYYCCRRBB  (C=場コード, RR=回, BB=日, ?? = レース番号)
    # 実際: 12桁 = YYYY(4) + 場(2) + 回(1) + 日(1) + レース(2)
    venue_code = race_id[4:6]
    race_num_str = race_id[10:12].lstrip("0") or "1"

    venue_map = {
        "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
        "05": "東京", "06": "中山", "07": "中京", "08": "京都",
        "09": "阪神", "10": "小倉",
    }
    venue = venue_map.get(venue_code, f"場{venue_code}")
    return f"{venue}{race_num_str}R"


# ─── タイム指数テーブルパース ──────────────────────────────────
def parse_speed_table(page, race_id: str, mode: str) -> Optional[pd.DataFrame]:
    url = SPEED_URL.format(race_id=race_id, mode=mode)
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("table.Speed_Index_Table, table.RaceSpeed, .SpeedIndex, table", timeout=15000)
    except PlaywrightTimeoutError:
        logger.warning(f"テーブル待機タイムアウト: {url}")

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # テーブルを探す（複数候補）
    tables = soup.find_all("table")
    if not tables:
        logger.warning(f"テーブルが見つかりません: {url}")
        return None

    # 最も行数の多いテーブルを選択
    target = max(tables, key=lambda t: len(t.find_all("tr")))

    rows = target.find_all("tr")
    if len(rows) < 2:
        logger.warning(f"テーブル行数不足: {url}")
        return None

    # ヘッダー
    header_cells = rows[0].find_all(["th", "td"])
    headers = [c.get_text(strip=True) for c in header_cells]

    # データ行
    data = []
    for row in rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        data.append([c.get_text(strip=True) for c in cells])

    if not data:
        return None

    # 列数を揃える
    max_cols = max(len(headers), max(len(r) for r in data))
    headers += [f"col{i}" for i in range(len(headers), max_cols)]
    for row in data:
        row += [""] * (max_cols - len(row))

    df = pd.DataFrame(data, columns=headers[:max_cols])
    return df


# ─── Excel 書き出し ───────────────────────────────────────────
def venue_of(label: str) -> str:
    """'中山11R' → '中山'"""
    return re.sub(r"\d+R$", "", label)


def write_excel(
    all_data: Dict[str, Dict[str, Dict[str, Optional[pd.DataFrame]]]],
    all_summaries: Dict[str, Dict[str, pd.DataFrame]],
    date: str,
    triple_rows: List[dict],
) -> None:
    """
    all_data:      venue -> race_label -> mode_label -> df
    all_summaries: venue -> race_label -> summary_df
    triple_rows:   3指数重複馬リスト（黄色ハイライト用）
    """
    Path("output").mkdir(exist_ok=True)
    Path("summary").mkdir(exist_ok=True)

    # 古い個別CSVを削除（全場_3指数重複馬.csv は残す）
    old_dir = Path("output") / date
    if old_dir.exists():
        for f in old_dir.glob("*.csv"):
            if f.name != "全場_3指数重複馬.csv":
                f.unlink()

    # 黄色ハイライト対象の (開催場, レース番号, 馬番) セット
    triple_set = set(
        (r["開催場"], r["レース番号"], str(r["馬番"]).strip())
        for r in triple_rows
    )

    output_path = Path("output") / f"{date}.xlsx"
    summary_path = Path("summary") / f"{date}.xlsx"

    # ── タイム指数 Excel ──
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for venue, races in all_data.items():
            row_cursor = 0
            sheet_written = False
            for race_label, mode_dfs in races.items():
                race_num = race_label[len(venue):]  # "中山11R" → "11R"
                for mode_label, df in mode_dfs.items():
                    if df is None:
                        continue
                    # セクションヘッダー行
                    header_df = pd.DataFrame([[f"■ {race_label}  {mode_label}"]])
                    header_df.to_excel(
                        writer, sheet_name=venue,
                        startrow=row_cursor, index=False, header=False
                    )
                    row_cursor += 1
                    df_start = row_cursor
                    df.to_excel(
                        writer, sheet_name=venue,
                        startrow=df_start, index=False
                    )

                    # 馬番セルを黄色ハイライト
                    num_col_idx = next(
                        (i for i, c in enumerate(df.columns) if "馬番" in c), None
                    )
                    if num_col_idx is not None and triple_set:
                        ws = writer.sheets[venue]
                        for row_i, (_, row) in enumerate(df.iterrows()):
                            num_val = str(row.iloc[num_col_idx]).strip()
                            if (venue, race_num, num_val) in triple_set:
                                # openpyxl は1始まり、df_start+1=列ヘッダー行、df_start+2=データ1行目
                                ws.cell(
                                    row=df_start + 2 + row_i,
                                    column=num_col_idx + 1
                                ).fill = YELLOW_FILL

                    row_cursor += len(df) + 2  # データ + 空行
                    sheet_written = True
            if sheet_written:
                logger.info(f"[output] {venue} シート書き込み完了")

    logger.info(f"保存: {output_path}")

    # ── サマリー Excel ──
    with pd.ExcelWriter(summary_path, engine="openpyxl") as writer:
        for venue, races in all_summaries.items():
            row_cursor = 0
            sheet_written = False
            for race_label, summary_df in races.items():
                header_df = pd.DataFrame([[f"■ {race_label}"]])
                header_df.to_excel(
                    writer, sheet_name=venue,
                    startrow=row_cursor, index=False, header=False
                )
                row_cursor += 1
                summary_df.to_excel(
                    writer, sheet_name=venue,
                    startrow=row_cursor, index=False
                )
                row_cursor += len(summary_df) + 2
                sheet_written = True
            if sheet_written:
                logger.info(f"[summary] {venue} シート書き込み完了")

    logger.info(f"保存: {summary_path}")


# ─── サマリー生成 ─────────────────────────────────────────────
def _get_top5(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """指数列を特定してトップ5を返す"""
    # 「指数」列を探す（部分一致）
    idx_col = None
    for col in df.columns:
        if "指数" in col:
            idx_col = col
            break
    if idx_col is None:
        # 数値列の3列目付近を推定
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        if len(numeric_cols) >= 1:
            idx_col = numeric_cols[0]

    if idx_col is None:
        return None

    # 馬番・馬名列を探す
    horse_col = next((c for c in df.columns if "馬名" in c), None)
    num_col = next((c for c in df.columns if "馬番" in c), None)

    # 数値変換
    df = df.copy()
    df[idx_col] = pd.to_numeric(df[idx_col], errors="coerce")
    df = df.dropna(subset=[idx_col])
    df = df.sort_values(idx_col, ascending=False).head(5)

    cols = [c for c in [num_col, horse_col, idx_col] if c is not None]
    if not cols:
        return None
    return df[cols].reset_index(drop=True)


def build_summary(dfs: Dict[str, Optional[pd.DataFrame]], label: str) -> pd.DataFrame:
    """
    dfs: {"average": df, "distance": df, "course": df}
    """
    records = []

    # ① 各指数トップ5
    top5_all: Dict[str, Optional[pd.DataFrame]] = {}
    for mode_key, mode_label in MODES.items():
        df = dfs.get(mode_key)
        top5 = _get_top5(df) if df is not None else None
        top5_all[mode_key] = top5

        records.append({"セクション": f"【{mode_label} トップ5】", "馬番": "", "馬名": "", "近走平均": "", "当該距離": "", "当該コース": ""})
        if top5 is not None:
            for _, row in top5.iterrows():
                r = {"セクション": "", "馬番": "", "馬名": "", "近走平均": "", "当該距離": "", "当該コース": ""}
                for col in top5.columns:
                    if "馬番" in col:
                        r["馬番"] = row[col]
                    elif "馬名" in col:
                        r["馬名"] = row[col]
                    elif "指数" in col:
                        r[mode_label] = row[col]
                records.append(r)
        else:
            records.append({"セクション": "データなし", "馬番": "", "馬名": "", "近走平均": "", "当該距離": "", "当該コース": ""})

    # ② 3指数すべてでトップ5に入っている馬
    records.append({"セクション": "【3指数すべてトップ5入り馬】", "馬番": "", "馬名": "", "近走平均": "", "当該距離": "", "当該コース": ""})

    triple_horses = _find_triple_top5(dfs)
    if triple_horses:
        for entry in triple_horses:
            records.append({
                "セクション": "",
                "馬番": entry["馬番"],
                "馬名": entry["馬名"],
                "近走平均": entry.get("近走平均", ""),
                "当該距離": entry.get("当該距離", ""),
                "当該コース": entry.get("当該コース", ""),
            })
    else:
        records.append({"セクション": "該当なし", "馬番": "", "馬名": "", "近走平均": "", "当該距離": "", "当該コース": ""})

    return pd.DataFrame(records)


def find_triple_top5_rows(label: str, dfs: Dict[str, Optional[pd.DataFrame]]) -> List[dict]:
    """
    3指数すべてでトップ5（馬番一致）に入っている馬を返す。
    Returns: [{"開催場", "レース番号", "馬番", "馬名",
               "近走平均指数", "近走平均順位", "当該距離指数", "当該距離順位",
               "当該コース指数", "当該コース順位"}, ...]
    """
    venue = venue_of(label)
    race_num = re.sub(r"^[^\d]+", "", label)  # "中山11R" → "11R"

    # mode_key -> { 馬番: {馬名, 指数, 順位} }
    mode_info: Dict[str, Dict[str, dict]] = {}

    for mode_key, mode_label in MODES.items():
        df = dfs.get(mode_key)
        if df is None:
            return []

        # 列を特定
        idx_col = next((c for c in df.columns if "指数" in c), None)
        num_col = next((c for c in df.columns if "馬番" in c), None)
        horse_col = next((c for c in df.columns if "馬名" in c), None)

        if idx_col is None or num_col is None:
            return []

        work = df.copy()
        work[idx_col] = pd.to_numeric(work[idx_col], errors="coerce")
        work = work.dropna(subset=[idx_col])
        work = work.sort_values(idx_col, ascending=False).reset_index(drop=True)

        table = {}
        for rank_0, row in work.iterrows():
            num = str(row[num_col]).strip()
            if not num or rank_0 >= 5:  # トップ5のみ
                if rank_0 >= 5:
                    break
            table[num] = {
                "馬名": str(row[horse_col]).strip() if horse_col else "",
                "指数": row[idx_col],
                "順位": f"{rank_0 + 1}位",
            }
        mode_info[mode_key] = table

    # 馬番の積集合
    sets = [set(t.keys()) for t in mode_info.values()]
    common_nums = set.intersection(*sets) if sets else set()

    rows = []
    for num in sorted(common_nums, key=lambda x: int(x) if x.isdigit() else 0):
        avg = mode_info["average"][num]
        dist = mode_info["distance"][num]
        crs = mode_info["course"][num]
        rows.append({
            "開催場": venue,
            "レース番号": race_num,
            "馬番": num,
            "馬名": avg["馬名"] or dist["馬名"] or crs["馬名"],
            "近走平均指数": avg["指数"],
            "近走平均順位": avg["順位"],
            "当該距離指数": dist["指数"],
            "当該距離順位": dist["順位"],
            "当該コース指数": crs["指数"],
            "当該コース順位": crs["順位"],
        })
    return rows


def _find_triple_top5(dfs: Dict[str, Optional[pd.DataFrame]]) -> List[dict]:
    """3指数すべてでトップ5入りの馬を返す"""
    top5_names: dict[str, set[str]] = {}
    top5_data: dict[str, dict[str, dict]] = {}  # mode -> {馬名: row_dict}

    for mode_key, mode_label in MODES.items():
        df = dfs.get(mode_key)
        top5 = _get_top5(df) if df is not None else None
        if top5 is None:
            return []

        names = set()
        data = {}
        for _, row in top5.iterrows():
            name = None
            num = None
            idx_val = None
            for col in top5.columns:
                if "馬名" in col:
                    name = str(row[col])
                elif "馬番" in col:
                    num = str(row[col])
                elif "指数" in col:
                    idx_val = row[col]
            if name:
                names.add(name)
                data[name] = {"馬番": num, "馬名": name, mode_label: idx_val}
        top5_names[mode_key] = names
        top5_data[mode_key] = data

    # 積集合
    common = set.intersection(*top5_names.values()) if top5_names else set()
    result = []
    for name in common:
        entry = {"馬番": "", "馬名": name}
        for mode_key, mode_label in MODES.items():
            entry[mode_label] = top5_data[mode_key].get(name, {}).get(mode_label, "")
            if not entry["馬番"] and top5_data[mode_key].get(name, {}).get("馬番"):
                entry["馬番"] = top5_data[mode_key][name]["馬番"]
        result.append(entry)
    return result


# ─── メイン ───────────────────────────────────────────────────
def main():
    # 日付引数
    if len(sys.argv) >= 2:
        date = sys.argv[1]
    else:
        date = datetime.now().strftime("%Y%m%d")
    logger.info(f"対象日: {date}")

    email, password = load_env()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        # ログイン処理
        cookie_loaded = load_cookies(context)
        if cookie_loaded and is_logged_in(page):
            logger.info("cookies でログイン済みを確認")
        else:
            login(page, email, password)
            save_cookies(context)

        # レース一覧取得
        races = get_race_ids(page, date)
        if not races:
            logger.error("レースが見つかりませんでした。終了します。")
            browser.close()
            return

        # venue -> race_label -> mode_label -> df
        all_data: Dict[str, Dict] = {}
        # venue -> race_label -> summary_df
        all_summaries: Dict[str, Dict] = {}
        # 全場 3指数重複馬の行リスト
        triple_rows: List[dict] = []

        for race in races:
            race_id = race["race_id"]
            label = race["label"]
            venue = venue_of(label)
            logger.info(f"処理中: {label} ({race_id})")

            dfs: Dict[str, Optional[pd.DataFrame]] = {}

            for mode_key, mode_label in MODES.items():
                try:
                    df = parse_speed_table(page, race_id, mode_key)
                    dfs[mode_key] = df
                    if df is None:
                        logger.warning(f"{label} {mode_label}: データなし")
                except Exception as e:
                    logger.error(f"{label} {mode_label} 取得失敗: {e}")
                    dfs[mode_key] = None
                finally:
                    time.sleep(ACCESS_INTERVAL)

            # mode_label をキーに変換して蓄積
            all_data.setdefault(venue, {})[label] = {
                MODES[k]: v for k, v in dfs.items()
            }

            # サマリー生成
            try:
                summary_df = build_summary(dfs, label)
                all_summaries.setdefault(venue, {})[label] = summary_df
            except Exception as e:
                logger.error(f"{label} サマリー生成失敗: {e}")

            # 3指数重複馬チェック
            try:
                rows = find_triple_top5_rows(label, dfs)
                if rows:
                    logger.info(f"{label}: 3指数重複馬 {len(rows)}頭")
                    triple_rows.extend(rows)
            except Exception as e:
                logger.error(f"{label} 3指数重複馬チェック失敗: {e}")

        browser.close()

        # Excel 一括出力
        write_excel(all_data, all_summaries, date, triple_rows)

        # 全場 3指数重複馬 CSV 出力
        out_dir = Path("output") / date
        out_dir.mkdir(parents=True, exist_ok=True)
        triple_path = out_dir / "全場_3指数重複馬.csv"
        if triple_rows:
            pd.DataFrame(triple_rows).to_csv(triple_path, index=False, encoding="utf-8-sig")
            logger.info(f"保存: {triple_path} ({len(triple_rows)}行)")
        else:
            logger.info("3指数重複馬: 該当レースなし")

        logger.info("完了")


if __name__ == "__main__":
    main()
