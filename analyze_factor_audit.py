#!/usr/bin/env python3
"""
ファクター再現性監査スクリプト v2（2026-06 調査用）

重要な前提（このスクリプトで発見されたデータ汚染への対処）:
  - 2026-03-28 より前の pickup_scores.json は遡及生成 or 全体rescoreにより、
    前走系ファクター（prev_idx/前走好走/中4週/同距離）が「未来の前走」で計算されている。
    data_top系（ピックアップ/出走馬分析）と中4週は全てゼロ。
  - → 現行ファクターの as-recorded 検証は 2026-03-28 以降（クリーン窓）のみで行う。
  - → 前走系の候補ファクターは、自前の race_results.json 履歴から
       「対象日より前の直近レース」を再構築して全期間で検証する（日付整合・汚染なし）。

検証原則（docs/anaba_research_log.md 準拠）:
  - 時系列分割で全期間プラスのリフトのみ「再現」と判定
  - 単勝ROIは外れ値（勝ちオッズ上位2頭）除去後を併記
  - 複勝ROI: place_odds がある日付のみ・8頭以上レースのみ・分母は全対象馬
  - 人気帯コントロールで「人気の代理変数」を排除
"""
import json
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
CLEAN_START = "20260328"  # これ以降がリアルタイム採点（前走データ正当）


def parse_class(class_name: str) -> str:
    if not class_name:
        return ""
    if "新馬" in class_name: return "新馬"
    if "未勝利" in class_name: return "未勝利"
    if "１勝" in class_name or "1勝" in class_name: return "1勝"
    if "２勝" in class_name or "2勝" in class_name: return "2勝"
    if "３勝" in class_name or "3勝" in class_name: return "3勝"
    return "OP他"


def load_results_history():
    """自前のrace_results全体から horse_id → [(date, rank, pop, dist, surface, venue)] を構築"""
    hist = defaultdict(list)
    dates = sorted(d.name for d in OUTPUT_DIR.iterdir()
                   if d.is_dir() and re.match(r"^\d{8}$", d.name))
    for date in dates:
        rr_path = OUTPUT_DIR / date / "race_results.json"
        rc_path = OUTPUT_DIR / date / "race_conditions.json"
        if not rr_path.exists():
            continue
        rr = json.load(open(rr_path, encoding="utf-8"))
        rc = json.load(open(rc_path, encoding="utf-8")) if rc_path.exists() else {}
        for race_label, rows in rr.items():
            if not isinstance(rows, list):
                continue
            cond = rc.get(race_label, {}) or {}
            for x in rows:
                hid = x.get("horse_id", "")
                if not hid:
                    continue
                hist[hid].append({
                    "date": date,
                    "rank": x.get("rank"),
                    "pop": x.get("pop"),
                    "dist": cond.get("distance"),
                    "surface": (cond.get("surface") or "")[:1],
                    "n_horses": cond.get("num_horses"),
                })
    for hid in hist:
        hist[hid].sort(key=lambda e: e["date"])
    return hist, dates


def find_prev(hist, hid, date):
    """対象日より前の直近レース（自前DB内）を返す。なければ None"""
    entries = hist.get(hid, [])
    prev = None
    for e in entries:
        if e["date"] < date:
            prev = e
        else:
            break
    return prev


