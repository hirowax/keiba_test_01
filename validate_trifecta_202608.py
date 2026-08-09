"""
Issue #2: 3連複の事前登録検証（docs/roi_improvement_plan.md 柱3 Step 2）

事前登録した買い目定義・判定基準（変更禁止）に従い、◎-紐2頭の3連複ベタ買いを
JRA公式払戻（payouts_jra.json）で精算し、回収率・的中率・外れ値除外後回収率を集計する。

買い目定義（himo_analysis_202607.md §3-4 / roi_improvement_plan.md 柱3 Step 2）:
- 対象レース: ◎（スコア1位かつ8pt以上）が存在するレース
- 軸: ◎
- 紐: B（◎以外の3指数重複馬）∪ D（単指数1位・非重複）∪ C（2指数重複・非重複）
- 買い目: ◎-紐2頭の3連複 C(k,2)点（k=紐頭数。k<2は見送り）
- 1点100円

判定（事前固定）:
- 窓: クリーン窓(20260328〜) / 全期間 の2窓 + 月別推移
- 指標: 回収率・的中率・上位3的中除外後回収率
- 「有望」判定: 素の回収率≥110% かつ 上位3本除外後≥70% かつ 的中数≥15

使い方: python3 validate_trifecta_202608.py
"""
import json
from itertools import combinations
from pathlib import Path

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
SUMMARY_DIR = BASE_DIR / "summary"

CLEAN_WINDOW_START = "20260328"
# himo_analysis_202607.md §5 の照合窓（netkeiba集計値 馬連27.4%/ワイド67.0% を出した期間）
HIMO_WINDOW_START = "20260328"
HIMO_WINDOW_END = "20260705"
# himo §5 が「db.netkeiba.comに払戻未反映で不算入」とした3レース（的中扱いだが集計から欠落）。
# JRA公式にはこれらの払戻があるため、netkeiba集計値と照合する際はこの3件を除外して比較する。
NETKEIBA_EXCLUDED = {("20260704", "函館6R"), ("20260704", "函館7R"), ("20260705", "小倉4R")}
NETKEIBA_UMAREN_REF = 27.4
NETKEIBA_WIDE_REF = 67.0
MIN_SCORE = 8  # ◎の最低スコア
BET_UNIT = 100  # 1点100円


