#!/usr/bin/env python3
"""
GitHub Pages 用 JSON エクスポート
- output/{date}/triple.json    (全場_3指数重複馬.csv → JSON)
- summary/{date}.json          (YYYYMMDD.xlsx → JSON)
- output/dates.json            (利用可能な日付一覧)

usage:
  python3 export_json.py 20260404   # 特定の日付
  python3 export_json.py            # 全日付（初回セットアップ）
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
SUMMARY_DIR = BASE_DIR / "summary"


def export_triple(date: str):
    csv_path = OUTPUT_DIR / date / "全場_3指数重複馬.csv"
    out_path = OUTPUT_DIR / date / "triple.json"
    if not csv_path.exists():
        print(f"  [triple] スキップ（CSV なし）: {csv_path}")
        return
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    records = df.to_dict(orient="records")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False)
    print(f"  [triple] {len(records)}頭 → {out_path.name}")


def export_summary(date: str):
    xlsx_path = SUMMARY_DIR / f"{date}.xlsx"
    out_path = SUMMARY_DIR / f"{date}.json"
    if not xlsx_path.exists():
        print(f"  [summary] スキップ（Excel なし）: {xlsx_path}")
        return

    xl = pd.ExcelFile(xlsx_path)
    result = {}

    for venue in xl.sheet_names:
        df = xl.parse(venue, header=None)
        races = []
        current_race = None
        current_section = None

        for _, row in df.iterrows():
            cell0 = str(row[0]) if pd.notna(row[0]) else ""

            if cell0.startswith("■"):
                if current_race:
                    races.append(current_race)
                race_label = cell0[1:].strip()
                current_race = {
                    "label": race_label,
                    "average": [],
                    "distance": [],
                    "course": [],
                    "triple": [],
                }
                current_section = None
                continue

            if current_race is None:
                continue

            if "近走平均" in cell0 and "トップ5" in cell0:
                current_section = "average"; continue
            elif "当該距離" in cell0 and "トップ5" in cell0:
                current_section = "distance"; continue
            elif "当該コース" in cell0 and "トップ5" in cell0:
                current_section = "course"; continue
            elif "3指数すべて" in cell0:
                current_section = "triple"; continue
            elif cell0 in ("セクション", "該当なし", "データなし"):
                continue

            num = str(row[1]).strip() if pd.notna(row[1]) else ""
            name = str(row[2]).strip() if pd.notna(row[2]) else ""
            if not name or name == "nan":
                continue

            v_avg  = row[3] if pd.notna(row[3]) else ""
            v_dist = row[4] if pd.notna(row[4]) else ""
            v_crs  = row[5] if pd.notna(row[5]) else ""

            entry = {"num": num, "name": name,
                     "avg": v_avg, "dist": v_dist, "crs": v_crs}

            if current_section == "average":
                entry["val"] = v_avg; current_race["average"].append(entry)
            elif current_section == "distance":
                entry["val"] = v_dist; current_race["distance"].append(entry)
            elif current_section == "course":
                entry["val"] = v_crs; current_race["course"].append(entry)
            elif current_section == "triple":
                current_race["triple"].append(entry)

        if current_race:
            races.append(current_race)

        result[venue] = races

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"  [summary] {len(result)}場 → {out_path.name}")


def update_dates_json():
    dates = sorted(
        [f.stem for f in OUTPUT_DIR.glob("*.xlsx")
         if re.match(r"^\d{8}$", f.stem)],
        reverse=True,
    )
    out_path = OUTPUT_DIR / "dates.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dates, f, ensure_ascii=False)
    print(f"  [dates] {dates} → {out_path.name}")


def main():
    if len(sys.argv) >= 2:
        dates = [sys.argv[1]]
    else:
        # 全日付
        dates = sorted(
            [f.stem for f in OUTPUT_DIR.glob("*.xlsx")
             if re.match(r"^\d{8}$", f.stem)]
        )

    for date in dates:
        print(f"▶ {date}")
        export_triple(date)
        export_summary(date)

    update_dates_json()
    print("✅ JSON エクスポート完了")


if __name__ == "__main__":
    main()