def load_all():
    hist, _ = load_results_history()
    horses = []
    dates = sorted(d.name for d in OUTPUT_DIR.iterdir()
                   if d.is_dir() and re.match(r"^\d{8}$", d.name))
    for date in dates:
        ps_path = OUTPUT_DIR / date / "pickup_scores.json"
        rr_path = OUTPUT_DIR / date / "race_results.json"
        rc_path = OUTPUT_DIR / date / "race_conditions.json"
        if not ps_path.exists() or not rr_path.exists():
            continue
        ps = json.load(open(ps_path, encoding="utf-8"))
        rr = json.load(open(rr_path, encoding="utf-8"))
        rc = json.load(open(rc_path, encoding="utf-8")) if rc_path.exists() else {}
        races = ps.get("races", ps)
        for race_label, rdata in races.items():
            if not isinstance(rdata, dict) or "error" in rdata or "scored" not in rdata:
                continue
            results_list = rr.get(race_label, [])
            if not results_list:
                continue
            res_map = {str(r["num"]): r for r in results_list}
            cond = rc.get(race_label, {}) or {}
            hid_map = rdata.get("horse_id_map", {})
            race_dist = rdata.get("race_dist") or cond.get("distance")
            surface = (cond.get("surface") or "")[:1]
            track_cond = cond.get("condition", "")
            num_horses = cond.get("num_horses") or len(results_list)
            class_cat = parse_class(cond.get("class_name", ""))
            m = re.search(r"(\d+)R", race_label)
            race_num = int(m.group(1)) if m else 0

            for h in rdata["scored"]:
                num = str(h.get("馬番", "")).strip()
                res = res_map.get(num)
                if not res:
                    continue
                rank = res.get("rank")
                odds = res.get("odds", 0) or 0
                place_odds = res.get("place_odds", 0) or 0
                hid = hid_map.get(num, "") or res.get("horse_id", "")

                bd_labels = [b["label"] for b in h.get("breakdown", [])]
                factors = set()
                for lb in bd_labels:
                    if "90以上" in lb: factors.add("prev_idx_90")
                    if "70以上" in lb: factors.add("prev_idx_70")
                    if "前走指数レース内1位" in lb: factors.add("prev_idx_race_top")
                    if "前走好走" in lb: factors.add("prev_good")
                    if "逃げ馬" in lb: factors.add("nige")
                    if "中4週以内" in lb: factors.add("interval_short")
                    if "同距離" in lb: factors.add("same_dist")
                    if "データ分析ピックアップ" in lb: factors.add("data_pickup")
                    if "各データ上位" in lb: factors.add("top3_data")
                    if "出走馬分析" in lb: factors.add("analysis")

                today_pop = h.get("today_pop", "")
                try:
                    pop_val = int(today_pop) if today_pop else (res.get("pop") or 0)
                except (ValueError, TypeError):
                    pop_val = res.get("pop") or 0

                def _rank_no(key):
                    v = str(h.get(key, ""))
                    mm = re.match(r"(\d+)", v)
                    return int(mm.group(1)) if mm else None
                def _fnum(key):
                    try: return float(h.get(key))
                    except (TypeError, ValueError): return None

                # ── 真の前走（自前履歴から再構築・日付整合保証） ──
                tprev = find_prev(hist, hid, date) if hid else None
                t_interval = None
                if tprev:
                    t_interval = (datetime.strptime(date, "%Y%m%d")
                                  - datetime.strptime(tprev["date"], "%Y%m%d")).days

                gate = res.get("gate", "")
                try: gate = int(gate)
                except (ValueError, TypeError): gate = None

                horses.append({
                    "date": date, "race_label": race_label, "race_num": race_num,
                    "num": num, "score": h.get("score", 0),
                    "rank": rank, "odds": odds, "place_odds": place_odds,
                    "pop": pop_val,
                    "is_win": rank == 1,
                    "is_top3": rank is not None and rank <= 3,
                    "factors": factors, "bd_labels": bd_labels,
                    "r_recent": _rank_no("近走平均順位"),
                    "r_dist": _rank_no("当該距離順位"),
                    "r_course": _rank_no("当該コース順位"),
                    "idx_recent": _fnum("近走平均指数"),
                    "idx_dist": _fnum("当該距離指数"),
                    "idx_course": _fnum("当該コース指数"),
                    "tprev": tprev, "t_interval": t_interval,
                    "surface": surface, "track_cond": track_cond,
                    "num_horses": num_horses, "class_cat": class_cat,
                    "race_dist": race_dist, "gate": gate,
                })
    return horses


