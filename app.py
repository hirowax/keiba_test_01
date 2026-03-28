#!/usr/bin/env python3
"""netkeiba タイム指数 Web アプリ"""

from flask import Flask, render_template, jsonify, request
from pathlib import Path
import pandas as pd
import json
import re

app = Flask(__name__)
BASE_DIR = Path(__file__).parent


def get_available_dates():
    dates = []
    for f in sorted(BASE_DIR.glob("output/*.xlsx"), reverse=True):
        dates.append(f.stem)
    return dates


def load_triple(date: str):
    csv_path = BASE_DIR / "output" / date / "全場_3指数重複馬.csv"
    if not csv_path.exists():
        return []
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    return df.to_dict(orient="records")


def load_summary(date: str):
    path = BASE_DIR / "summary" / f"{date}.xlsx"
    if not path.exists():
        return {}

    xl = pd.ExcelFile(path)
    result = {}  # venue -> [race_dict]

    for venue in xl.sheet_names:
        df = xl.parse(venue, header=None)
        races = []
        current_race = None

        for _, row in df.iterrows():
            cell0 = str(row[0]) if pd.notna(row[0]) else ""

            # レースヘッダー
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

            # セクション検出
            if "近走平均" in cell0 and "トップ5" in cell0:
                current_section = "average"
                continue
            elif "当該距離" in cell0 and "トップ5" in cell0:
                current_section = "distance"
                continue
            elif "当該コース" in cell0 and "トップ5" in cell0:
                current_section = "course"
                continue
            elif "3指数すべて" in cell0:
                current_section = "triple"
                continue
            elif cell0 in ("セクション", "該当なし", "データなし"):
                continue

            # データ行
            num = str(row[1]).strip() if pd.notna(row[1]) else ""
            name = str(row[2]).strip() if pd.notna(row[2]) else ""

            if not name or name == "nan":
                continue

            v_avg = row[3] if pd.notna(row[3]) else ""
            v_dist = row[4] if pd.notna(row[4]) else ""
            v_crs = row[5] if pd.notna(row[5]) else ""

            entry = {
                "num": num,
                "name": name,
                "avg": v_avg,
                "dist": v_dist,
                "crs": v_crs,
            }

            if current_section == "average":
                entry["val"] = v_avg
                current_race["average"].append(entry)
            elif current_section == "distance":
                entry["val"] = v_dist
                current_race["distance"].append(entry)
            elif current_section == "course":
                entry["val"] = v_crs
                current_race["course"].append(entry)
            elif current_section == "triple":
                current_race["triple"].append(entry)

        if current_race:
            races.append(current_race)

        result[venue] = races

    return result


@app.route("/")
def index():
    dates = get_available_dates()
    return render_template("index.html", dates=dates)


@app.route("/api/data/<date>")
def api_data(date):
    if not re.match(r"^\d{8}$", date):
        return jsonify({"error": "invalid date"}), 400
    triple = load_triple(date)
    summary = load_summary(date)
    return jsonify({"triple": triple, "summary": summary})


@app.route("/api/pickup", methods=["POST"])
def api_pickup():
    data = request.get_json()
    shutuba_url = data.get("shutuba_url", "")
    date = data.get("date", "")

    if not re.search(r"race_id=\d{12}", shutuba_url):
        return jsonify({"error": "shutuba URL が正しくありません"}), 400
    if not re.match(r"^\d{8}$", date):
        return jsonify({"error": "date が不正です"}), 400

    # race_id からレース情報を特定して (A) 馬を絞り込む
    m = re.search(r"race_id=(\d{12})", shutuba_url)
    race_id = m.group(1)
    venue_code = race_id[4:6]
    race_num = race_id[10:12].lstrip("0") or "1"
    venue_map = {
        "01": "札幌", "02": "函館", "03": "福島", "04": "新潟",
        "05": "東京", "06": "中山", "07": "中京", "08": "京都",
        "09": "阪神", "10": "小倉",
    }
    venue = venue_map.get(venue_code, "")
    race_label = f"{race_num}R"

    # この日の全3指数重複馬を読み込み、対象レースで絞り込む
    triple_all = load_triple(date)
    triple_race = [
        h for h in triple_all
        if str(h.get("開催場", "")) == venue
        and str(h.get("レース番号", "")).lstrip("0") == race_label.rstrip("R")
    ]

    from race_pickup import analyze_race
    result = analyze_race(shutuba_url, triple_race)
    result["venue"] = venue
    result["race_label"] = f"{venue}{race_label}"
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
