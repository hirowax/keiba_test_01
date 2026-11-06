"""タスク1の数え直しの2つの出力（prev_data_check.json）の違いを、集計と明細について機械的に出す（2026-11-06）。
手で書いた差分の表に記載漏れがあった（外部レビュー指摘）ので、差分は必ずこのスクリプトの出力から書く。

usage: python3 research/prev_data_check_20261104/diff_results.py <前の出力フォルダ> <後の出力フォルダ> > 差分.md
"""
import json
import sys
from pathlib import Path


def load(d):
    return json.load(open(Path(d) / "prev_data_check.json", encoding="utf-8"))


def key_of(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def main():
    a, b = load(sys.argv[1]), load(sys.argv[2])
    print(f"# 差分: {sys.argv[1]} → {sys.argv[2]}\n")
    print("## 集計（値が違う項目だけ。片方にしか無い項目は「-」）\n")
    print("| 節 | 項目 | 前 | 後 |")
    print("|---|---|---:|---:|")
    n = 0
    for sec in sorted(set(a["集計"]) | set(b["集計"])):
        x, y = a["集計"].get(sec, {}), b["集計"].get(sec, {})
        for k in sorted(set(x) | set(y)):
            if x.get(k) != y.get(k):
                print(f"| {sec} | {k} | {x.get(k, '-')} | {y.get(k, '-')} |")
                n += 1
    print(f"\n違う項目: {n}\n")
    print("## 明細（件数と、前だけ・後だけにある行の数）\n")
    print("| 明細 | 前の件数 | 後の件数 | 前だけ | 後だけ |")
    print("|---|---:|---:|---:|---:|")
    for name in sorted(k for k in set(a) | set(b) if k.startswith("明細")):
        x = [key_of(r) for r in a.get(name, [])]
        y = [key_of(r) for r in b.get(name, [])]
        sx, sy = set(x), set(y)
        print(f"| {name} | {len(x)} | {len(y)} | {len(sx - sy)} | {len(sy - sx)} |")


if __name__ == "__main__":
    main()
