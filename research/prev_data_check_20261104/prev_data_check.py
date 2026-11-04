"""残りのタスク1: 前走データ（馬ページから取った値）の数え直し。
計画: docs/prev_data_check_plan_202611.md（2026-11-03 記録）。netkeiba には新しくアクセスしない。

usage: python3 research/prev_data_check_20261104/prev_data_check.py <入力の根> <出力フォルダ>
  入力の根: output/ と jra_calendar_2026.json を含むフォルダ（取り直し中は output/ の写しを渡す）

数え方の要点（計画の節番号）:
- 単位は（対象日・レース・馬ID）の延べ数（4-1）。
- 対象日の馬の一覧は、その日の結果（取り直しのレースがあれば race_results_all.json、無ければ race_results.json）から作る。
  馬番・馬名・馬IDは結果のもの。取消・除外の馬は対象にしない。
- 出走（4-2）: 着順のある馬、競走中止・失格。取消・除外は出走でない。それ以外の状態の文字は「未確認」。
- 古さ（4-3）: 前走日 < 結果の日 < 対象日 の出走が結果にあれば「古いことを確かめられた」。
- 採点への影響（4-4）: 前走の加点は race_pickup.score_horses（現行のコード）を呼んで計算する。
  正しい前走の指数は保存したデータからは確かめられないので、厳密な計算では「算定不能」にする。
  参考として、指数の表（前日に取った speed.html）の「前走」の列の値を使った計算を別に出す。
"""
import collections
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "research" / "data_check_20261103"))
from xlsx_blocks import load_day  # noqa: E402
import race_pickup  # noqa: E402

ROOT = Path(sys.argv[1]).resolve()
OUT = ROOT / "output"
RES_DIR = Path(sys.argv[2]).resolve()
RES_DIR.mkdir(parents=True, exist_ok=True)

START, END = "20260321", "20261101"
SPLIT = "20260613"           # この日以降は採点後の写しである見込みが高い期間（計画3章）
FIX_DATE = "20261031"        # 馬番の取り方を直した後の最初の開催日（collector_version あり）
SP = re.compile(r"[\s　]+")
NUM = re.compile(r"-?\d+")

RUN_STATUSES = {"中止", "失格"}
NOT_RUN_STATUSES = {"取消", "除外"}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def period(d):
    return "6/13以降" if d >= SPLIT else "6/12以前"


def nd(s):
    return (s or "").replace("/", "")


# ── 入力の一覧と sha256（実行の前後で比べる）──
def manifest():
    files = {}
    for pat in ["*.xlsx", "*/race_results.json", "*/race_results_all.json", "*/race_nonfinishers.json",
                "*/prev_data.json", "*/pickup_scores.json", "*/race_conditions.json", "threshold_config.json"]:
        for f in sorted(OUT.glob(pat)):
            if pat == "*.xlsx" and not re.fullmatch(r"\d{8}", f.stem):
                continue
            files[str(f.relative_to(ROOT))] = sha(f)
    cal = ROOT / "jra_calendar_2026.json"
    files[str(cal.relative_to(ROOT))] = sha(cal)
    return files


before = manifest()

# ── 結果を馬IDごとに集める（全日）──
result_version = {}      # 日 → "取り直し(完了)" / "取り直し(途中)+旧版" / "旧版" / なし
runs = collections.defaultdict(list)   # 馬ID → [{date, label, race_id, status, rank, pop, dist, surface, version}]
day_races = {}           # 日 → {label: {"race_id", "horses":[{num,name,hid,status}], "version", "dist", "surface"}}
rescrape_state = {"完了": 0, "途中": 0}

