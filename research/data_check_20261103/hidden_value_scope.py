"""タスク9: 保存済みの指数の表で、値が200以上（検出の条件）の表・レース・日を数える。

使い方（リポジトリの根から）:
    python3 research/data_check_20261103/hidden_value_scope.py 入力の_output_フォルダ --base-commit SHA
- 入力のフォルダ: 2026-11-04 の実行は、結果の取り直しが書き込む途中のファイルを避けるため、
  output/ の必要なファイルを別のフォルダに写し、その写しを読んだ。
- --base-commit: 候補の一覧（全場_3指数重複馬.csv）の git の履歴を、このコミットから探す。
  実行時の HEAD に頼らないので、後で候補の一覧が変わっても同じコードを選ぶ。完全な SHA を出力に残す。
- 読んだファイルの sha256 は results/hidden_value_scope_manifest.json に書く。実行の前と後で比べ、
  変わったファイルがあれば、結果ファイル（.txt・.json・manifest）を書かずに終了コード1で止まる。

数え方:
- 対象: 入力フォルダの YYYYMMDD.xlsx すべて。2段目の見出し行（1列目が「5走前」「着度数」）は読み飛ばす。
- 値は6列目（指数の列）。セルごとに次のどれかに分ける:
  空 / 整数（末尾の「*」を外して整数として読めた）/ 整数として読めない（「**」など。元の文字を数える）
- 表の分類（読めた整数値だけで判定する。読めない値の有無は別に数える）:
  読めた整数なし / 読めた整数値がすべて200以上 / 一部200以上 / 200以上なし
- 値が200以上は検出の条件で、隠し値（1000＋指数）と確かめたものではない。
- レース: 3モードのどれかに200以上の値があればそのレースを数える。
- 候補の数: pickup_scores.json の races[ラベル].scored の頭数。レースが無ければ「採点記録なし」。
- 障害戦: race_conditions.json → race_results_all.json → race_results.json の順にクラス名を探し、
  「障害」を含めば障害戦。どこにも無ければ「条件の記録なし」。
- 旧集計（候補の一覧の1000以上）: 全場_3指数重複馬.csv の指数の列のどれかが1000以上の行がある日。
- ペーパートレード: 2026-09-12 以降の対象レースすべてを、paper_bets.json の races（買い目あり）・
  skipped_races（見送り）・どちらにも無い、に分ける。invalidated.json に載っているかも見る。
- 当時のコードの再現: 候補のある対象レースについて、--base-commit から見て、その日の候補の一覧を最後に
  変えたコミットを探す。そのコミット時点の scraper.py（そのコミットで scraper.py も変えていれば変えた後の版）を
  git から取り出し、find_triple_top5_rows() に保存済みの表を渡して、出た馬番を候補の一覧の馬番と比べる
  （書き写さず、元の関数を呼ぶ）。入力の候補の一覧が、選んだコミットの内容とバイト単位で一致するかも確かめる。
  一致しても、選んだコードと保存済みの表から保存済みの候補の一覧を再現できたことまでしか言えない。
  当時実際に使った入力と実行コードの証明ではない。
"""
import argparse, builtins, collections, hashlib, importlib.util, json, re, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from xlsx_blocks import load_day

ap = argparse.ArgumentParser()
ap.add_argument("input_dir")
ap.add_argument("--base-commit", required=True)
ap.add_argument("--results-dir", default=None,
                help="結果の置き場所（既定は results/。2026-11-06 の出し直しで、11/4 の結果を残すために足した）")
ARGS = ap.parse_args()
OUT = Path(ARGS.input_dir).resolve()
RESULTS = Path(ARGS.results_dir).resolve() if ARGS.results_dir else HERE / "results"
LOG = []


def print(*a, **k):  # 画面に出しつつ、変化が無かったときだけ .txt に書く
    LOG.append(" ".join(str(x) for x in a))
    builtins.print(*a, **k)