def load_json(path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_summary_index(summary):
    """summary/{date}.json を {label: race_obj} に変換"""
    idx = {}
    if not summary:
        return idx
    for venue, races in summary.items():
        for race in races:
            idx[race["label"]] = race
    return idx


def himo_set(scored, summary_race):
    """
    紐集合 B∪C∪D を馬番(int)のsetで返す。◎自身は含めない。

    B: scored[1:]（◎以外の3指数重複馬）
    D: 各指数トップ1のうち非重複馬（index.html buildSingleTopMap と同一ロジック）
    C: トップ5に2指数以上登場する非重複馬
    """
    # ◎ = scored[0]
    axis_num = int(scored[0]["馬番"])

    # B: scored[1:] の馬番
    b_nums = {int(h["馬番"]) for h in scored[1:]}

    # 3指数重複馬の名前集合（C/Dの除外に使う）
    triple_names = {h["name"] for h in summary_race.get("triple", [])}

    # 各指数のトップ5リスト
    cats = ["average", "distance", "course"]

    # D: 各指数トップ1（非重複）
    d_nums = set()
    for cat in cats:
        lst = summary_race.get(cat, [])
        if not lst:
            continue
        top = lst[0]
        if top["name"] in triple_names:
            continue
        d_nums.add(int(top["num"]))

    # C: トップ5に2指数以上登場（非重複）
    appear_count = {}  # name -> (count, num)
    for cat in cats:
        seen_in_cat = set()
        for h in summary_race.get(cat, []):
            name = h["name"]
            if name in seen_in_cat:
                continue
            seen_in_cat.add(name)
            if name in triple_names:
                continue
            cnt, num = appear_count.get(name, (0, int(h["num"])))
            appear_count[name] = (cnt + 1, num)
    c_nums = {num for (cnt, num) in appear_count.values() if cnt >= 2}

    himo = (b_nums | c_nums | d_nums) - {axis_num}
    return axis_num, himo


def maru_num(scored, summary_race):
    """
    ◯ = スコア2位の重複馬。いなければ単指数1位馬のうち指数値最上位（馬連/ワイド検証用）。
    馬番(int)を返す。存在しなければ None。
    """
    if len(scored) >= 2:
        return int(scored[1]["馬番"])
    # 単指数1位・非重複馬のうち val 最大
    triple_names = {h["name"] for h in summary_race.get("triple", [])}
    best = None  # (val, num)
    for cat in ["average", "distance", "course"]:
        lst = summary_race.get(cat, [])
        if not lst:
            continue
        top = lst[0]
        if top["name"] in triple_names:
            continue
        val = top.get("val") or 0
        if best is None or val > best[0]:
            best = (val, int(top["num"]))
    return best[1] if best else None


def combo_key(nums):
    """馬番のリストを昇順で '-' 連結（3連複キー）"""
    return "-".join(str(n) for n in sorted(nums))


def analyze():
    dates = sorted(
        d.name for d in OUTPUT_DIR.iterdir()
        if d.is_dir() and d.name.isdigit()
        and (d / "pickup_scores.json").exists()
        and (d / "payouts_jra.json").exists()
    )

    # レース単位の結果を貯める
    records = []  # dict per bet race
    # 馬連/ワイド再集計用
    umaren_records = []  # (date, hit_bool, return_yen)
    wide_records = []

    skipped_no_summary = 0
    skipped_no_axis = 0
    skipped_k_lt2 = 0

    for date in dates:
        pickup = load_json(OUTPUT_DIR / date / "pickup_scores.json")
        payouts = load_json(OUTPUT_DIR / date / "payouts_jra.json")
        summary = load_json(SUMMARY_DIR / f"{date}.json")
        sidx = build_summary_index(summary)

        for label, rdata in pickup.get("races", {}).items():
            scored = rdata.get("scored", [])
            if not scored:
                continue
            # ◎ = スコア1位かつ8pt以上
            if scored[0].get("score", 0) < MIN_SCORE:
                skipped_no_axis += 1
                continue
            summary_race = sidx.get(label)
            if summary_race is None:
                skipped_no_summary += 1
                continue

            axis, himo = himo_set(scored, summary_race)
            race_payout = payouts.get(label, {})

            # ---- 馬連/ワイド（◎-◯ベタ買い）再集計 ----
            o_num = maru_num(scored, summary_race)
            if o_num is not None and o_num != axis:
                pair_key = combo_key([axis, o_num])
                # 馬連
                um = race_payout.get("umaren", {})
                um_hit = pair_key in um
                umaren_records.append((date, label, um_hit, um.get(pair_key, 0) if um_hit else 0))
                # ワイド
                wd = race_payout.get("wide", {})
                wd_hit = pair_key in wd
                wide_records.append((date, label, wd_hit, wd.get(pair_key, 0) if wd_hit else 0))

            # ---- 3連複 ----
            k = len(himo)
            if k < 2:
                skipped_k_lt2 += 1
                continue

            # 買い目: ◎-紐i-紐j の全 C(k,2)
            bet_keys = set()
            for h1, h2 in combinations(sorted(himo), 2):
                bet_keys.add(combo_key([axis, h1, h2]))

            cost = len(bet_keys) * BET_UNIT

            # 精算: trio の当選組合せが買い目に含まれるか
            trio = race_payout.get("trio", {})
            ret = 0
            hit = False
            for win_combo, yen in trio.items():
                # 当選キーも昇順化して比較（payoutsは昇順キーだが念のため正規化）
                norm = combo_key([int(x) for x in win_combo.split("-")])
                if norm in bet_keys:
                    ret += yen  # 1点100円あたりの払戻
                    hit = True

            records.append({
                "date": date,
                "label": label,
                "k": k,
                "n_bets": len(bet_keys),
                "cost": cost,
                "return": ret,
                "hit": hit,
                "trio_win": list(trio.keys()),
            })

    return records, umaren_records, wide_records, {
        "skipped_no_axis": skipped_no_axis,
        "skipped_no_summary": skipped_no_summary,
        "skipped_k_lt2": skipped_k_lt2,
        "n_dates": len(dates),
        "dates": dates,
    }


def summarize(records, label):
    """回収率・的中率・上位3除外後回収率を集計して dict で返す"""
    if not records:
        return None
    n = len(records)
    total_cost = sum(r["cost"] for r in records)
    total_ret = sum(r["return"] for r in records)
    hits = [r for r in records if r["hit"]]
    n_hit = len(hits)
    roi = total_ret / total_cost * 100 if total_cost else 0
    hit_rate = n_hit / n * 100 if n else 0

    # 上位3的中除外後
    top3 = sorted((r["return"] for r in records), reverse=True)[:3]
    ret_ex = total_ret - sum(top3)
    roi_ex = ret_ex / total_cost * 100 if total_cost else 0

    avg_k = sum(r["k"] for r in records) / n
    avg_bets = sum(r["n_bets"] for r in records) / n

    return {
        "label": label,
        "n_races": n,
        "n_hit": n_hit,
        "hit_rate": hit_rate,
        "total_cost": total_cost,
        "total_return": total_ret,
        "roi": roi,
        "roi_ex_top3": roi_ex,
        "avg_k": avg_k,
        "avg_bets": avg_bets,
    }


def summarize_pair(pair_records, label):
    """馬連/ワイドの的中率・回収率。pair_records = [(date, label, hit, yen)]"""
    if not pair_records:
        return None
    n = len(pair_records)
    n_hit = sum(1 for (_, _, hit, _) in pair_records if hit)
    total_ret = sum(y for (_, _, _, y) in pair_records)
    total_cost = n * BET_UNIT
    return {
        "label": label,
        "n_races": n,
        "n_hit": n_hit,
        "hit_rate": n_hit / n * 100,
        "roi": total_ret / total_cost * 100 if total_cost else 0,
    }


def reconcile_netkeiba(pair_records, ref, name):
    """
    himo窓の馬連/ワイドを netkeiba集計値と照合する。
    netkeibaは不算入3件（NETKEIBA_EXCLUDED）を「レース数=分母には数えるが払戻は欠落」
    として集計していた（himo §5）。同じ扱い（分母は維持し、3件の払戻のみ0にする）で
    JRAを再集計し、apples-to-apples で比較する。
    """
    window = [x for x in pair_records if HIMO_WINDOW_START <= x[0] <= HIMO_WINDOW_END]
    n = len(window)
    n_excl_in_window = sum(1 for (d, l, _, _) in window if (d, l) in NETKEIBA_EXCLUDED)
    # 分母nは維持し、除外3件の払戻だけを numerator から落とす
    ret_adj = sum(y for (d, l, h, y) in window if h and (d, l) not in NETKEIBA_EXCLUDED)
    roi_adj = ret_adj / (n * BET_UNIT) * 100 if n else 0
    diff = roi_adj - ref
    print(f"\n### {name} netkeiba照合（不算入3件の払戻を除外）")
    print(f"  JRA窓 {n}R（うちnetkeiba不算入 {n_excl_in_window}件の払戻を0扱い）")
    print(f"  補正後回収率: {roi_adj:.1f}%  vs netkeiba集計値 {ref}%  差 {diff:+.1f}pt "
          f"→ {'整合(±2pt以内)' if abs(diff) <= 2 else '不整合'}")
    return abs(diff) <= 2


def print_table(title, s):
    if s is None:
        print(f"\n### {title}: 該当レースなし")
        return
    print(f"\n### {title}")
    print(f"  対象レース: {s['n_races']}  的中: {s['n_hit']}  的中率: {s['hit_rate']:.1f}%")
    print(f"  平均紐頭数 k: {s['avg_k']:.2f}  平均点数: {s['avg_bets']:.1f}点/R")
    print(f"  総投資: {s['total_cost']:,}円  総払戻: {s['total_return']:,}円")
    print(f"  回収率: {s['roi']:.1f}%")
    print(f"  上位3的中除外後 回収率: {s['roi_ex_top3']:.1f}%")


def main():
    records, umaren_records, wide_records, meta = analyze()

    print("=" * 60)
    print("Issue #2: 3連複 事前登録検証")
    print("=" * 60)
    print(f"対象日数: {meta['n_dates']}  (payouts_jra.json と pickup_scores.json が揃う日)")
    print(f"期間: {meta['dates'][0]} 〜 {meta['dates'][-1]}")
    print(f"スキップ: ◎不在(8pt未満) {meta['skipped_no_axis']}R / "
          f"summary欠落 {meta['skipped_no_summary']}R / 紐<2頭(見送り) {meta['skipped_k_lt2']}R")

    clean = [r for r in records if r["date"] >= CLEAN_WINDOW_START]

    print("\n" + "=" * 60)
    print("【3連複】")
    print_table("全期間", summarize(records, "全期間"))
    print_table(f"クリーン窓（{CLEAN_WINDOW_START}〜）", summarize(clean, "クリーン窓"))

    # 月別推移
    print("\n### 月別推移（3連複）")
    months = sorted({r["date"][:6] for r in records})
    print(f"  {'月':<8}{'R数':>5}{'的中':>5}{'的中率':>8}{'回収率':>9}{'除外後':>9}")
    for m in months:
        mr = [r for r in records if r["date"][:6] == m]
        s = summarize(mr, m)
        print(f"  {m:<8}{s['n_races']:>5}{s['n_hit']:>5}{s['hit_rate']:>7.1f}%"
              f"{s['roi']:>8.1f}%{s['roi_ex_top3']:>8.1f}%")

    # 的中明細
    print("\n### 的中明細（3連複・払戻額つき）")
    hits = sorted((r for r in records if r["hit"]), key=lambda x: x["return"], reverse=True)
    print(f"  {'日付':<10}{'レース':<10}{'点数':>5}{'投資':>7}{'払戻':>9}")
    for r in hits:
        print(f"  {r['date']:<10}{r['label']:<10}{r['n_bets']:>5}{r['cost']:>7}{r['return']:>9}"
              f"  当選{r['trio_win']}")

    # 馬連・ワイド再集計（netkeiba集計値との整合確認）
    print("\n" + "=" * 60)
    print("【馬連・ワイド（◎-◯ベタ買い）JRA払戻再集計】")
    clean_um = [x for x in umaren_records if x[0] >= CLEAN_WINDOW_START]
    clean_wd = [x for x in wide_records if x[0] >= CLEAN_WINDOW_START]
    himo_um = [x for x in umaren_records if HIMO_WINDOW_START <= x[0] <= HIMO_WINDOW_END]
    himo_wd = [x for x in wide_records if HIMO_WINDOW_START <= x[0] <= HIMO_WINDOW_END]
    for name, recs in [
        ("馬連 全期間", umaren_records),
        ("馬連 クリーン窓", clean_um),
        ("馬連 himo照合窓(20260328〜20260705・生値)", himo_um),
        ("ワイド 全期間", wide_records),
        ("ワイド クリーン窓", clean_wd),
        ("ワイド himo照合窓(20260328〜20260705・生値)", himo_wd),
    ]:
        s = summarize_pair(recs, name)
        if s is None:
            print(f"\n### {name}: 該当なし")
            continue
        print(f"\n### {name}")
        print(f"  対象: {s['n_races']}R  的中: {s['n_hit']}  的中率: {s['hit_rate']:.1f}%  "
              f"回収率: {s['roi']:.1f}%")

    # netkeiba集計値との厳密照合（受入基準3）
    print("\n" + "-" * 60)
    print("netkeiba集計値との照合（受入基準3: ±2pt以内で整合）")
    print("※netkeibaは払戻未反映の3件(20260704函館6R/7R・20260705小倉4R)を不算入。")
    print("　JRA公式にはこの払戻があるため、同3件を除外して apples-to-apples 比較する。")
    um_ok = reconcile_netkeiba(umaren_records, NETKEIBA_UMAREN_REF, "馬連")
    wd_ok = reconcile_netkeiba(wide_records, NETKEIBA_WIDE_REF, "ワイド")
    print(f"\n  受入基準3判定: {'PASS（両券種±2pt以内）' if um_ok and wd_ok else 'FAIL'}")

    # 有望判定
    print("\n" + "=" * 60)
    print("【有望判定（事前固定基準）】")
    print("  基準: 素の回収率≥110% かつ 上位3本除外後≥70% かつ 的中数≥15")
    for tag, recs in [("全期間", records), ("クリーン窓", clean)]:
        s = summarize(recs, tag)
        ok = s["roi"] >= 110 and s["roi_ex_top3"] >= 70 and s["n_hit"] >= 15
        print(f"  {tag}: 回収率{s['roi']:.1f}% / 除外後{s['roi_ex_top3']:.1f}% / "
              f"的中{s['n_hit']} → {'有望' if ok else '基準未達'}")


if __name__ == "__main__":
    main()