for d in sorted({p.parent.name for p in OUT.glob("*/race_results*.json")}):
    if not re.fullmatch(r"\d{8}", d):
        continue
    allp, oldp, nfp = OUT / d / "race_results_all.json", OUT / d / "race_results.json", OUT / d / "race_nonfinishers.json"
    ra = json.load(open(allp)) if allp.exists() else None
    ro = json.load(open(oldp)) if oldp.exists() else {}
    nf = json.load(open(nfp)) if nfp.exists() else {}
    if ra is not None:
        rescrape_state["完了" if ra.get("complete") else "途中"] += 1
    races = {}
    labels = set(ro) | set((ra or {}).get("races", {}))
    for lab in labels:
        if ra is not None and lab in ra.get("races", {}):
            r = ra["races"][lab]
            cond = r.get("condition") or {}
            hs = [dict(num=str(h["num"]), name=h["name"], hid=h.get("horse_id"), status="着順あり",
                       rank=h.get("rank"), pop=h.get("pop")) for h in r.get("horses", [])]
            for o in r.get("others", []):
                hs.append(dict(num=str(o.get("num")), name=o.get("name"), hid=o.get("horse_id"),
                               status=o.get("status") or "", rank=o.get("rank_text"), pop=o.get("pop")))
            ver = "取り直し"
            rid = r.get("race_id")
        else:
            cond = {}
            hs = [dict(num=str(h["num"]), name=h["name"], hid=h.get("horse_id"), status="着順あり",
                       rank=h.get("rank"), pop=h.get("pop")) for h in ro.get(lab, [])]
            for o in (nf.get(lab) or []) if isinstance(nf, dict) else []:
                hs.append(dict(num=str(o.get("num")), name=o.get("name"), hid=o.get("horse_id"),
                               status=o.get("status") or "", rank=o.get("rank_text"), pop=o.get("pop")))
            ver = "旧版" if not nf else "旧版+着順なしの記録"
            rid = None
        races[lab] = dict(race_id=rid, horses=hs, version=ver,
                          dist=cond.get("distance"), surface=cond.get("surface"))
    # 旧版の日は race_conditions.json から距離を補う
    rc = OUT / d / "race_conditions.json"
    if rc.exists():
        rcj = json.load(open(rc))
        for lab, r in races.items():
            c = rcj.get(lab) or {}
            if r["dist"] is None:
                r["dist"], r["surface"] = c.get("distance"), c.get("surface")
            if r["race_id"] is None:
                r["race_id"] = c.get("race_id")
    day_races[d] = races
    vers = {r["version"] for r in races.values()}
    result_version[d] = "取り直し" if vers == {"取り直し"} else ("旧版" if "取り直し" not in vers else "取り直しと旧版")
    for lab, r in races.items():
        for h in r["horses"]:
            if h["hid"]:
                runs[h["hid"]].append(dict(date=d, label=lab, race_id=r["race_id"], status=h["status"],
                                           rank=h["rank"], pop=h["pop"], dist=r["dist"],
                                           surface=r["surface"], version=r["version"]))

cal = set(json.load(open(ROOT / "jra_calendar_2026.json"))["dates"])


def run_kind(status):
    if status == "着順あり" or status in RUN_STATUSES:
        return "出走"
    if status in NOT_RUN_STATUSES:
        return "非出走"
    return "未確認"


def staleness(hid, prev_date, d):
    """4-3 の3分類と、間の日の結果の状況"""
    between = [r for r in runs.get(hid, []) if prev_date < r["date"] < d]
    ran = [r for r in between if run_kind(r["status"]) == "出走"]
    unk = [r for r in between if run_kind(r["status"]) == "未確認"]
    gap_days = [x for x in cal if prev_date < x < d] if prev_date >= "20260101" else None
    gap = None
    if gap_days is not None:
        gap = collections.Counter(result_version.get(x, "結果なし") for x in gap_days)
    if ran:
        cls = "古いことを確かめられた"
    elif unk:
        cls = "判定できない"
    else:
        cls = "保存した結果の範囲では古さを見つけなかった"
    latest = max(ran, key=lambda r: r["date"]) if ran else None
    return cls, latest, dict(gap) if gap is not None else "2025年のカレンダーが無いので数えない"


