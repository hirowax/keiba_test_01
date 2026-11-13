"""タスク3: 保存済みの指数の表（output/YYYYMMDD.xlsx）の馬番・馬名を、結果と全頭照合する。
結果の選び方: レースごとに、race_results_all.json（取り直し）にそのレースがあればそれを使い、無ければ race_results.json（旧版。着順が数字でない馬を捨てていた）を使う。
race_results_all.json は着順のある馬（horses）と着順が数字でない馬（others）の両方を使う。
2段目の見出し行（1列目が「5走前」「着度数」）は読み飛ばす。馬名は空白の違いだけを許す。"""
import json, re, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from xlsx_blocks import load_day
from common import OUT
import sys as _sys  # 2026-11-13〜: 結果の保存が不完全な日があれば止める
_sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from data_quality import stop_if_any_results_save_incomplete
stop_if_any_results_save_incomplete()
SP = re.compile(r"[\s　]+")
SUB = ("5走前", "着度数")

def results_of(date):
    res, src = {}, {}
    p = OUT / date / "race_results.json"
    if p.exists():
        for l, rows in json.load(open(p)).items():
            res[l] = {str(h["num"]): h["name"] for h in rows}; src[l] = "race_results"
    p = OUT / date / "race_results_all.json"
    if p.exists():
        for l, r in json.load(open(p))["races"].items():
            m = {str(h["num"]): h["name"] for h in r["horses"]}
            m.update({str(h["num"]): h["name"] for h in r.get("others", [])})
            res[l] = m; src[l] = "race_results_all"
    return res, src

cnt = collections.Counter(); detail = []
by_day = collections.Counter()
days = sorted(f.stem for f in OUT.glob("*.xlsx") if re.fullmatch(r"\d{8}", f.stem))
for date in days:
    res, src = results_of(date)
    if not res: cnt["結果ファイルが無い日"] += 1; continue
    for (sh, label), modes in load_day(OUT / f"{date}.xlsx").items():
        if label not in res: cnt["結果にレースが無い表のレース"] += 1; continue
        r = res[label]
        for mk, df in modes.items():
            cnt["表"] += 1
            rows = df[~df.iloc[:, 0].astype(str).str.strip().isin(SUB)]
            got = {}
            for _, x in rows.iterrows():
                n = str(x["馬番"]).strip(); got.setdefault(n, []).append(str(x["馬名"]))
            bad = []
            for n, names in got.items():
                if len(names) > 1: bad.append(("馬番の重複", n))
                elif n not in r: bad.append(("結果に無い馬番", n))
                elif SP.sub("", names[0]) != SP.sub("", r[n]): bad.append(("馬名が違う", f"{n} {names[0]} / {r[n]}"))
            for n in r:
                if n not in got: bad.append(("表に無い馬", n))
            cnt["馬"] += len(got)
            if bad:
                cnt["不一致のある表"] += 1
                for k, v in bad:
                    cnt[k] += 1; by_day[(k, date, src[label])] += 1
                    detail.append({"種類": k, "日": date, "レース": label, "指数": mk, "内容": v, "結果の元": src[label]})
for k in ("馬名が違う", "馬番の重複", "結果に無い馬番", "表に無い馬"): cnt.setdefault(k, 0)
summary = {"集計": dict(cnt), "種類・日・結果の元ごとの件数": [[k, d, s, n] for (k, d, s), n in sorted(by_day.items())]}
json.dump({"summary": summary, "detail": detail}, open(Path(__file__).parent / "results/index_vs_results.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(summary, ensure_ascii=False, indent=1))