NUM = re.compile(r"-?\d+")
SUB = ("5走前", "着度数")
SIX_DAYS = {"20251102", "20260110", "20260111", "20260117", "20260118", "20260124"}  # タスク3: 表が馬番1〜3だけの日
PAPER_START = "20260912"
INPUT_NAMES = ("pickup_scores.json", "race_conditions.json", "race_results_all.json", "race_results.json",
               "paper_bets.json", "invalidated.json", "全場_3指数重複馬.csv")
CSV_IDX = ("近走平均指数", "当該距離指数", "当該コース指数")


def manifest():
    files = {}
    for f in day_xlsx() + sorted(p for n in INPUT_NAMES for p in OUT.glob(f"[0-9]*/{n}")):
        files[str(f.relative_to(OUT))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return files


def day_xlsx():
    """YYYYMMDD.xlsx だけ（20260329_12R_preview.xlsx などの試し取りは対象外）"""
    return sorted(f for f in OUT.glob("[0-9]*.xlsx") if re.fullmatch(r"\d{8}", f.stem))


def git(*args, binary=False):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=not binary, check=True)
    return r.stdout


BASE = git("rev-parse", "--verify", ARGS.base_commit + "^{commit}").strip()


def csv_commits(d):
    """--base-commit から見て、その日の候補の一覧を変えたコミット（新しい順）"""
    return git("log", "--format=%H %cI", BASE, "--", f"output/{d}/全場_3指数重複馬.csv").split("\n")[:-1]


def jload(p):
    return json.load(open(p)) if p.exists() else None


def races_of(j):
    if not isinstance(j, dict):
        return {}
    r = j.get("races", j)
    return r if isinstance(r, dict) else {}


def class_name(d, lab):
    rc = jload(OUT / d / "race_conditions.json") or {}
    if isinstance(rc.get(lab), dict) and rc[lab].get("class_name"):
        return rc[lab]["class_name"], "race_conditions.json"
    for fn in ("race_results_all.json", "race_results.json"):
        r = races_of(jload(OUT / d / fn)).get(lab)
        if isinstance(r, dict):
            cn = (r.get("condition") or {}).get("class_name")
            if cn:
                return cn, fn
    return None, None


def has_result(d, lab):
    for fn in ("race_results_all.json", "race_results.json"):
        if lab in races_of(jload(OUT / d / fn)):
            return fn
    return None


def read_csv(d):
    import pandas as pd
    p = OUT / d / "全場_3指数重複馬.csv"
    if not p.exists():
        return None
    return pd.read_csv(p, dtype=str, encoding="utf-8-sig")


_old_cache = {}


