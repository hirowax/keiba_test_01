#!/usr/bin/env python3
"""
3指数のトップ3の定義を変えたとき（「*」付き・同じ順位を含める）の、候補の入れ替わりと成績の比較。
計画（成績を見る前に固定）は docs/index_top3_definition_202611.md の3章。この比較は参考情報で、定義の判断には使わない。

- 旧方式: コミット 6f2ed8d の scraper.find_triple_top5_rows をそのまま呼ぶ（--old-module で渡す。書き写さない）
- 新しい定義: 今の scraper.find_triple_top5_rows
- 指数: output/YYYYMMDD.xlsx。結果: output/YYYYMMDD/race_results_all.json（全レースの取り直し・着順なしの馬を含む）
- 払戻: output/YYYYMMDD/payouts_jra.json（そのレース・券種があるものだけ）。無効レース: invalidated.json（ある日だけ）

使い方:
  git show 6f2ed8d:scraper.py > /tmp/old_scraper_6f2ed8d.py
  python3 compare_index_top3_202611.py --old-module /tmp/old_scraper_6f2ed8d.py --counts-only   # 件数だけ（成績は出さない）
  python3 compare_index_top3_202611.py --old-module /tmp/old_scraper_6f2ed8d.py --out result.json

参考集計（2026-11-07 追加。docs/task3_refetch_reference_plan_202611.md）:
  python3 compare_index_top3_202611.py --old-module ... --days 20251102,20260110 \
      --index-dir research/task3_index_refetch_20261106/tables [--index-name "{date}_index_refetch.xlsx"] --counts-only
  - 指定した日の、指定したフォルダの xlsx だけを読む。ファイルが無ければ止まる（output/{日}.xlsx には戻らない）。
  - 結果のファイルが無い・取り直しが未完了の日も、外さずに止まる。
  - 前半・後半は作らない（全期間だけ）。当時の候補ファイルとは比べない。
  - 払戻の不足を単勝・複勝で別々に数える（payout_coverage_by_kind・races_missing_payout_by_kind）。
  - 実行の記録に、実際に読んだ xlsx のパスと sha256 を残す。
  --days・--index-dir を省略したときの動作と出力は、追加前と同じ。
"""
import argparse
import importlib.util
import json
import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

import scraper
from index_table_check import load_index_tables

logging.disable(logging.CRITICAL)
OUTPUT_DIR = Path(__file__).parent / "output"
START, END = "20250920", "20261101"
MODE_KEYS = {"近走平均": "average", "当該距離": "distance", "当該コース": "course"}
GROUPS = ("両方で候補", "新しく候補", "候補から外れる")
SPACES = re.compile(r"[\s　]+")


