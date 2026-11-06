"""
タスク3の参考集計の前に、6日の払戻（payouts_jra.json）の単勝・複勝を券種ごとに確かめる（2026-11-07。外部レビュー指摘）。
ページは取りに行かない。成績（候補の馬の払戻）は集計しない。払戻があるレースすべてを、候補がいるかに関係なく調べる。

券種ごとの確かめ方（結果は output/{日}/race_results_all.json）:
- 単勝:
  - 払戻の馬番の集まり＝着順1の馬の集まり。
  - 金額＝確定単勝オッズ×100（四捨五入）。1着が同着（着順1が2頭以上）のレースは払戻が分けられるので、金額の不一致は「同着」として数え、
    取得異常にしない。同着でないのに合わないものは「不一致」。
- 複勝:
  - 払戻の馬番の集まり＝着順が K 以内の馬の集まり（K は出走頭数が8頭以上なら3、5〜7頭なら2。4頭以下は複勝の発売なし）。
    同着は同じ着順なので、K 位に同着があれば全員が入る。
  - 出走頭数は、着順のある馬と、状態が「中止」「失格」の馬（発走した馬）。取消・除外は数えない。
  - 金額は 100円以上か（正解データが無いので、金額そのものの正しさは確かめられない）。
- 払戻の記録が無いレース・券種の欄が無いレースは、券種ごとに件数とレース名を出す。

実行: python3 research/task3_index_refetch_20261106/check_payouts.py [--out 出力.json]
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DAYS = ["20251102", "20260110", "20260111", "20260117", "20260118", "20260124"]


def place_k(starters):
    return 3 if starters >= 8 else (2 if starters >= 5 else None)


def check_race(r, pay):
    """1レースの単勝・複勝の確認。戻り値 {券種: (判定, 詳細)}"""
    out = {}
    ranked = {str(h["num"]): h for h in r["horses"] if isinstance(h.get("rank"), int)}
    started = len(ranked) + sum(1 for o in r.get("others", []) if o.get("status") in ("中止", "失格"))
    winners = {n for n, h in ranked.items() if h["rank"] == 1}
    t = (pay or {}).get("tansho")
    if pay is None:
        out["tansho"] = ("払戻の記録が無い", None)
    elif not t:
        out["tansho"] = ("単勝の欄が無い", None)
    elif set(t) != winners:
        out["tansho"] = ("馬番が合わない", {"払戻": sorted(t), "着順1": sorted(winners)})
    else:
        bad = {n: (t[n], ranked[n].get("odds")) for n in t
               if ranked[n].get("odds") is None or round(ranked[n]["odds"] * 100) != t[n]}
        if not bad:
            out["tansho"] = ("一致", None)
        elif len(winners) >= 2:
            out["tansho"] = ("同着（金額はオッズ×100と違う）", bad)
        else:
            out["tansho"] = ("金額が合わない", bad)
    f = (pay or {}).get("fukusho")
    k = place_k(started)
    if pay is None:
        out["fukusho"] = ("払戻の記録が無い", None)
    elif k is None:
        out["fukusho"] = ("発売なし（出走4頭以下）" if not f else "4頭以下なのに複勝の欄がある", {"出走": started})
    elif not f:
        out["fukusho"] = ("複勝の欄が無い", {"出走": started})
    else:
        want = {n for n, h in ranked.items() if h["rank"] <= k}
        if set(f) != want:
            out["fukusho"] = ("馬番が合わない", {"払戻": sorted(f), f"{k}着以内": sorted(want), "出走": started})
        elif any(v < 100 for v in f.values()):
            out["fukusho"] = ("100円未満の金額", f)
        else:
            out["fukusho"] = ("馬番が一致・金額は100円以上", None)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    args = ap.parse_args()
    result, summary = {"races": {}}, {"tansho": Counter(), "fukusho": Counter()}
    inputs = {}
    for d in DAYS:
        rp, pp = ROOT / "output" / d / "race_results_all.json", ROOT / "output" / d / "payouts_jra.json"
        inputs[str(rp.relative_to(ROOT))] = hashlib.sha256(rp.read_bytes()).hexdigest()
        races = json.load(open(rp, encoding="utf-8"))["races"]
        pays = {}
        if pp.exists():
            inputs[str(pp.relative_to(ROOT))] = hashlib.sha256(pp.read_bytes()).hexdigest()
            pays = json.load(open(pp, encoding="utf-8"))
        for label, r in sorted(races.items(), key=lambda kv: kv[1]["race_id"]):
            c = check_race(r, pays.get(label))
            result["races"][f"{d} {label}"] = {k: {"判定": v[0], "詳細": v[1]} for k, v in c.items()}
            for k, v in c.items():
                summary[k][v[0]] += 1
    result["summary"] = {k: dict(v) for k, v in summary.items()}
    result["inputs_sha256"] = inputs
    for k, v in summary.items():
        print(k, dict(v))
    for key, v in result["races"].items():
        for kind, x in v.items():
            if x["判定"] not in ("一致", "馬番が一致・金額は100円以上", "払戻の記録が無い"):
                print("  ", key, kind, x["判定"], x["詳細"])
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
