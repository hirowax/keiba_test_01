"""
Issue #3: ペーパートレード台帳 paper_trade.py
（docs/betting_workflow_issues.md「ペーパートレードの買い目メニュー」節・事前登録済み）

買い目メニュー M1〜M4（事前登録・変更禁止）:
- M1: ◎複勝 100円
- M2: ◎単勝 100円
- M3: ◎-○ワイド 100円（○=スコア2位重複馬 or 単指数1位最上位）
- M4: ◎軸3連複 紐(B∪D∪C)2頭流し C(k,2)点・1点100円

対象レース: ◎（スコア1位かつ8pt以上）が存在する全レース。
紐集合 B∪C∪D・◯の定義は `validate_trifecta_202608.py`（Issue #2・Fableレビュー合格済み）の
himo_set() / maru_num() をそのまま再利用し、定義の重複実装によるズレを防ぐ。

裁量の入る余地をなくすため、generate/settle は日付（または --all）のみを引数に取る。
判定基準（回収率≥110% かつ上位3本除外後≥80% かつ的中数≥15）はreportでは判定せず数値のみ出す
（90日経過後にFable/人間が判定する運用のため）。

使い方:
  python3 paper_trade.py generate YYYYMMDD   # 1日分の買い目生成
  python3 paper_trade.py generate --all      # pickup_scores.json+summaryが揃う全日付で生成
  python3 paper_trade.py settle YYYYMMDD     # 1日分を精算
  python3 paper_trade.py settle --all        # payouts_jra.jsonが揃う全日付を精算
  python3 paper_trade.py report              # メニュー別の累積成績表
"""
import argparse
import json
from datetime import datetime
from itertools import combinations
from pathlib import Path

from validate_trifecta_202608 import himo_set, maru_num, combo_key, MIN_SCORE, BET_UNIT

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
SUMMARY_DIR = BASE_DIR / "summary"

MENU_NAMES = {
    "M1": "◎複勝",
    "M2": "◎単勝",
    "M3": "◎-○ワイド",
    "M4": "◎軸3連複(紐2頭流し)",
}


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def build_summary_index(summary):
    idx = {}
    if not summary:
        return idx
    for venue, races in summary.items():
        for race in races:
            idx[race["label"]] = race
    return idx


# ── generate ────────────────────────────────────────────────

def build_menus(scored, summary_race):
    """1レース分のM1〜M4を組み立てて dict で返す"""
    axis_num = int(scored[0]["馬番"])
    axis_name = scored[0].get("馬名", "")

    menus = {}

    # M1: ◎複勝
    menus["M1"] = {
        "name": MENU_NAMES["M1"], "type": "fukusho",
        "bets": {str(axis_num): BET_UNIT}, "cost": BET_UNIT,
    }

    # M2: ◎単勝
    menus["M2"] = {
        "name": MENU_NAMES["M2"], "type": "tansho",
        "bets": {str(axis_num): BET_UNIT}, "cost": BET_UNIT,
    }

    # M3: ◎-○ワイド
    o_num = maru_num(scored, summary_race)
    if o_num is None or o_num == axis_num:
        menus["M3"] = {
            "name": MENU_NAMES["M3"], "type": "wide",
            "skip_reason": "no_o_candidate", "bets": {}, "cost": 0,
        }
    else:
        key = combo_key([axis_num, o_num])
        menus["M3"] = {
            "name": MENU_NAMES["M3"], "type": "wide", "o_num": o_num,
            "bets": {key: BET_UNIT}, "cost": BET_UNIT,
        }

    # M4: ◎軸3連複（紐2頭流し）
    _, himo = himo_set(scored, summary_race)
    if len(himo) < 2:
        menus["M4"] = {
            "name": MENU_NAMES["M4"], "type": "trio",
            "skip_reason": "k<2", "himo": sorted(himo), "bets": {}, "cost": 0,
        }
    else:
        bets = {
            combo_key([axis_num, h1, h2]): BET_UNIT
            for h1, h2 in combinations(sorted(himo), 2)
        }
        menus["M4"] = {
            "name": MENU_NAMES["M4"], "type": "trio",
            "himo": sorted(himo), "bets": bets, "cost": len(bets) * BET_UNIT,
        }

    return axis_num, axis_name, menus


def generate_for_date(date: str) -> bool:
    """1日分の買い目を生成し output/{date}/paper_bets.json に保存。成功したらTrue"""
    pickup = load_json(OUTPUT_DIR / date / "pickup_scores.json")
    if pickup is None:
        print(f"  {date}: pickup_scores.json なし → スキップ")
        return False
    summary = load_json(SUMMARY_DIR / f"{date}.json")
    sidx = build_summary_index(summary)

    races = {}
    skipped = {}
    for label, rdata in pickup.get("races", {}).items():
        scored = rdata.get("scored", [])
        if not scored or scored[0].get("score", 0) < MIN_SCORE:
            skipped[label] = "no_axis(score<8 or unscored)"
            continue
        summary_race = sidx.get(label)
        if summary_race is None:
            skipped[label] = "no_summary_race"
            continue
        axis_num, axis_name, menus = build_menus(scored, summary_race)
        races[label] = {
            "axis_num": axis_num,
            "axis_name": axis_name,
            "score": scored[0].get("score"),
            "menus": menus,
        }

    out = {
        "date": date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "settled": False,
        "races": races,
        "skipped_races": skipped,
    }
    save_json(OUTPUT_DIR / date / "paper_bets.json", out)
    print(f"  {date}: {len(races)}レース買い目生成 (スキップ{len(skipped)}) → paper_bets.json")
    return True


