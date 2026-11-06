"""
タスク3: 取り直した指数の表（pages/ の保存ページ）を、本番の output/{日}.xlsx と同じ形の別ファイルに書き出し、読み戻して確かめる。
2026-11-07（外部レビューの勧め: 書き出し先は research/ の中。取得日時・対象日・元ページのハッシュ・変換コードの版を残す。
読み戻して、全行・順位・「*」・マイナス・値なしが保存ページと一致することを確かめる）。

- 入力: pages/{日}/{レースID}_{mode}.html.gz、output/{日}/race_results_all.json（レースの一覧とレース名）、refetch_log.jsonl（取得日時）。
- 出力（既存の output/ は書き換えない）:
  - tables/{日}_index_refetch.xlsx: 本番の scraper.write_excel と同じ形（シート＝開催場、「■ レース名  指数名」の行、見出しの行、
    馬の行、空行）。scraper.write_excel は output/ に書き、output/{日}/ の CSV を消すので呼ばない。黄色の塗りは付けない。
    入れる表は、順位の表ありのページと、全馬が値なしの表のページ（どちらも表がある）。新馬戦のページ（表なし）は入れない。
  - tables/manifest.json: ページごとに、対象日・レースID・レース名・指数・ページの分類・取得日時・元ページ（gz と中の HTML）の
    sha256・xlsx に入れたか。書き出しと確認のコード（このスクリプト・scraper.py・index_table_check.py）の sha256、実行時の HEAD、
    作業ツリーの変更の有無、読み戻しの確認の結果。
- ページの分類は check_six_days.py と同じ判定（scraper.parse_rank_html・_is_valid_speed_df、全馬が「-」で値なし、新馬戦の決まった文）。
- 読み戻しの確認: 書いた xlsx を index_table_check.load_index_tables（本番・タスク7の比較と同じ読み込み処理）で読み、
  ページから読んだ表と、列名・行数・全セルの文字が完全に一致するか（2段目の見出し行を含む）。読み込み処理はシートの全幅で読むので、
  狭い表（当該距離・当該コースは10列、近走平均は11列）の右に見出しも中身も空の列が付く（本番の xlsx でも同じ）。その列だけは外して比べ、数を記録する。ほかに表の数・レース名の重複も確かめる。
  さらに、順位（N位・-）・「*」付き・マイナス・値なし（classify_index_text が none）の行数を、ページ側と読み戻し側で数えて並べる。

この表はレースの後（2026-11-06）に取ったもの。当時（レース前日）の表と同じとは確かめていない（docs/incident_umaban_20261030.md のタスク3）。
当時の候補・当時の運用成績・新しい定義の評価には使わない。

実行: python3 research/task3_index_refetch_20261106/export_tables.py
"""
import gzip
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import scraper  # noqa: E402
from index_table_check import load_index_tables  # noqa: E402

HERE = Path(__file__).resolve().parent
PAGES = HERE / "pages"
OUT = HERE / "tables"
DAYS = ["20251102", "20260110", "20260111", "20260117", "20260118", "20260124"]
MODES = ["average", "distance", "course"]
SUB = ("5走前", "着度数")
NEWCOMER_TEXT = "新馬戦にはタイム指数はございません"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def classify(html):
    """(分類, DataFrame か None)。check_six_days.py と同じ判定"""
    df = scraper.parse_rank_html(html)
    if df is not None and scraper._is_valid_speed_df(df):
        return "順位の表あり", df
    if df is not None:
        rows = [[str(x) for x in r.values] for _, r in df.iterrows() if str(r.values[0]) not in SUB]
        if rows and all(v[0] == "-" and scraper.classify_index_text(v[5])[0] == "none" for v in rows):
            return "全馬が値なしの表", df
    if NEWCOMER_TEXT in html:
        return "新馬戦", None
    return "表なし（ほか）", None


def counts(df):
    """順位・「*」・マイナス・値なしの行数（2段目の見出し行を除く）"""
    c = Counter()
    for _, r in df.iterrows():
        v = [str(x) for x in r.values]
        if v[0] in SUB:
            c["見出し行"] += 1
            continue
        c["馬の行"] += 1
        c["順位N位" if re.fullmatch(r"\d+位", v[0]) else ("順位-" if v[0] == "-" else "順位ほか")] += 1
        kind, _ = scraper.classify_index_text(v[5])
        c[f"値_{kind}"] += 1
        if v[5].endswith("*"):
            c["「*」付き"] += 1
        if v[5].startswith("-") and v[5] != "-":
            c["マイナス"] += 1
    return c


def fetched_at():
    at = {}
    for line in open(HERE / "refetch_log.jsonl", encoding="utf-8"):
        j = json.loads(line)
        if j["event"] == "page":
            at[(j["race_id"], j["mode"])] = j["at"]
    return at


def write_day(path, blocks):
    """blocks: [(開催場, レース名, 指数名, df)]。scraper.write_excel の表の書き方と同じ（塗りなし）"""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        cursor = {}
        for venue, label, mode_label, df in blocks:
            r = cursor.get(venue, 0)
            pd.DataFrame([[f"■ {label}  {mode_label}"]]).to_excel(writer, sheet_name=venue, startrow=r, index=False, header=False)
            df.to_excel(writer, sheet_name=venue, startrow=r + 1, index=False)
            cursor[venue] = r + 1 + len(df) + 2