# place_oddsが存在する日付集合（複勝ROI分母用）
def place_dates(horses):
    ds = defaultdict(bool)
    for h in horses:
        if h["place_odds"]:
            ds[h["date"]] = True
    return {d for d, ok in ds.items() if ok}


PLACE_DATES = set()


def stats(sub):
    n = len(sub)
    if n == 0:
        return dict(n=0, win=0, top3=0, roi=0, roi_trim=0, p_n=0, p_roi=0)
    wins = sorted((h["odds"] for h in sub if h["is_win"]), reverse=True)
    roi = sum(wins) / n * 100
    roi_trim = sum(wins[2:]) / n * 100 if len(wins) > 2 else 0.0
    psub = [h for h in sub if h["date"] in PLACE_DATES and h["num_horses"] >= 8]
    p_roi = (sum(h["place_odds"] for h in psub if h["is_top3"]) / len(psub) * 100) if psub else 0
    return dict(
        n=n,
        win=sum(1 for h in sub if h["is_win"]) / n * 100,
        top3=sum(1 for h in sub if h["is_top3"]) / n * 100,
        roi=roi, roi_trim=roi_trim,
        p_n=len(psub), p_roi=p_roi,
    )


def fmt_row(label, s, base=None):
    lift = ""
    if base and s["n"]:
        lift = f"  lift{s['top3'] - base['top3']:+5.1f}pp"
    return (f"  {label:<40s} N={s['n']:>4d} 勝率{s['win']:5.1f}% 3着内{s['top3']:5.1f}%"
            f" 単ROI{s['roi']:6.1f}%/外れ値除去{s['roi_trim']:6.1f}%"
            f" 複ROI{s['p_roi']:6.1f}%(N={s['p_n']}){lift}")


def make_reporter(horses, periods):
    baseline_by_p = {name: stats([h for h in horses if h["date"] in ds])
                     for name, ds in periods}

    def report(title, pred, min_n=12, extra_periods=True):
        total_sub = [h for h in horses if pred(h)]
        total = stats(total_sub)
        base_total = stats(horses)
        ss = {name: stats([h for h in horses if h["date"] in ds and pred(h)])
              for name, ds in periods}
        main_p = [name for name, _ in periods if not name.startswith("直近")]
        ok_n = all(ss[p]["n"] >= min_n for p in main_p)
        lifts = [ss[p]["top3"] - baseline_by_p[p]["top3"] for p in main_p]
        ok_lift = all(l > 0 for l in lifts)
        verdict = "◎再現" if (ok_n and ok_lift) else ("△N不足" if not ok_n else "✗不安定")
        print(f"■ {title}  [{verdict}]")
        print(fmt_row("全期間", total, base_total))
        for name, _ in periods:
            print(fmt_row(f"  {name}", ss[name], baseline_by_p[name]))
        print()
        return verdict, total

    return report, baseline_by_p


def split_periods(dates_sorted, k=3, recent_days=30):
    n = len(dates_sorted) // k
    periods = []
    for i in range(k):
        chunk = dates_sorted[i * n: (i + 1) * n if i < k - 1 else len(dates_sorted)]
        periods.append((f"P{i+1}({chunk[0][4:]}-{chunk[-1][4:]})", set(chunk)))
    last_dt = datetime.strptime(dates_sorted[-1], "%Y%m%d")
    recent = {d for d in dates_sorted
              if (last_dt - datetime.strptime(d, "%Y%m%d")).days <= recent_days}
    periods.append((f"直近{recent_days}日", recent))
    return periods