def cmd_generate(args):
    if args.all:
        dates = sorted(
            d.name for d in OUTPUT_DIR.iterdir()
            if d.is_dir() and d.name.isdigit() and (d / "pickup_scores.json").exists()
        )
        print(f"generate --all: 対象{len(dates)}日")
        for date in dates:
            generate_for_date(date)
    else:
        generate_for_date(args.date)


# ── settle ──────────────────────────────────────────────────

def settle_menu(menu: dict, race_payout: dict) -> dict:
    """1メニュー分を精算し return/hit を書き込んで返す"""
    if not menu.get("bets"):
        menu["return"] = 0
        menu["hit"] = False
        return menu

    mtype = menu["type"]
    ret = 0
    hit = False

    if mtype in ("fukusho", "tansho"):
        table = race_payout.get(mtype, {})
        for num in menu["bets"]:
            if num in table:
                ret += table[num]
                hit = True
    elif mtype == "wide":
        table = race_payout.get("wide", {})
        for key in menu["bets"]:
            if key in table:
                ret += table[key]
                hit = True
    elif mtype == "trio":
        table = race_payout.get("trio", {})
        bet_keys = set(menu["bets"].keys())
        for win_combo, yen in table.items():
            norm = combo_key([int(x) for x in win_combo.split("-")])
            if norm in bet_keys:
                ret += yen
                hit = True

    menu["return"] = ret
    menu["hit"] = hit
    return menu


def settle_for_date(date: str) -> bool:
    bets_path = OUTPUT_DIR / date / "paper_bets.json"
    bets = load_json(bets_path)
    if bets is None:
        print(f"  {date}: paper_bets.json なし（先に generate が必要）→ スキップ")
        return False
    payouts = load_json(OUTPUT_DIR / date / "payouts_jra.json")
    if payouts is None:
        print(f"  {date}: payouts_jra.json なし → スキップ")
        return False

    for label, race in bets["races"].items():
        race_payout = payouts.get(label, {})
        for menu in race["menus"].values():
            settle_menu(menu, race_payout)

    bets["settled"] = True
    bets["settled_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    save_json(bets_path, bets)
    print(f"  {date}: {len(bets['races'])}レース精算完了")
    return True


def cmd_settle(args):
    if args.all:
        dates = sorted(
            d.name for d in OUTPUT_DIR.iterdir()
            if d.is_dir() and d.name.isdigit()
            and (d / "paper_bets.json").exists()
            and (d / "payouts_jra.json").exists()
        )
        print(f"settle --all: 対象{len(dates)}日")
        for date in dates:
            settle_for_date(date)
    else:
        settle_for_date(args.date)


# ── report ──────────────────────────────────────────────────

def cmd_report(args):
    dates = sorted(
        d.name for d in OUTPUT_DIR.iterdir()
        if d.is_dir() and d.name.isdigit() and (d / "paper_bets.json").exists()
    )

    per_menu = {k: [] for k in MENU_NAMES}  # menu -> list of (date, label, cost, return, hit)
    n_settled_dates = 0
    for date in dates:
        bets = load_json(OUTPUT_DIR / date / "paper_bets.json")
        if not bets or not bets.get("settled"):
            continue
        n_settled_dates += 1
        for label, race in bets["races"].items():
            for mkey, menu in race["menus"].items():
                if menu.get("cost", 0) <= 0:
                    continue
                per_menu[mkey].append((date, label, menu["cost"], menu.get("return", 0), menu.get("hit", False)))

    print("=" * 60)
    print("ペーパートレード累積成績（M1〜M4）")
    print("=" * 60)
    print(f"精算済み日数: {n_settled_dates} / 買い目生成済み日数: {len(dates)}")
    if not n_settled_dates:
        print("精算済みデータがありません（先に settle を実行してください）")
        return

    for mkey, name in MENU_NAMES.items():
        recs = per_menu[mkey]
        if not recs:
            print(f"\n### {mkey} {name}: 対象なし")
            continue
        n = len(recs)
        cost = sum(r[2] for r in recs)
        ret = sum(r[3] for r in recs)
        n_hit = sum(1 for r in recs if r[4])
        roi = ret / cost * 100 if cost else 0
        hit_rate = n_hit / n * 100 if n else 0

        top3 = sorted((r[3] for r in recs), reverse=True)[:3]
        ret_ex = ret - sum(top3)
        roi_ex = ret_ex / cost * 100 if cost else 0

        d_start, d_end = min(r[0] for r in recs), max(r[0] for r in recs)

        print(f"\n### {mkey} {name}")
        print(f"  期間: {d_start} 〜 {d_end}")
        print(f"  対象レース: {n}  的中: {n_hit}  的中率: {hit_rate:.1f}%")
        print(f"  総投資: {cost:,}円  総払戻: {ret:,}円  回収率: {roi:.1f}%")
        print(f"  上位3的中除外後 回収率: {roi_ex:.1f}%")


def main():
    parser = argparse.ArgumentParser(description="ペーパートレード台帳")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_gen = sub.add_parser("generate", help="買い目生成")
    g = p_gen.add_mutually_exclusive_group(required=True)
    g.add_argument("date", nargs="?", help="YYYYMMDD")
    g.add_argument("--all", action="store_true")
    p_gen.set_defaults(func=cmd_generate)

    p_settle = sub.add_parser("settle", help="精算")
    g2 = p_settle.add_mutually_exclusive_group(required=True)
    g2.add_argument("date", nargs="?", help="YYYYMMDD")
    g2.add_argument("--all", action="store_true")
    p_settle.set_defaults(func=cmd_settle)

    p_report = sub.add_parser("report", help="累積成績表")
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    if args.cmd in ("generate", "settle") and not args.all and not args.date:
        parser.error("date か --all のどちらかを指定してください")
    args.func(args)


if __name__ == "__main__":
    main()