def main():
    OUT.mkdir(exist_ok=True)
    at = fetched_at()
    pages, check = [], {"days": {}}
    for d in DAYS:
        races = json.load(open(ROOT / "output" / d / "race_results_all.json", encoding="utf-8"))["races"]
        blocks, expected = [], {}
        for label, r in sorted(races.items(), key=lambda kv: kv[1]["race_id"]):
            rid = r["race_id"]
            assert scraper._build_label(rid, None, None) == label, (d, label, rid)
            for m in MODES:
                f = PAGES / d / f"{rid}_{m}.html.gz"
                raw = f.read_bytes()
                html = gzip.decompress(raw).decode("utf-8")
                kind, df = classify(html)
                rec = {"date": d, "race_id": rid, "label": label, "mode": m, "kind": kind,
                       "fetched_at": at.get((rid, m)), "page_gz": str(f.relative_to(ROOT)),
                       "page_gz_sha256": sha(raw), "page_html_sha256": sha(html.encode("utf-8")),
                       "in_xlsx": df is not None}
                if rec["fetched_at"] is None:
                    rec["fetched_at_note"] = ("ログに記録なし（2026-11-06 18:53:11 の最初の実行で、このページを保存した後、"
                                              "ログに書く値の型の誤りで止まった。18:53:36 の2回目の実行より前に保存）")
                pages.append(rec)
                if df is not None:
                    blocks.append((scraper.venue_of(label), label, scraper.MODES[m], df))
                    expected[(label, m)] = df
        path = OUT / f"{d}_index_refetch.xlsx"
        write_day(path, blocks)
        # 読み戻し
        back = load_index_tables(path)
        got = {(lab, m): v for lab, ms in back.items() for m, v in ms.items()}
        diffs = []
        cnt_page, cnt_back = Counter(), Counter()
        padded = Counter()
        for key, df in expected.items():
            b = got.get(key)
            if b is None:
                diffs.append({"table": key, "problem": "読み戻した表に無い"})
                continue
            a = df.astype(str)
            # 読み込み処理はシートの全幅で読むので、同じシートの広い表（近走平均は11列、当該距離・当該コースは10列）に
            # 合わせて、狭い表の右に見出しも中身も空の列が付く（本番の xlsx でも同じ）。その列だけを外して比べ、数を記録する
            pad = 0
            while b.shape[1] > a.shape[1] and b.columns[-1] == "" and (b.iloc[:, -1] == "").all():
                b = b.iloc[:, :-1]
                pad += 1
            if pad:
                padded[pad] += 1
            if list(a.columns) != list(b.columns):
                diffs.append({"table": key, "problem": "列名が違う", "page": list(a.columns), "back": list(b.columns)})
            elif a.shape != b.shape:
                diffs.append({"table": key, "problem": "行数が違う", "page": a.shape, "back": b.shape})
            else:
                bad = [(i, c, a.iat[i, j], b.iat[i, j]) for i in range(a.shape[0]) for j, c in enumerate(a.columns)
                       if a.iat[i, j] != b.iat[i, j]]
                if bad:
                    diffs.append({"table": key, "problem": "セルの文字が違う", "cells": bad[:10], "n": len(bad)})
            cnt_page.update(counts(df))
            cnt_back.update(counts(b))
        extra = sorted(set(got) - set(expected))
        check["days"][d] = {"xlsx": str(path.relative_to(ROOT)), "xlsx_sha256": sha(path.read_bytes()),
                            "tables_written": len(expected), "tables_read_back": len(got),
                            "extra_tables": extra, "diffs": diffs,
                            "counts_page": dict(cnt_page), "counts_read_back": dict(cnt_back),
                            "counts_equal": cnt_page == cnt_back,
                            "read_back_empty_right_columns": {f"{k}列": v for k, v in padded.items()}}
        print(d, "表", len(expected), "読み戻し", len(got), "違い", len(diffs), "余分", len(extra),
              "数の一致", cnt_page == cnt_back, "右に空の列が付いた表", dict(padded), dict(cnt_page))
    kinds = Counter(p["kind"] for p in pages)
    ok = all(not v["diffs"] and not v["extra_tables"] and v["counts_equal"] and v["tables_written"] == v["tables_read_back"]
             for v in check["days"].values())
    check["all_ok"] = ok
    print("ページの分類", dict(kinds), "xlsx に入れた表", sum(p["in_xlsx"] for p in pages),
          "取得日時の記録なし", sum(p["fetched_at"] is None for p in pages), "読み戻しの確認", "すべて一致" if ok else "違いあり")
    code = [Path(__file__).resolve(), ROOT / "scraper.py", ROOT / "index_table_check.py"]
    git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    manifest = {
        "note": ("2026-11-06 にレースの後に取り直した指数の表。当時（レース前日）の表と同じとは確かめていない。"
                 "当時の候補・当時の運用成績・新しい定義の評価には使わない。docs/incident_umaban_20261030.md のタスク3"),
        "run": {"commit": git("rev-parse", "HEAD"),
                "code_dirty": git("status", "--porcelain", "--", *[str(c.relative_to(ROOT)) for c in code]),
                "code": {str(c.relative_to(ROOT)): sha(c.read_bytes()) for c in code},
                "index_reader_version": scraper.INDEX_READER_VERSION, "python": sys.version.split()[0],
                "pandas": pd.__version__},
        "page_kinds": dict(kinds), "read_back_check": check, "pages": pages}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