def load_module(path):
    spec = importlib.util.spec_from_file_location("old_scraper", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_json(path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_day(path):
    """保存済み xlsx を {label: {mode_key: DataFrame}} に戻す（index_table_check.load_index_tables と同じ処理を使う）"""
    return load_index_tables(path)


def same_name(a, b):
    return SPACES.sub("", str(a)) == SPACES.sub("", str(b))


def sha256(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_record(args, days, index_files=None):
    """実行の記録: コードの版・旧方式のファイル・入力ファイルの sha256（入力を Git の版とハッシュから確かめられるように）。
    参考集計（index_files あり）では、output/{日}.xlsx の代わりに実際に読んだ xlsx を index_files に記録する"""
    import subprocess
    import sys
    repo = Path(__file__).parent
    inputs = {}
    names = ("race_results_all.json", "payouts_jra.json", "invalidated.json") if index_files is not None else \
        ("race_results_all.json", "payouts_jra.json", "invalidated.json", "全場_3指数重複馬.csv")
    for d in days:
        for name in ([] if index_files is not None else [f"{d}.xlsx"]) + [f"{d}/{n}" for n in names]:
            f = OUTPUT_DIR / name
            if f.exists():
                inputs[name] = sha256(f)
    rec = {}
    if index_files is not None:
        rec["index_files"] = {d: {"path": str(f), "sha256": sha256(f)} for d, f in index_files.items()}
    return {**rec, "command": " ".join(sys.argv), "counts_only": args.counts_only,
            "head": subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
            "dirty": subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", "compare_index_top3_202611.py",
                                     "scraper.py", "index_table_check.py"], capture_output=True, text=True).stdout.strip(),
            "old_module": str(args.old_module), "old_module_sha256": sha256(args.old_module),
            "pandas": pd.__version__, "inputs_sha256": inputs}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-module", required=True)
    ap.add_argument("--counts-only", action="store_true", help="件数だけを出し、成績は出さない")
    ap.add_argument("--out")
    ap.add_argument("--days", help="参考集計: 対象の日（カンマ区切り）。--index-dir と一緒に使う")
    ap.add_argument("--index-dir", help="参考集計: 指数の xlsx を読むフォルダ（output/ の代わり）")
    ap.add_argument("--index-name", default="{date}_index_refetch.xlsx", help="参考集計: --index-dir の中のファイル名")
    args = ap.parse_args(argv)
    if bool(args.days) != bool(args.index_dir):
        raise SystemExit("--days と --index-dir は一緒に指定する")
    reference = bool(args.days)
    old = load_module(args.old_module)

    files, days, day_skip = {}, [], {}
    index_files = None
    if reference:
        days = sorted(set(args.days.split(",")))
        if not all(re.fullmatch(r"\d{8}", d) for d in days):
            raise SystemExit(f"--days の形が違う: {args.days}")
        index_files = {d: Path(args.index_dir) / args.index_name.format(date=d) for d in days}
        for d in days:
            ra = load_json(OUTPUT_DIR / d / "race_results_all.json")
            if not index_files[d].exists():
                raise SystemExit(f"指数の xlsx が無い: {index_files[d]}（output/{d}.xlsx には戻らない）")
            if not (ra and ra.get("complete")):
                raise SystemExit(f"{d}: race_results_all.json が無いか、取り直しが未完了")
            files[d] = {"xlsx": str(index_files[d]), "race_results_all": True, "complete": True,
                        "payouts_jra": (OUTPUT_DIR / d / "payouts_jra.json").exists(),
                        "invalidated": (OUTPUT_DIR / d / "invalidated.json").exists()}
        half = set()
    else:
        days_all = sorted(f.stem for f in OUTPUT_DIR.glob("*.xlsx") if re.fullmatch(r"\d{8}", f.stem) and START <= f.stem <= END)
        for d in days_all:
            ra = load_json(OUTPUT_DIR / d / "race_results_all.json")
            files[d] = {"xlsx": True, "race_results_all": bool(ra), "complete": bool(ra and ra.get("complete")),
                        "payouts_jra": (OUTPUT_DIR / d / "payouts_jra.json").exists(),
                        "invalidated": (OUTPUT_DIR / d / "invalidated.json").exists(),
                        "当時の候補CSV": (OUTPUT_DIR / d / "全場_3指数重複馬.csv").exists()}
            if not (ra and ra.get("complete")):
                day_skip[d] = "全レースの結果の取り直しが未完了"
                continue
            days.append(d)
        half = set(days[: len(days) // 2])

    race_excl = Counter()
    horses = []              # 候補の馬ごとの記録
    unmatched = Counter()
    repro = []               # 当時の候補CSVと、固定した旧コードで再計算した候補の違い
    for d in days:
        races = load_day(index_files[d] if reference else OUTPUT_DIR / f"{d}.xlsx")
        res = load_json(OUTPUT_DIR / d / "race_results_all.json")["races"]
        pay = load_json(OUTPUT_DIR / d / "payouts_jra.json") or {}
        inv = (load_json(OUTPUT_DIR / d / "invalidated.json") or {}).get("races", {})
        old_all = set()
        for label in set(res) - set(races):
            race_excl["指数の表が無い（xlsxにレースが無い）"] += 1
        for label, dfs in races.items():
            for k in scraper.MODES:
                dfs.setdefault(k, None)
            o = old.find_triple_top5_rows(label, {k: (v.copy() if v is not None else None) for k, v in dfs.items()})
            old_all |= {(label, r["馬番"]) for r in o}
            # 表の検査（当たったレースは両方の選び方から外す）
            if label in inv:
                race_excl["無効レース"] += 1; continue
            if any(v is None for v in dfs.values()):
                race_excl["3指数のどれかの表が無い"] += 1; continue
            checked = [scraper.ranked_horses(v) for v in dfs.values()]
            if any(c[1] for c in checked):
                race_excl["取得異常（200以上・読めない値・順位の形・馬番の重複）"] += 1; continue
            if any(c[2] for c in checked):
                race_excl["ページの順位の食い違い"] += 1; continue
            r = res.get(label)
            if not r:
                race_excl["結果が無い"] += 1; continue
            # 表の欠けは指数の表ごとに確かめる（3表をまとめると、ある表に無い馬が別の表にいれば通ってしまう。
            # 2026-11-06 外部レビュー指摘。それまでの版は3表の合計で判定していた）
            result_nums = {str(x["num"]) for x in r["horses"]} | {str(x["num"]) for x in r["others"]}
            if any(result_nums - {h["馬番"] for h in c[0]} for c in checked):
                race_excl["表の欠け（結果の馬番が表に無い）"] += 1; continue
            race_excl["比較の対象"] += 1

            new = {x["馬番"]: x for x in scraper.find_triple_top5_rows(label, dfs)}
            oldc = {x["馬番"]: x for x in o}
            fin = {str(x["num"]): x for x in r["horses"]}
            oth = {str(x["num"]): x for x in r["others"]}
            rp = pay.get(label, {})
            for g, nums in (("両方で候補", new.keys() & oldc.keys()), ("新しく候補", new.keys() - oldc.keys()),
                            ("候補から外れる", oldc.keys() - new.keys())):
                for n in nums:
                    cand = (new.get(n) or oldc.get(n))
                    rec = {"date": d, "half": None if reference else ("前半" if d in half else "後半"), "label": label, "num": n,
                           "group": g, "name": cand["馬名"]}
                    x = fin.get(n) or oth.get(n)
                    if x is None:
                        rec["match"] = "馬番が結果に無い"
                    elif not same_name(x["name"], cand["馬名"]):
                        rec["match"] = "馬番は合うが馬名が違う"
                    else:
                        rec["match"] = "一致"
                        rec["status"] = "着順あり" if n in fin else x["status"]
                        if n in fin:
                            rec["rank"] = fin[n]["rank"]
                            rec["odds"] = fin[n].get("odds")
                        for kind in ("tansho", "fukusho"):
                            if kind in rp:
                                rec[kind] = rp[kind].get(n, 0)
                    if rec["match"] != "一致":
                        unmatched[(g, rec["match"])] += 1
                    horses.append(rec)
        csv = OUTPUT_DIR / d / "全場_3指数重複馬.csv"
        if not reference and csv.exists():
            t = pd.read_csv(csv, encoding="utf-8-sig", dtype=str)
            then = {(r["開催場"] + r["レース番号"], str(r["馬番"]).strip()) for _, r in t.iterrows()}
            if then != old_all:
                repro.append({"date": d, "当時だけ": len(then - old_all), "再計算だけ": len(old_all - then)})

    out = {"files": files, "day_skip": day_skip, "days": len(days),
           "race_exclusions": dict(race_excl), "unmatched": {f"{g}・{m}": v for (g, m), v in unmatched.items()},
           "repro_diff_days": repro}
    print(f"対象 {len(days)}日（取り直し未完了で外した日 {len(day_skip)}）")
    print("レース:", dict(race_excl))
    print("当時の候補CSVと再計算した旧方式の候補が違う日:", len(repro), repro[:8])
    status = Counter((h["group"], h.get("status", h["match"])) for h in horses)
    for g in GROUPS:
        print(f"{g}: {sum(v for (gg, _), v in status.items() if gg == g)}頭 内訳",
              {s: v for (gg, s), v in status.items() if gg == g})
    out["status"] = {f"{g}・{s}": v for (g, s), v in status.items()}
    # 払戻の記録があるレースの候補の頭数（金額は見ない。回収率の対象の偏りの確認。2026-11-06〜）
    cov = Counter((h["group"], "払戻あり" if "tansho" in h else "払戻なし") for h in horses if h["match"] == "一致")
    out["payout_coverage"] = {f"{g}・{c}": v for (g, c), v in sorted(cov.items())}
    print("払戻の記録があるレースの候補（頭数）:", out["payout_coverage"])
    # 候補がいるのに払戻の記録が無いレース（回収率の前に払戻をそろえる対象。scrape_jra_payouts.py --races-file で使う）
    out["races_without_payout"] = sorted({(h["date"], h["label"]) for h in horses
                                          if h["match"] == "一致" and "tansho" not in h})
    print("候補がいて払戻の記録が無いレース:", len(out["races_without_payout"]))
    if reference:
        # 単勝・複勝を別々に（2026-11-07 外部レビュー指摘。上の2つは単勝の有無だけで見ている）
        cov_k = Counter((h["group"], kind, "払戻あり" if kind in h else "払戻なし")
                        for h in horses if h["match"] == "一致" for kind in ("tansho", "fukusho"))
        out["payout_coverage_by_kind"] = {f"{g}・{k}・{c}": v for (g, k, c), v in sorted(cov_k.items())}
        out["races_missing_payout_by_kind"] = {
            kind: sorted({(h["date"], h["label"]) for h in horses if h["match"] == "一致" and kind not in h})
            for kind in ("tansho", "fukusho")}
        print("払戻の不足（券種ごと・レース数）:", {k: len(v) for k, v in out["races_missing_payout_by_kind"].items()})
    out["run"] = run_record(args, days, index_files)

    if not args.counts_only:
        out["results"] = summarize(horses, ("全期間",) if reference else ("全期間", "前半", "後半"))
        for key, v in out["results"].items():
            print(key, v)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)


def summarize(horses, periods=("全期間", "前半", "後半")):
    """計画どおりの集計。3着内率の分母は着順のある馬と中止・失格。取消・除外は外す。不明は外して件数を出す"""
    out = {}
    for period in periods:
        for g in GROUPS:
            hs = [h for h in horses if h["group"] == g and (period == "全期間" or h["half"] == period)]
            ran = [h for h in hs if h.get("status") in ("着順あり", "中止", "失格")]
            top3 = sum(1 for h in ran if h.get("rank") and h["rank"] <= 3)
            r = {"頭数": len(hs), "レース数": len({(h["date"], h["label"]) for h in hs}),
                 "3着内率の分母": len(ran), "3着以内": top3,
                 "3着内率": round(top3 / len(ran) * 100, 1) if ran else None,
                 "取消・除外": sum(1 for h in hs if h.get("status") in ("取消", "除外")),
                 "状態不明・照合できない": sum(1 for h in hs if h.get("status") == "不明" or h["match"] != "一致")}
            for kind, name in (("tansho", "単勝"), ("fukusho", "複勝")):
                bet = [h for h in ran if kind in h]
                ret = sum(h[kind] for h in bet)
                r[f"{name}: 払戻あり頭数"] = len(bet)
                r[f"{name}: 払戻無しで外した頭数"] = len(ran) - len(bet)
                r[f"{name}回収率"] = round(ret / (len(bet) * 100) * 100, 1) if bet else None
            # 参考: 確定単勝オッズ×100（1着の馬だけ）
            ref = [h for h in ran]
            ret_ref = sum(round(h["odds"] * 100) for h in ref if h.get("rank") == 1 and h.get("odds"))
            r["参考: オッズから計算した単勝回収率"] = round(ret_ref / (len(ref) * 100) * 100, 1) if ref else None
            both = [h for h in ran if "tansho" in h and h.get("rank") == 1 and h.get("odds")]
            r["参考: 1着でオッズ×100と公式払戻が一致"] = f"{sum(1 for h in both if round(h['odds'] * 100) == h['tansho'])}/{len(both)}"
            out[f"{period}・{g}"] = r
    return out


if __name__ == "__main__":
    main()