# ── 写しを変更したコミット（計画3章）──
def snapshot_commits(d):
    out = subprocess.run(["git", "-C", str(REPO), "log", "--format=%h\t%ad\t%s", "--date=format:%Y%m%d",
                          "--", f"output/{d}/prev_data.json"], capture_output=True, text=True).stdout
    return [line.split("\t") for line in out.strip().splitlines() if line]


def idx_value(s):
    """馬ページの値（写し）: 数字ならその値、それ以外は文字のまま分類"""
    s = (s or "").strip()
    if NUM.fullmatch(s):
        return int(s), "数字"
    if s == "**":
        return None, "「**」"
    if s == "":
        return None, "空"
    return None, "その他の文字"


def table_value(s):
    """指数の表の値: 末尾の「*」を除いた数字。200以上は隠し値なので使わない"""
    s = (s or "").strip()
    m = NUM.fullmatch(s.rstrip("*"))
    if not m:
        return None, "数字でない"
    v = int(m.group())
    if v >= 200:
        return None, "200以上"
    return v, "数字(「*」付き)" if s.endswith("*") else "数字"


def prev_points(prev, race_max, d, race_dist, num="1", hid="H"):
    """現行の race_pickup.score_horses を呼び、前走に由来する加点だけを返す。
    前走以外の加点が入らないように、出馬表・データ分析の値は空にする。"""
    shutuba = dict(map_issues=None, scratched_nums={}, position_nums=set(), top3_hits={},
                   horse_id_map={num: hid}, pop_map={}, odds_map={})
    data_top = dict(pickup_nums=set(), analysis_hits={})
    db = {hid: prev} if prev is not None else {}
    r = race_pickup.score_horses([{"馬番": num, "馬名": ""}], shutuba, data_top, prev_db=db,
                                 race_max_prev_idx=race_max, race_date=d, horse_style_db=None,
                                 race_dist=race_dist)
    return r[0]["score"], [b["label"] for b in r[0]["breakdown"]]


PREV_LABELS = ("前走指数", "中4週", "前走好走", "同距離前走")


def recorded_prev_points(h):
    return sum(b["pts"] for b in h.get("breakdown", []) if b["label"].startswith(PREV_LABELS))


def stars(score):
    if score >= race_pickup.THRESHOLD_HIGH:
        return "★★★"
    if score >= race_pickup.THRESHOLD_MID:
        return "★★"
    if score >= race_pickup.THRESHOLD_LOW:
        return "★"
    return "－"


def usable_max(vals):
    """run_pickup_all.py と同じ: 前走日 < 対象日 の値のうち数字にできるものの最大"""
    v = [x for x in vals if x is not None]
    return max(v) if v else None


C = collections.defaultdict(collections.Counter)
detail_stale, detail_idx, detail_cand, detail_top1, commit_notes = [], [], [], [], []
fire = json.load(open(OUT / "threshold_config.json")).get("ev_threshold") if (OUT / "threshold_config.json").exists() else None