def old_finder(d):
    """今の候補の一覧を最後にコミットした時点の scraper.find_triple_top5_rows
    （そのコミットで scraper.py も変えていれば、変えた後の版）"""
    cs = csv_commits(d)
    if not cs:
        return None, {"error": "候補の一覧がgitに無い"}
    csv_commit = cs[0].split()[0]
    sc = git("rev-list", "-1", csv_commit, "--", "scraper.py").strip()
    same = git("show", f"{csv_commit}:output/{d}/全場_3指数重複馬.csv", binary=True) == \
        (OUT / d / "全場_3指数重複馬.csv").read_bytes()
    info = {"csv_commit": csv_commit, "csv_commits_count": len(cs), "scraper_commit": sc,
            "input_csv_equals_commit": same}
    if sc not in _old_cache:
        src = subprocess.run(["git", "show", f"{sc}:scraper.py"], cwd=ROOT, capture_output=True, text=True).stdout
        tmp = Path(tempfile.mkdtemp()) / f"scraper_{sc[:7]}.py"
        tmp.write_text(src)
        spec = importlib.util.spec_from_file_location(f"scraper_{sc[:7]}", tmp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _old_cache[sc] = getattr(mod, "find_triple_top5_rows", None)
    return _old_cache[sc], info


def main():
    before = manifest()
    tables = collections.Counter()
    raw_unreadable = collections.Counter()
    modes_per_race = collections.Counter()
    table_rows = []
    rows = []
    day_dfs = {}
    for xf in day_xlsx():
        d = xf.stem
        ps = jload(OUT / d / "pickup_scores.json") or {}
        sc = {lab: len(r.get("scored", [])) for lab, r in ps.get("races", {}).items() if isinstance(r, dict)}
        loaded = load_day(xf)
        for (sh, lab), modes in loaded.items():
            modes_per_race[len(modes)] += 1
            kinds, big_vals = {}, []
            for mk, df in modes.items():
                rws = df[~df.iloc[:, 0].astype(str).str.strip().isin(SUB)]
                cells = [str(v).strip() for v in rws[df.columns[5]]]
                n_empty = sum(1 for v in cells if v == "")
                ints, bad = [], []
                for v in cells:
                    if v == "":
                        continue
                    if NUM.fullmatch(v.rstrip("*")):
                        ints.append(int(v.rstrip("*")))
                    else:
                        bad.append(v)
                big = [v for v in ints if v >= 200]
                k = ("読めた整数なし" if not ints else
                     "読めた整数値がすべて200以上" if len(big) == len(ints) else
                     "一部200以上" if big else "200以上なし")
                kinds[mk] = k
                tables[k] += 1
                raw_unreadable.update(bad)
                big_vals += big
                if big or not ints:
                    table_rows.append({"date": d, "race": lab, "mode": mk, "class": k, "rows": len(cells),
                                       "ints": len(ints), "ge200": len(big), "empty": n_empty,
                                       "unreadable": len(bad), "unreadable_raw": sorted(set(bad)),
                                       "ge200_exactly_1000": sum(1 for v in big if v == 1000),
                                       "ge200_1001_plus": sum(1 for v in big if v > 1000),
                                       "ge200_200_999": sum(1 for v in big if v < 1000)})
            if not big_vals:
                continue
            day_dfs.setdefault(d, {})[lab] = modes
            cn, src = class_name(d, lab)
            rows.append({
                "date": d, "race": lab, "modes": kinds, "n_modes": len(modes),
                "group": "表が馬番1〜3だけの6日" if d in SIX_DAYS else "ほかの日",
                "all_exactly_1000": all(v == 1000 for v in big_vals),
                "has_200_999": any(v < 1000 for v in big_vals),
                "candidates": sc.get(lab), "scored_record": lab in sc,
                "class_name": cn, "class_source": src,
                "steeple": None if cn is None else ("障害" in cn),
                "result_file": has_result(d, lab),
            })

    days = sorted({r["date"] for r in rows})
    print("表の数", dict(tables), "合計", sum(tables.values()))
    print("レースごとの表の数（モードの数: レース数）", dict(sorted(modes_per_race.items())))
    print("整数として読めない値（元の文字: 件数）", dict(raw_unreadable))
    t200 = [t for t in table_rows if t["ge200"]]
    print("200以上の値がある表", len(t200),
          "うち読めない値を含む", sum(1 for t in t200 if t["unreadable"]),
          "空のセルを含む", sum(1 for t in t200 if t["empty"]),
          "200〜999の値を含む", sum(1 for t in t200 if t["ge200_200_999"]))
    print("読めた整数の無い表", [(t["date"], t["race"], t["mode"], t["rows"], t["empty"], t["unreadable_raw"])
                         for t in table_rows if t["class"] == "読めた整数なし"])
    print("\n200以上の値のあるレース", len(rows), "日数", len(days), "範囲", days[0], "〜", days[-1])
    for g in ("表が馬番1〜3だけの6日", "ほかの日"):
        rs = [r for r in rows if r["group"] == g]
        print(f"\n[{g}] レース {len(rs)} 日数 {len({r['date'] for r in rs})} 表の数 {sum(r['n_modes'] for r in rs)}")
        print("  値がすべて1000ちょうど", sum(r["all_exactly_1000"] for r in rs),
              " 200〜999の値を含む", sum(r["has_200_999"] for r in rs))
        print("  候補の延べ", sum(r["candidates"] or 0 for r in rs),
              "候補のあるレース", sum(1 for r in rs if r["candidates"]),
              "候補0頭", sum(1 for r in rs if r["candidates"] == 0),
              "採点記録なし", sum(1 for r in rs if not r["scored_record"]))
        print("  障害戦の判定", dict(collections.Counter(
            "障害" if r["steeple"] else ("条件の記録なし" if r["steeple"] is None else "平地") for r in rs)))
        print("  判定の出所", dict(collections.Counter(r["class_source"] for r in rs)))
        print("  結果ファイルにレースがある", dict(collections.Counter(r["result_file"] for r in rs)))
        nc = [(r["date"], r["race"], r["result_file"]) for r in rs if r["steeple"] is None]
        if nc:
            print("  条件の記録なし", nc)

    # 旧集計（候補の一覧の1000以上）との比較
    old_days = {}
    for p in sorted(OUT.glob("[0-9]*/全場_3指数重複馬.csv")):
        df = read_csv(p.parent.name)
        n = 0
        for _, row in df.iterrows():
            vals = [float(row[c]) for c in CSV_IDX if c in df.columns and str(row[c]) not in ("", "nan")]
            n += any(v >= 1000 for v in vals)
        if n:
            old_days[p.parent.name] = n
    print("\n[旧集計: 今の候補の一覧に1000以上の行がある日]", len(old_days), "日", sum(old_days.values()), "行")
    import io, pandas as pd
    hist_days = {}
    for p in sorted(OUT.glob("[0-9]*/全場_3指数重複馬.csv")):
        d = p.parent.name
        for line in csv_commits(d):
            h = line.split()[0]
            df = pd.read_csv(io.StringIO(git("show", f"{h}:output/{d}/全場_3指数重複馬.csv")), dtype=str)
            df.columns = [c.lstrip("\ufeff") for c in df.columns]
            n = sum(any(float(row[c]) >= 1000 for c in CSV_IDX if str(row[c]) not in ("", "nan"))
                    for _, row in df.iterrows())
            if n and d not in old_days:
                hist_days.setdefault(d, []).append((h[:7], n))
    print("  今の一覧には無いが、git の過去の版に1000以上の行がある日", hist_days)
    print("  新集計に含まれない日", sorted(set(old_days) - set(days)))
    added = sorted(set(days) - set(old_days))
    print("  新集計だけにある日", len(added))
    add_rows = [r for r in rows if r["date"] in added]
    print("   その日の対象レース", len(add_rows),
          "候補あり", sum(1 for r in add_rows if r["candidates"]),
          "候補0頭", sum(1 for r in add_rows if r["candidates"] == 0),
          "採点記録なし", sum(1 for r in add_rows if not r["scored_record"]),
          "200〜999の値を含む", sum(r["has_200_999"] for r in add_rows))
    add_with_cand = [(r["date"], r["race"], r["candidates"]) for r in add_rows if r["candidates"]]
    print("   候補があるのに旧集計に無いレース", add_with_cand)

    # ペーパートレード期間の全対象レース
    print("\n[ペーパートレード期間（%s〜）の対象レースすべて]" % PAPER_START)
    paper = []
    for r in rows:
        if r["date"] < PAPER_START:
            continue
        pb = jload(OUT / r["date"] / "paper_bets.json")
        invr = races_of(jload(OUT / r["date"] / "invalidated.json"))
        if pb is None:
            st = "paper_bets.json 無し"
        elif r["race"] in pb.get("races", {}):
            st = "買い目あり"
        elif r["race"] in pb.get("skipped_races", {}):
            st = "見送り: " + str(pb["skipped_races"][r["race"]])
        else:
            st = "races にも skipped_races にも無い"
        p = {"date": r["date"], "race": r["race"], "candidates": r["candidates"],
             "scored_record": r["scored_record"], "paper": st, "invalidated": r["race"] in invr,
             "steeple": r["steeple"], "all_exactly_1000": r["all_exactly_1000"]}
        paper.append(p)
    print("  レース", len(paper), dict(collections.Counter(p["paper"].split(":")[0] for p in paper)))
    for p in paper:
        print("  ", p)

    # 当時のコードの再現（候補のある対象レース）
    print("\n[当時のコードの再現: find_triple_top5_rows と候補の一覧の馬番]")
    repro = []
    for r in rows:
        if not r["candidates"]:
            continue
        df = read_csv(r["date"])
        if df is None:
            repro.append({**{k: r[k] for k in ("date", "race")}, "status": "候補の一覧なし"})
            continue
        venue = re.match(r"^\D+", r["race"]).group(0)
        num = r["race"][len(venue):]
        csv_nums = sorted(df[(df["開催場"] == venue) & (df["レース番号"] == num)]["馬番"].astype(str))
        fn, info = old_finder(r["date"])
        if fn is None:
            repro.append({"date": r["date"], "race": r["race"], **info, "status": "再現できない"})
            continue
        got = sorted(str(x["馬番"]) for x in fn(r["race"], day_dfs[r["date"]][r["race"]]))
        repro.append({"date": r["date"], "race": r["race"], **info, "csv": csv_nums, "old_code": got,
                      "status": "一致" if got == csv_nums else "不一致"})
    print("   基準のコミット", BASE)
    print("  ", dict(collections.Counter(x["status"] for x in repro)),
          "使った scraper.py のコミット", dict(collections.Counter(x.get("scraper_commit") for x in repro)))
    print("   入力の候補の一覧が選んだコミットの内容と一致",
          dict(collections.Counter(x.get("input_csv_equals_commit") for x in repro)))
    for x in repro:
        if x["status"] != "一致" or not x.get("input_csv_equals_commit") or x["date"] >= PAPER_START:
            print("  ", {k: x.get(k) for k in ("date", "race", "csv_commit", "scraper_commit",
                                               "input_csv_equals_commit", "csv", "old_code", "status")})
    multi = sorted({x["date"] for x in repro if x.get("csv_commits_count", 0) > 1})
    print("   候補の一覧を2回以上コミットした日（対象レースに候補のある日のうち）", multi)
    for d in sorted({r["date"] for r in rows if r["date"] >= PAPER_START}):
        import datetime
        mt = datetime.datetime.fromtimestamp((OUT / f"{d}.xlsx").stat().st_mtime).isoformat(timespec="seconds")
        print("   ", d, "表の更新時刻", mt, "候補の一覧のコミット", [c[:7] + c[40:] for c in csv_commits(d)])

    after = manifest()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    print("\n読んだ入力ファイル", len(after), "実行中に変わったファイル", changed)
    if changed:
        builtins.print("実行中に入力が変わったので、結果ファイルを書かずに止める", file=sys.stderr)
        sys.exit(1)
    json.dump({"input_dir": str(OUT), "base_commit": BASE, "files": after, "changed_during_run": changed},
              open(RESULTS / "hidden_value_scope_manifest.json", "w"), ensure_ascii=False, indent=0)
    json.dump({"base_commit": BASE, "tables": dict(tables), "modes_per_race": dict(modes_per_race),
               "unreadable_raw": dict(raw_unreadable), "table_detail": table_rows, "races": rows,
               "old_csv_days": old_days, "old_csv_history_only_days": hist_days,
               "paper": paper, "reproduction": repro},
              open(RESULTS / "hidden_value_scope.json", "w"), ensure_ascii=False, indent=1)
    (RESULTS / "hidden_value_scope.txt").write_text("\n".join(LOG) + "\n")


if __name__ == "__main__":
    main()