def main():
    global PLACE_DATES
    horses = load_all()
    PLACE_DATES = place_dates(horses)
    all_dates = sorted(set(h["date"] for h in horses))
    clean = [h for h in horses if h["date"] >= CLEAN_START]
    clean_dates = sorted(set(h["date"] for h in clean))
    print(f"全データ: {len(horses)}頭/{len(all_dates)}日 ({all_dates[0]}〜{all_dates[-1]})")
    print(f"クリーン窓(リアルタイム採点): {len(clean)}頭/{len(clean_dates)}日 ({clean_dates[0]}〜)")
    print(f"複勝オッズあり日数: {len(PLACE_DATES)}日")
    print()

    # ── 整合性チェック: クリーン窓の中4週ラベル vs 真の前走間隔 ──
    print("=" * 112)
    print("【0】整合性チェック（クリーン窓: 中4週ラベルの日数 vs 自前履歴の真の間隔）")
    print("=" * 112)
    n_chk = n_ok = n_no_hist = 0
    for h in clean:
        lb = next((l for l in h["bd_labels"] if "中4週以内" in l), None)
        if not lb:
            continue
        m = re.search(r"\((\d+)日\)", lb)
        if not m:
            continue
        n_chk += 1
        if h["t_interval"] is None:
            n_no_hist += 1
        elif abs(h["t_interval"] - int(m.group(1))) <= 1:
            n_ok += 1
    print(f"  中4週ラベル付き {n_chk}頭: 履歴一致 {n_ok} / 履歴なし {n_no_hist} / 不一致 {n_chk - n_ok - n_no_hist}")
    print()

    # ════════ A. クリーン窓: 現行ファクター as-recorded ════════
    cp = split_periods(clean_dates, k=3, recent_days=21)
    report_c, base_c = make_reporter(clean, cp)
    print("=" * 112)
    print(f"【A】クリーン窓ベースライン（{CLEAN_START}以降・3指数重複馬全体）")
    print("=" * 112)
    print(fmt_row("全期間", stats(clean)))
    for name, _ in cp:
        print(fmt_row(f"  {name}", base_c[name]))
    print()

    print("=" * 112)
    print("【B】現行ファクター（クリーン窓のみ・as-recorded）")
    print("=" * 112)
    current = [
        ("前走指数90以上(+2)", lambda h: "prev_idx_90" in h["factors"]),
        ("前走指数70-89(+1)", lambda h: "prev_idx_70" in h["factors"]),
        ("前走指数レース内1位(+2)", lambda h: "prev_idx_race_top" in h["factors"]),
        ("前走好走(+2)", lambda h: "prev_good" in h["factors"]),
        ("中4週以内(+2)", lambda h: "interval_short" in h["factors"]),
        ("同距離前走(+1)", lambda h: "same_dist" in h["factors"]),
        ("データ分析ピックアップ(+1)", lambda h: "data_pickup" in h["factors"]),
        ("各データ上位3頭(+1-2)", lambda h: "top3_data" in h["factors"]),
        ("出走馬分析(+1/条件)", lambda h: "analysis" in h["factors"]),
        ("逃げ馬Sペース(+1)", lambda h: "nige" in h["factors"]),
    ]
    for title, pred in current:
        report_c(title, pred)

    print("=" * 112)
    print("【C】人気帯コントロール（クリーン窓・同一人気帯内のファクター有無の3着内率差）")
    print("=" * 112)
    pop_bands = [(1, 3), (4, 6), (7, 18)]
    for title, pred in current:
        cells = []
        for lo, hi in pop_bands:
            band = [h for h in clean if lo <= h["pop"] <= hi]
            w = stats([h for h in band if pred(h)])
            wo = stats([h for h in band if not pred(h)])
            if w["n"] >= 10:
                cells.append(f"{lo}-{hi}人気:{w['top3']:3.0f}vs{wo['top3']:3.0f}%(N={w['n']},{w['top3']-wo['top3']:+.0f}pp)")
            else:
                cells.append(f"{lo}-{hi}人気:N={w['n']}不足")
        print(f"  {title:<22s} " + " | ".join(cells))
    print()

    # ════════ D. 全期間: 当日3指数ファクター（日付整合OK） ════════
    ap = split_periods(all_dates, k=3, recent_days=30)
    report_a, base_a = make_reporter(horses, ap)
    print("=" * 112)
    print("【D】候補: 当日3指数（全期間・3分割）")
    print("=" * 112)
    print(fmt_row("ベースライン全期間", stats(horses)))
    for name, _ in ap:
        print(fmt_row(f"  {name}", base_a[name]))
    print()
    for title, pred in [
        ("当該距離順位=1位", lambda h: h["r_dist"] == 1),
        ("当該コース順位=1位", lambda h: h["r_course"] == 1),
        ("近走平均順位=1位", lambda h: h["r_recent"] == 1),
        ("3指数すべて1位", lambda h: h["r_recent"] == 1 and h["r_dist"] == 1 and h["r_course"] == 1),
        ("3指数順位合計<=5", lambda h: None not in (h["r_recent"], h["r_dist"], h["r_course"])
            and h["r_recent"] + h["r_dist"] + h["r_course"] <= 5),
        ("近走平均指数>=95", lambda h: h["idx_recent"] is not None and h["idx_recent"] >= 95),
        ("近走平均指数>=90", lambda h: h["idx_recent"] is not None and h["idx_recent"] >= 90),
        ("当該距離指数>=105", lambda h: h["idx_dist"] is not None and h["idx_dist"] >= 105),
    ]:
        report_a(title, pred, min_n=20)

    # ════════ E. 全期間: 再構築した前走ファクター（日付整合保証） ════════
    print("=" * 112)
    print("【E】候補: 前走・間隔・距離変化（自前履歴から再構築・全期間・汚染なし）")
    print("    ※前走が自前DBの範囲外（地方・障害・2025/9以前）の馬は対象外")
    print("=" * 112)
    has_prev = [h for h in horses if h["tprev"]]
    print(f"  前走特定可能: {len(has_prev)}/{len(horses)}頭 ({len(has_prev)/len(horses)*100:.0f}%)")
    print()
    ep = split_periods(sorted(set(h["date"] for h in has_prev)), k=3, recent_days=30)
    report_e, base_e = make_reporter(has_prev, ep)
    print(fmt_row("前走特定可能・全期間", stats(has_prev)))
    for name, _ in ep:
        print(fmt_row(f"  {name}", base_e[name]))
    print()

    def tp(h): return h["tprev"]
    for title, pred in [
        ("前走1着", lambda h: tp(h)["rank"] == 1),
        ("前走2-3着", lambda h: tp(h)["rank"] in (2, 3)),
        ("前走1-3着(好走・人気不問)", lambda h: tp(h)["rank"] in (1, 2, 3)),
        ("前走1-6人気かつ1-3着(現行相当)", lambda h: tp(h)["rank"] in (1, 2, 3)
            and tp(h)["pop"] and tp(h)["pop"] <= 6),
        ("前走7人気以下で1-3着(穴激走)", lambda h: tp(h)["rank"] in (1, 2, 3)
            and tp(h)["pop"] and tp(h)["pop"] >= 7),
        ("前走4-5着(惜敗)", lambda h: tp(h)["rank"] in (4, 5)),
        ("前走10着以下(大敗)", lambda h: tp(h)["rank"] and tp(h)["rank"] >= 10),
        ("前走1-3人気で4着以下(巻返し旧)", lambda h: tp(h)["pop"] and tp(h)["pop"] <= 3
            and tp(h)["rank"] and tp(h)["rank"] >= 4),
        ("中4週以内(再構築)", lambda h: h["t_interval"] is not None and h["t_interval"] <= 28),
        ("中2週以内(再構築)", lambda h: h["t_interval"] is not None and h["t_interval"] <= 14),
        ("間隔29-56日", lambda h: h["t_interval"] is not None and 29 <= h["t_interval"] <= 56),
        ("間隔57日以上", lambda h: h["t_interval"] is not None and h["t_interval"] >= 57),
        ("同距離前走±50m(再構築)", lambda h: tp(h)["dist"] and h["race_dist"]
            and abs(tp(h)["dist"] - h["race_dist"]) <= 50),
        ("距離延長>50m", lambda h: tp(h)["dist"] and h["race_dist"]
            and h["race_dist"] - tp(h)["dist"] > 50),
        ("距離短縮>50m", lambda h: tp(h)["dist"] and h["race_dist"]
            and tp(h)["dist"] - h["race_dist"] > 50),
        ("芝⇄ダ替わり", lambda h: tp(h)["surface"] and h["surface"]
            and tp(h)["surface"] != h["surface"]),
        ("前走多頭数(15頭+)→今回", lambda h: tp(h)["n_horses"] and tp(h)["n_horses"] >= 15),
    ]:
        report_e(title, pred, min_n=20)

    # 複合: 前走好走×中4週（現行の2大ファクターのAND・再構築版）
    for title, pred in [
        ("前走1-3着×中4週以内", lambda h: tp(h)["rank"] in (1, 2, 3)
            and h["t_interval"] is not None and h["t_interval"] <= 28),
        ("前走1-3着×同距離", lambda h: tp(h)["rank"] in (1, 2, 3)
            and tp(h)["dist"] and h["race_dist"] and abs(tp(h)["dist"] - h["race_dist"]) <= 50),
    ]:
        report_e(title, pred, min_n=15)

    # 人気帯コントロール（再構築・主要候補）
    print("-" * 112)
    print("  人気帯コントロール（全期間・再構築前走ファクター）")
    for title, pred in [
        ("前走1着", lambda h: tp(h)["rank"] == 1),
        ("前走1-3着", lambda h: tp(h)["rank"] in (1, 2, 3)),
        ("中4週以内", lambda h: h["t_interval"] is not None and h["t_interval"] <= 28),
        ("距離短縮>50m", lambda h: tp(h)["dist"] and h["race_dist"]
            and tp(h)["dist"] - h["race_dist"] > 50),
    ]:
        cells = []
        for lo, hi in pop_bands:
            band = [h for h in has_prev if lo <= h["pop"] <= hi]
            w = stats([h for h in band if pred(h)])
            wo = stats([h for h in band if not pred(h)])
            if w["n"] >= 10:
                cells.append(f"{lo}-{hi}人気:{w['top3']:3.0f}vs{wo['top3']:3.0f}%(N={w['n']},{w['top3']-wo['top3']:+.0f}pp)")
            else:
                cells.append(f"{lo}-{hi}人気:N={w['n']}不足")
        print(f"  {title:<22s} " + " | ".join(cells))
    print()

    # ════════ F. 全期間: レース条件ファクター ════════
    print("=" * 112)
    print("【F】候補: レース条件（全期間・3分割）")
    print("=" * 112)
    for title, pred in [
        ("少頭数(<=10頭)", lambda h: h["num_horses"] <= 10),
        ("多頭数(15頭+)", lambda h: h["num_horses"] >= 15),
        ("道悪(稍重/重/不良)", lambda h: h["track_cond"] in ("稍重", "重", "不良")),
        ("ダート", lambda h: h["surface"] == "ダ"),
        ("芝", lambda h: h["surface"] == "芝"),
        ("クラス=新馬/未勝利", lambda h: h["class_cat"] in ("新馬", "未勝利")),
        ("クラス=1勝", lambda h: h["class_cat"] == "1勝"),
        ("クラス=2勝以上", lambda h: h["class_cat"] in ("2勝", "3勝", "OP他")),
        ("内枠(1-3枠)", lambda h: h["gate"] is not None and h["gate"] <= 3),
        ("外枠(7-8枠)", lambda h: h["gate"] is not None and h["gate"] >= 7),
    ]:
        report_a(title, pred, min_n=20)

    # ════════ G. クリーン窓: スコア閾値 ════════
    print("=" * 112)
    print("【G】スコア閾値の妥当性（クリーン窓のみ）")
    print("=" * 112)
    for th in (5, 6, 7, 8, 9):
        report_c(f"スコア{th}pt以上", lambda h, t=th: h["score"] >= t, min_n=5)


if __name__ == "__main__":
    main()