days = sorted(p.parent.name for p in OUT.glob("*/prev_data.json") if START <= p.parent.name <= END)
for d in days:
    P = period(d)
    commits = snapshot_commits(d)
    from datetime import timedelta
    day_before = (datetime.strptime(d, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    first = commits[-1] if commits else None
    path_ok = (len(commits) == 1 and first[1] <= d and first[2].strip() == f"data: {d}")
    if not path_ok:
        C["保存経路"]["保存経路の追加確認が必要な日（下の条件に当たらない日）"] += 1
        commit_notes.append(dict(日=d, コミット=commits))
    else:
        C["保存経路"]["件名が「data: 対象日」の1コミットだけで書かれ、コミット日が対象日以前の日"] += 1
    prev = json.load(open(OUT / d / "prev_data.json"))
    if d not in day_races:
        C["除外"][f"{P}: 結果ファイルが無い日"] += 1
        continue
    races = day_races[d]
    xl = OUT / f"{d}.xlsx"
    tables = load_day(xl) if xl.exists() else {}
    tab_by_label = {lab: m.get("average") for (sh, lab), m in tables.items()}
    pk = json.load(open(OUT / d / "pickup_scores.json")) if (OUT / d / "pickup_scores.json").exists() else {"races": {}}
    pk_races = pk.get("races", {})

    for lab, r in races.items():
        # 指数の表の「前走」の列（近走平均の表。見出し行の5列目が「前走」であることを確かめる）
        tab = {}
        df = tab_by_label.get(lab)
        if df is not None and len(df):
            hdr = [str(v).strip() for v in df.iloc[0][:5]]
            if hdr == ["5走前", "4走前", "3走前", "2走前", "前走"] and len(df.columns) == 11:
                for _, x in df.iloc[1:].iterrows():
                    tab[str(x["馬番"]).strip()] = (SP.sub("", str(x["馬名"])), str(x[df.columns[10]]))
            else:
                C["除外"]["指数の表の見出しの形が違うレース"] += 1
        elif df is None:
            C["除外"]["指数の表が無いレース"] += 1

        recs = {}
        for h in r["horses"]:
            k = run_kind(h["status"])
            if k == "非出走":
                C["除外"]["取消・除外の馬"] += 1
                continue
            if k == "未確認":
                C["除外"]["状態が未確認の馬（対象日）"] += 1
            if not h["hid"]:
                C["除外"]["馬IDが無い"] += 1
                continue
            p = prev.get(h["hid"])
            rec = dict(num=h["num"], name=h["name"], hid=h["hid"], p=p)
            if p is None:
                C[f"写しの有無 {P}"]["写しに無い"] += 1
                rec["state"] = "写しに無い"
            elif not p.get("prev_date"):
                C[f"写しの有無 {P}"]["前走日が無い"] += 1
                rec["state"] = "前走日が無い"
            elif nd(p["prev_date"]) >= d:
                C[f"写しの有無 {P}"]["前走日が対象日以降（採点では使わない）"] += 1
                rec["state"] = "前走日が対象日以降"
            else:
                C[f"写しの有無 {P}"]["前走日が対象日より前"] += 1
                cls, latest, gap = staleness(h["hid"], nd(p["prev_date"]), d)
                rec.update(state=cls, latest=latest, gap=gap)
                C[f"古さ {P}"][cls] += 1
                if p.get("scraped_at", "") > d:
                    C[f"取得日 {P}"]["取得日が対象日より後"] += 1
                v, kind = idx_value(p.get("prev_idx"))
                rec["idx"], rec["idx_kind"] = v, kind
                if cls == "古いことを確かめられた":
                    detail_stale.append(dict(対象日=d, レース=lab, レースID=r["race_id"], 馬ID=h["hid"], 馬名=h["name"],
                                             写しの前走日=nd(p["prev_date"]), 取得日=p.get("scraped_at"),
                                             間の出走=dict(日=latest["date"], レース=latest["label"],
                                                       レースID=latest["race_id"], 状態=latest["status"]),
                                             間の日の結果=gap))
            # 4-7 指数の表との比較
            t = tab.get(h["num"])
            if t is not None and p is not None:
                if t[0] != SP.sub("", h["name"]):
                    C["4-7 表との比較"]["表の馬名が結果と違う（比べない）"] += 1
                else:
                    tv, tkind = table_value(t[1])
                    rec["table"] = tv
                    sv, skind = idx_value(p.get("prev_idx"))
                    same = rec.get("state") == "保存した結果の範囲では古さを見つけなかった"
                    prev_race = None
                    if rec.get("state") not in (None, "写しに無い", "前走日が無い", "前走日が対象日以降"):
                        pr = [x for x in runs.get(h["hid"], []) if x["date"] == nd(p["prev_date"])]
                        prev_race = pr[0] if len(pr) == 1 else None
                    group = ("古さを見つけなかった" if same else
                             "古いことを確かめられた" if rec.get("state") == "古いことを確かめられた" else "その他")
                    if tv is None:
                        C["4-7 表との比較"][f"表の値: {tkind}"] += 1
                    if sv is None:
                        C["4-7 表との比較"][f"写しの値: {skind}"] += 1
                    if tv is not None and sv is not None:
                        ok = tv == sv
                        C[f"4-7 {P} {group}"]["一致" if ok else "不一致"] += 1
                        C["4-7 日ごと"][f"{d} {group} {'一致' if ok else '不一致'}"] += 1
                        if not ok:
                            mine = [x for x in runs.get(h["hid"], []) if x["date"] < d and run_kind(x["status"]) == "出走"]
                            detail_idx.append(dict(
                                対象日=d, レース=lab, レースID=r["race_id"], 馬ID=h["hid"], 馬名=h["name"],
                                写しの前走日=nd(p["prev_date"]),
                                写しの前走レース=(dict(レース=prev_race["label"], レースID=prev_race["race_id"]) if prev_race else "結果に無い"),
                                自前の結果での直近の出走日=(max(x["date"] for x in mine) if mine else "不明"),
                                写しの元の文字=p.get("prev_idx"), 写しの値=sv, 表の元の文字=t[1], 表の値=tv,
                                取得日=p.get("scraped_at"), 古さ=rec.get("state")))
            recs[h["num"]] = rec

        # 4-6 「**」
        pk_r = pk_races.get(lab) if isinstance(pk_races, dict) else None
        cand_nums = {str(x["馬番"]) for x in (pk_r or {}).get("scored", [])} if isinstance(pk_r, dict) else set()
        for num, rec in recs.items():
            if rec.get("idx_kind") == "「**」":
                who = "候補" if num in cand_nums else "比較相手"
                C[f"4-6 「**」 {P}"][who] += 1
                C["4-6 「**」 日ごと"][f"{d} {who}"] += 1

        if not isinstance(pk_r, dict) or not pk_r.get("scored"):
            continue
        race_dist = pk_r.get("race_dist")
        # 4-5: 比較相手の値の状態
        def okv(rec):
            return rec.get("idx") is not None and rec.get("state") == "保存した結果の範囲では古さを見つけなかった"
        problems = collections.Counter()
        for num, rec in recs.items():
            if okv(rec):
                continue
            problems[rec.get("state") if rec.get("state") != "保存した結果の範囲では古さを見つけなかった"
                     else f"値が{rec.get('idx_kind')}"] += 1
        all_ok = not problems
        snap_max = usable_max([rec.get("idx") for rec in recs.values()
                               if rec.get("state") not in (None, "写しに無い", "前走日が無い", "前走日が対象日以降")])
        verified_max = usable_max([rec.get("idx") for rec in recs.values() if okv(rec)])
        table_max = usable_max([rec.get("table") for rec in recs.values()])

        hid_map = pk_r.get("horse_id_map") or {}
        for c in pk_r["scored"]:
            num = str(c["馬番"])
            C[f"候補 {P}"]["候補"] += 1
            rec = recs.get(num)
            if rec is None or SP.sub("", rec["name"]) != SP.sub("", c["馬名"]):
                C[f"候補 {P}"]["結果と馬番・馬名で照合できない候補"] += 1
                continue
            used_hid = hid_map.get(num) or None
            if used_hid is None:
                same_hid = "採点時の馬IDが無い候補"
            elif used_hid == rec["hid"]:
                same_hid = "採点時の馬IDが正しい候補"
            else:
                same_hid = "採点時の馬IDが別の馬の候補"
            C[f"候補 {P}"][same_hid] += 1
            # 採点時の再現（採点時に使った馬IDの写しの値で、現行のコードを呼ぶ）
            used_prev = prev.get(used_hid) if used_hid else None
            rp, _ = prev_points(used_prev, snap_max, d, race_dist)
            C[f"候補 {P}"]["写しから計算した前走の加点の合計が記録と一致" if rp == recorded_prev_points(c) else "写しから計算した前走の加点の合計が記録と違う"] += 1
            C[f"候補 {P} {same_hid}"]["写しから計算した前走の加点の合計が記録と一致" if rp == recorded_prev_points(c) else "写しから計算した前走の加点の合計が記録と違う"] += 1
            # 正しい馬IDの写しの値での加点
            p = rec["p"]
            base_pts, _ = prev_points(p, snap_max, d, race_dist)
            other_pts = c["score"] - recorded_prev_points(c)
            st = rec.get("state", "写しに無い")
            C[f"4-4 {P}"][f"候補の写しの古さ: {st}"] += 1
            C[f"4-4 {P} {same_hid}"][f"写しの古さ: {st}"] += 1
            if p is not None and p.get("scraped_at", "") > d:
                C[f"4-4 {P}"]["候補の写しの取得日が対象日より後"] += 1
            # 4-5
            if snap_max is not None and rec.get("idx") == snap_max and snap_max > 0:
                C[f"4-5 {P}"]["写しの値でレース内1位"] += 1
            if problems:
                C[f"4-5 {P}"]["比較相手に古い値・「**」・値なし等があるレースの候補"] += 1
            if all_ok and verified_max is not None and rec.get("idx") == verified_max and verified_max > 0:
                C[f"4-5 {P}"]["全頭に数字があり古さを見つけなかったうえでの1位（計画の「全頭の値を確かめたうえでの1位」）"] += 1
            if okv(rec) and verified_max is not None and rec.get("idx") == verified_max and verified_max > 0:
                C[f"4-5 {P}"]["数字があり古さを見つけなかった馬の中だけの1位（計画の「確かめられた馬の中だけの1位」）"] += 1
            if problems and snap_max is not None and rec.get("idx") == snap_max:
                detail_top1.append(dict(対象日=d, レース=lab, 馬番=num, 馬名=c["馬名"], 写しの値=rec.get("idx"),
                                        写しの最大=snap_max, 数字があり古さを見つけなかった馬の最大=verified_max,
                                        比較相手の問題=dict(problems)))
            # 4-4 の2・3: 正しい前走での計算
            if st == "古いことを確かめられた":
                lt = rec["latest"]
                C[f"4-4 {P}"]["合計点: 算定不能（正しい前走の指数を確かめられない）"] += 1
                C[f"4-4 {P} {same_hid}"]["合計点: 算定不能（正しい前走の指数を確かめられない）"] += 1
                # 参考: 指数の表の「前走」の値を正しい前走の指数とみなした計算
                tv = rec.get("table")
                missing = [k for k, v in (("前走の距離", lt["dist"]), ("前走の人気", lt["pop"] if lt["status"] == "着順あり" else "対象外"),
                                          ("前走の着順", lt["rank"])) if v in (None, "")]
                if race_dist is None:
                    missing.append("今回の距離")
                if tv is None or table_max is None:
                    missing.append("指数の表の値")
                ref = None
                if missing:
                    C[f"4-4 参考 {P} {same_hid}"]["参考の計算をしない（値が欠けている）"] += 1
                    for k in missing:
                        C[f"4-4 参考 {P} {same_hid}"][f"欠けている値: {k}"] += 1
                else:
                    np_ = dict(prev_date=f"{lt['date'][:4]}/{lt['date'][4:6]}/{lt['date'][6:]}",
                               prev_pop=str(lt["pop"] if lt["pop"] is not None else ""), prev_rank=str(lt["rank"]),
                               prev_dist=f"{lt['surface'] or ''}{lt['dist']}", prev_idx=str(tv))
                    new_pts, new_lbl = prev_points(np_, table_max, d, race_dist)
                    rec_total = c["score"]                      # 記録された合計点
                    hyp_old = other_pts + base_pts              # 正しい馬の古い写しで計算した仮想の合計点
                    new_total = other_pts + new_pts             # 正しい前走（指数は表の値）で計算した参考の合計点
                    ref = dict(記録された合計=rec_total, 正しい馬の古い写しでの仮想の合計=hyp_old, 参考の合計=new_total,
                               参考の加点=new_lbl)
                    for name, old_total in (("記録点との比較", rec_total), ("馬IDを補正した仮想点同士の比較", hyp_old)):
                        k = f"4-4 参考 {P} {same_hid} {name}"
                        C[k]["合計点が変わる" if new_total != old_total else "合計点が変わらない"] += 1
                        if stars(new_total) != stars(old_total):
                            C[k]["★の段階が変わる"] += 1
                        if fire is not None and (new_total >= fire) != (old_total >= fire):
                            C[k][f"🔥（現在の閾値{fire}点以上）が変わる"] += 1
                detail_cand.append(dict(対象日=d, レース=lab, 馬番=num, 馬名=c["馬名"], 馬ID=rec["hid"], 採点時の馬ID=used_hid,
                                        写しの前走日=nd(p["prev_date"]), 取得日=p.get("scraped_at"),
                                        正しい前走の候補=lt, 写しでの前走の加点=base_pts, 参考=ref,
                                        参考の計算をしない理由=missing or None))

after = manifest()
changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
if changed:
    print("実行中に入力が変わったので結果を書かない:", changed[:10])
    sys.exit(1)

summary = {k: dict(v) for k, v in sorted(C.items()) if "日ごと" not in k}
by_day = {k: dict(v) for k, v in sorted(C.items()) if "日ごと" in k}
meta = dict(実行日時=datetime.now().isoformat(timespec="seconds"), 入力の根=str(ROOT), 対象日数=len(days),
            対象期間=[START, END], 取り直しの状況=rescrape_state, 火の印の点=fire,
            注記=["4-4 の候補の写しの古さは、正しい馬ID（結果の馬番・馬名で照合）の写しで判定した。2026-10-30 以前の採点は別の馬の馬IDを使っていることがある（採点時の馬IDの一致の件数を参照）。",
                "4-4 参考は、指数の表（前日に取った speed.html）の「前走」の列を正しい前走の指数とみなした計算で、列の意味は確かめていない。",
                "前走の加点の再現は、採点時の馬IDの写しの値と、結果の出走馬（採点時の出馬表ではない）の写しから計算したレース内の最大値で、現行の score_horses を呼び、前走の加点の合計だけを記録と比べたもの。入力の値そのものの一致は確かめていない。",
                "4-4 参考は2通りに比べる。記録点との比較は、記録された合計点と、記録の前走以外の加点＋正しい前走の加点。馬IDを補正した仮想点同士の比較は、正しい馬の古い写しでの加点と正しい前走の加点（どちらも前走以外の加点は記録の値）。",
                "参考の計算は、前走の距離・人気（着順ありの馬）・着順、今回の距離、表の値のどれかが欠けていればしない。",
                "🔥は、どの日も現在の threshold_config.json の閾値で判定した（当時の閾値ではない）。",
                "「古さを見つけなかった」は、保存した結果で間の出走が見つからなかったという意味で、値を確かめたという意味ではない。"],
            コードの版=subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
            race_pickup_sha256=sha(REPO / "race_pickup.py"))
json.dump(dict(meta=meta, 集計=summary, 日ごと=by_day, 保存経路の確認が必要な日=commit_notes,
               明細_古いことを確かめられた=detail_stale, 明細_候補の古い前走=detail_cand,
               明細_レース内1位=detail_top1, 明細_表と写しの不一致=detail_idx),
          open(RES_DIR / "prev_data_check.json", "w"), ensure_ascii=False, indent=1)
json.dump(after, open(RES_DIR / "prev_data_check_manifest.json", "w"), ensure_ascii=False, indent=0)
with open(RES_DIR / "prev_data_check.txt", "w") as f:
    f.write(json.dumps(meta, ensure_ascii=False, indent=1) + "\n")
    for k, v in summary.items():
        f.write(f"\n## {k}\n")
        for kk, vv in sorted(v.items()):
            f.write(f"  {kk}: {vv}\n")
print(open(RES_DIR / "prev_data_check.txt").read())
