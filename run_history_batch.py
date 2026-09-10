#!/usr/bin/env python3
"""
過去日付の XLSX + pickup_scores.json を一括生成するバッチスクリプト。
usage: python3 run_history_batch.py
"""

import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent

# XLSXがない日付 → scraper.py + run_pickup_all.py を実行
# XLSXはあるが pickup_scores.json がない日付 → run_pickup_all.py のみ実行

TARGET_DATES = [
    "20260104", "20260105", "20260112",
    "20260221", "20260301",
    "20260110", "20260111",
    "20260117", "20260118",
    "20260124", "20260125",
    "20260131", "20260201",
    "20260207", "20260208",
    "20260214", "20260215",
    "20260222", "20260228",
    "20260307", "20260308",
    "20260314", "20260315",
    "20260321", "20260322",  # XLSXあり・pickupなし
]


def run(cmd, label):
    print(f"\n{'='*60}")
    print(f"[{label}] 実行: {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=BASE)
    if result.returncode != 0:
        print(f"⚠️  {label} 失敗 (returncode={result.returncode})")
        return False
    return True


def main():
    for date in TARGET_DATES:
        xlsx_path = BASE / "output" / f"{date}.xlsx"
        csv_path  = BASE / "output" / date / "全場_3指数重複馬.csv"
        pickup_path = BASE / "output" / date / "pickup_scores.json"

        if pickup_path.exists():
            print(f"\n{date}: pickup_scores.json 既存 → スキップ")
            continue

        # XLSX / CSV がなければ scraper.py を実行
        if not xlsx_path.exists() or not csv_path.exists():
            ok = run([sys.executable, "scraper.py", date], f"scraper {date}")
            if not ok:
                print(f"  → scraper 失敗のため {date} の pickup をスキップ")
                continue
            time.sleep(5)  # scraper 後に少し待機
        else:
            print(f"\n{date}: XLSX/CSV 既存 → scraper スキップ")

        # CSV がなければ pickup もスキップ
        if not csv_path.exists():
            print(f"  → 全場_3指数重複馬.csv なし → pickup スキップ")
            continue

        run([sys.executable, "run_pickup_all.py", date], f"pickup {date}")
        time.sleep(10)

    print("\n\n=== バッチ完了 ===")


# import しただけでスクレイプが走らないようにガードする
if __name__ == "__main__":
    main()
