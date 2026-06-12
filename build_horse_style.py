#!/usr/bin/env python3
"""
全race_results.jsonのコーナー通過順から各馬の脚質を推定して保存。

保存先: output/horse_style.json
  {horse_id: {"style": "逃げ/先行/差し/追い込み", "avg_ratio": 0.12, "n_races": 5}}

usage:
  python3 build_horse_style.py   # output/ 以下の全日付を対象
"""

import json
import logging
import os
import re
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
STYLE_PATH = OUTPUT_DIR / "horse_style.json"

# 脚質分類の閾値（1コーナー相対位置: 0=先頭, 1=最後尾）
THRESHOLDS = {
    '逃げ':   (0.00, 0.15),
    '先行':   (0.15, 0.35),
    '差し':   (0.35, 0.60),
    '追い込み': (0.60, 1.01),
}

def classify(avg_ratio: float) -> str:
    for style, (lo, hi) in THRESHOLDS.items():
        if lo <= avg_ratio < hi:
            return style
    return '追い込み'


def build():
    horse_data = defaultdict(list)  # horse_id -> [c1_ratio, ...]

    dates = sorted([d for d in os.listdir(OUTPUT_DIR) if re.match(r'^\d{8}$', d)])
    log.info(f"対象日付: {len(dates)}日分")

    for d in dates:
        rr_path = OUTPUT_DIR / d / "race_results.json"
        if not rr_path.exists():
            continue
        with open(rr_path, encoding="utf-8") as f:
            rr = json.load(f)

        for label, horses in rr.items():
            n = len(horses)
            if n < 8:
                continue
            for h in horses:
                hid = h.get("horse_id")
                corners = h.get("corners", [])
                if not hid or not corners:
                    continue
                c1_ratio = (corners[0] - 1) / (n - 1) if n > 1 else 0.5
                horse_data[hid].append(c1_ratio)

    result = {}
    style_counts = defaultdict(int)
    for hid, ratios in horse_data.items():
        avg = sum(ratios) / len(ratios)
        style = classify(avg)
        result[hid] = {
            "style":     style,
            "avg_ratio": round(avg, 3),
            "n_races":   len(ratios),
        }
        style_counts[style] += 1

    with open(STYLE_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log.info(f"保存: {STYLE_PATH} ({len(result)}頭)")
    for s, n in sorted(style_counts.items()):
        log.info(f"  {s}: {n}頭")
    return result


if __name__ == "__main__":
    build()
