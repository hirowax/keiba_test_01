#!/usr/bin/env python3
"""
対象日ごとの脚質の全体を作り、その日の記録として固定する（2026-11-12〜）。
計画: docs/horse_style_plan_202611.md（承認）、設計: docs/horse_style_design_202611.md（承認）。

- 入力: 基準の commit（作り始めの HEAD）にある output/{日付}/race_results_all.json のうち、日付が対象日の2日前以前のもの
  （日曜の対象日に土曜は入らない。祝日の月曜なら土曜まで入り、日曜は入らない）と、JRA 公式の結果の一覧
  output/jra_cname_cache.json。作業ファイルは読まず、commit の blob から読む。作業ファイルと blob が違えば作成失敗。
- その日を使う条件: complete で、保存したレースの集合がレース一覧と同じで、予定を確定できる（schedule_check.day_schedule）。
- レースを使う条件（race_verdict）: 発走した全馬の最初の通過順が、同じ地点の位置として確かめられること。使わないレースは理由別に数える。
- 記録: output/{対象日}/horse_style/{実行ID}.json（git に入れる）。その日の最初に作成・保存に成功した記録で確定する。
  作成失敗も記録する。記録は使う前に検査し、壊れた記録・知らない状態があれば新しく作らずに止める。

usage:
  python3 build_horse_style.py make 20261114 [--dry-run]   # その日の記録を作る（あれば検査して使う）。--dry-run は保存しない
  python3 build_horse_style.py legacy                      # 旧方式（採点したレースだけ）の数を出すだけ（何も書かない）

output/horse_style.json（旧方式。2026-06-14 に作ったもの）は書き換えない。研究用のスクリプトが読む古いファイルとして残す。
"""
import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from schedule_check import day_schedule, JRA_CACHE_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
LEGACY_STYLE_PATH = OUTPUT_DIR / "horse_style.json"
STYLE_DIR_NAME = "horse_style"
RESULTS_ALL_NAME = "race_results_all.json"

STYLE_BUILD_VERSION = "全レース_発走頭数_全発走馬の最初の通過順_前日より前_基準commit_20261112"
STYLE_BUILD_VERSIONS = (STYLE_BUILD_VERSION,)   # 品質の判定が知っている版

STATUS_OK = "作成成功"
STATUS_FAILED = "作成失敗"
STATUSES = (STATUS_OK, STATUS_FAILED)

# 出走馬の状態（レースごとの結び付け）
HS_OK = "脚質あり"
HS_ONE = "1走（加点の対象外）"
HS_NONE = "脚質なし（正常な履歴不足）"
HS_NO_ID = "馬IDが無い"

# 使わないレースの理由（計画 2.2・設計 3.1。判定の順番もこの順）
R_UNKNOWN = "4 状態不明"
R_FEW = "5 8頭未満"
R_NOT_YET = "1 未取得"
R_NONFINISH = "2 中止・失格の最初の通過順を確かめられない"
R_SHAPE = "3 最初の通過順として使えると確かめられない"
R_BAD = "4 不整合"
MIN_STARTERS = 8
STARTED_OTHERS = ("中止", "失格")
NOT_STARTED = ("取消", "除外")
# 中止・失格の馬の先頭の値が、完走した馬の先頭の値と同じ地点かを確かめる方法は、まだ無い（設計 3.1・5章の4）
NONFINISHER_FIRST_CORNER_VERIFIED = False

# 脚質分類の閾値（1コーナー相対位置: 0=先頭, 1=最後尾）。変えない（計画 2.2）
THRESHOLDS = {
    '逃げ':   (0.00, 0.15),
    '先行':   (0.15, 0.35),
    '差し':   (0.35, 0.60),
    '追い込み': (0.60, 1.01),
}


class StyleRecordError(Exception):
    """その日の記録が壊れている・検査に通らない・保存できない（新しく作らずに止める）"""


def classify(avg_ratio: float) -> str:
    for style, (lo, hi) in THRESHOLDS.items():
        if lo <= avg_ratio < hi:
            return style
    return '追い込み'


def deadline(target: str) -> datetime:
    """入力の期限: 対象日 00:00（日本時間）。この時刻以上に作り終えたものは使わない"""
    from prev_inputs import JST
    return datetime.strptime(target, "%Y%m%d").replace(tzinfo=JST)


def now_jst() -> datetime:
    from prev_inputs import now_jst as _now
    return _now()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ── git ──
def git(repo: Path, *args, text=True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=text, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or '').strip()}")
    return r.stdout


def last_input_date(target: str) -> str:
    """使う結果の最後の日付（対象日の2日前）"""
    return (datetime.strptime(target, "%Y%m%d") - timedelta(days=2)).strftime("%Y%m%d")


def select_inputs(target: str, commit: str, repo: Path) -> list:
    """基準の commit にある、日付が対象日の2日前以前の race_results_all.json のパス（日付順）"""
    out = git(repo, "ls-tree", "-r", "--name-only", commit, "--", "output")
    last = last_input_date(target)
    paths = []
    for p in out.splitlines():
        m = re.fullmatch(r"output/(\d{8})/" + re.escape(RESULTS_ALL_NAME), p)
        if m and m.group(1) <= last:
            paths.append(p)
    return sorted(paths)


def blob_id(commit: str, path: str, repo: Path) -> str:
    return git(repo, "rev-parse", f"{commit}:{path}").strip()


def read_blob(commit: str, path: str, repo: Path) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{path}"], capture_output=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git show {commit}:{path}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout


def fix_inputs(target: str, commit: str, repo: Path) -> tuple:
    """入力を基準の commit から読み、作業ファイルと同じか確かめる。戻り値 (入力の一覧, {path: bytes})。
    作業ファイルが無い・違えば RuntimeError（作成失敗「commit されていない変更がある」）"""
    paths = select_inputs(target, commit, repo) + [JRA_CACHE_PATH]
    listing, data = [], {}
    for p in paths:
        b = read_blob(commit, p, repo)
        work = Path(repo) / p
        if not work.exists():
            raise RuntimeError(f"入力の作業ファイルが無い: {p}")
        if sha256_bytes(work.read_bytes()) != sha256_bytes(b):
            raise RuntimeError(f"入力に commit されていない変更がある: {p}")
        listing.append({"path": p, "sha256": sha256_bytes(b), "blob": blob_id(commit, p, repo)})
        data[p] = b
    return listing, data


# ── 規則（計画 2.2・設計 3.1）──
def _venue(label: str) -> str:
    m = re.match(r"(\D+)\d+R$", label)
    return m.group(1) if m else ""


def race_verdict(label: str, race: dict) -> tuple:
    """1レース → (使うか, 理由, [(馬ID, 相対位置)])。理由は最初に当たった1つ"""
    horses = race.get("horses") or []
    others = race.get("others") or []
    cond = race.get("condition") or {}
    if any(o.get("status") not in STARTED_OTHERS + NOT_STARTED for o in others):
        return False, R_UNKNOWN, []
    started_others = [o for o in others if o.get("status") in STARTED_OTHERS]
    n = len(horses) + len(started_others)
    if n < MIN_STARTERS:
        return False, R_FEW, []
    corners = [h.get("corners") or [] for h in horses]
    if all(not c for c in corners):
        return False, R_NOT_YET, []
    if started_others and not NONFINISHER_FIRST_CORNER_VERIFIED:
        return False, R_NONFINISH, []
    if _venue(label) == "新潟" and cond.get("surface") == "芝" and cond.get("distance") == 1000:
        return False, R_SHAPE, []
    lens = {len(c) for c in corners if c}
    if len(lens) > 1:
        return False, R_SHAPE, []
    if any(not c for c in corners):
        return False, R_BAD, []
    ids = [h.get("horse_id") for h in horses] + [o.get("horse_id") for o in started_others]
    if not all(ids) or len(set(ids)) != len(ids):
        return False, R_BAD, []
    if any(not isinstance(c[0], int) or not (1 <= c[0] <= n) for c in corners):
        return False, R_BAD, []
    return True, None, [(h["horse_id"], (c[0] - 1) / (n - 1)) for h, c in zip(horses, corners)]


def build_from_inputs(target: str, data: dict) -> dict:
    """入力（{path: bytes}）から脚質の全体と件数を作る（ファイルも git も触らない）"""
    jra_cache = json.loads(data[JRA_CACHE_PATH].decode("utf-8"))
    ratios = defaultdict(list)
    days_used, days_excluded = [], {}
    races_used, excluded, excluded_list = 0, Counter(), []
    for p in sorted(k for k in data if k != JRA_CACHE_PATH):
        date = p.split("/")[1]
        d = json.loads(data[p].decode("utf-8"))
        race_ids = d.get("race_ids") or {}
        races = d.get("races") or {}
        if not d.get("complete") or not race_ids or set(races) != set(race_ids):
            days_excluded[date] = "全レースの結果がそろっていない"
            continue
        saved = d.get("schedule_check")
        if saved and saved.get("status") != "確定":   # 結果の取得のときの判定（採点したレースが一覧に無い、など）
            days_excluded[date] = f"結果の取得のときに予定を確定できなかった: {saved.get('reason')}"
            continue
        sched = day_schedule(date, race_ids, jra_cache)
        if sched["status"] != "確定":
            days_excluded[date] = sched["reason"]
            continue
        days_used.append(date)
        for label in sorted(races):
            ok, reason, rows = race_verdict(label, races[label])
            if not ok:
                excluded[reason] += 1
                excluded_list.append([date, label, reason])
                continue
            races_used += 1
            for hid, r in rows:
                ratios[hid].append(r)
    horses = {}
    for hid, rs in sorted(ratios.items()):
        avg = sum(rs) / len(rs)
        horses[hid] = {"style": classify(avg), "avg_ratio": round(avg, 3), "n_races": len(rs)}
    counts = Counter(v["style"] for v in horses.values())
    return {
        "period": {"first": days_used[0] if days_used else None, "last": days_used[-1] if days_used else None},
        "days_used": days_used, "days_excluded": days_excluded,
        "races_used": races_used, "races_excluded": dict(sorted(excluded.items())),
        "races_excluded_list": excluded_list,
        "horses": horses,
        "counts": {"horses": len(horses), "2走以上": sum(1 for v in horses.values() if v["n_races"] >= 2),
                   "1走": sum(1 for v in horses.values() if v["n_races"] == 1), **dict(sorted(counts.items()))},
    }


# ── その日の記録 ──
def style_dir(target: str, output_dir: Path = OUTPUT_DIR) -> Path:
    return output_dir / target / STYLE_DIR_NAME


def file_sha256(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def check_record(path: Path, target: str) -> dict:
    """その日の記録1つを読んで検査する。通らなければ StyleRecordError（呼び出し元は新しく作らずに止める）"""
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        raise StyleRecordError(f"記録を読めない: {path.name}: {type(e).__name__}: {e}")
    if not isinstance(rec, dict):
        raise StyleRecordError(f"記録の形が違う: {path.name}")
    st = rec.get("status")
    if st not in STATUSES:
        raise StyleRecordError(f"記録の状態が分からない: {path.name}: {st!r}")
    if rec.get("target_date") != target:
        raise StyleRecordError(f"記録の対象日が違う: {path.name}: {rec.get('target_date')}")
    if rec.get("run_id") != Path(path).stem:
        raise StyleRecordError(f"記録の実行IDがファイル名と違う: {path.name}: {rec.get('run_id')}")
    if not rec.get("style_build_version"):
        raise StyleRecordError(f"記録に作成コードの版が無い: {path.name}")
    if not rec.get("finished_at"):
        raise StyleRecordError(f"記録に作り終えた時刻が無い: {path.name}")
    if st == STATUS_FAILED:
        if not (rec.get("failure") or {}).get("reason"):
            raise StyleRecordError(f"作成失敗の記録に理由が無い: {path.name}")
        return rec
    # 作成成功
    if rec["style_build_version"] != STYLE_BUILD_VERSION:
        raise StyleRecordError(f"作成成功の記録の版が今のコードと違う（その日の扱いは手で決める）: {path.name}: "
                               f"{rec['style_build_version']}")
    try:
        fin = datetime.fromisoformat(rec["finished_at"])
    except ValueError:
        raise StyleRecordError(f"記録の作り終えた時刻が読めない: {path.name}")
    if fin >= deadline(target):
        raise StyleRecordError(f"作成成功の記録が期限（対象日 00:00）以降に作り終えている: {path.name}")
    horses = rec.get("horses")
    if not isinstance(horses, dict) or not horses:
        raise StyleRecordError(f"作成成功の記録の脚質が空・形が違う: {path.name}")
    for hid, v in horses.items():
        if not (isinstance(v, dict) and v.get("style") in THRESHOLDS and isinstance(v.get("n_races"), int)
                and v["n_races"] >= 1 and isinstance(v.get("avg_ratio"), (int, float))):
            raise StyleRecordError(f"作成成功の記録の馬の値の形が違う: {path.name}: {hid}")
    if (rec.get("counts") or {}).get("horses") != len(horses):
        raise StyleRecordError(f"作成成功の記録の頭数が合わない: {path.name}")
    if not rec.get("base_commit") or not isinstance(rec.get("inputs"), list) or not rec["inputs"]:
        raise StyleRecordError(f"作成成功の記録に基準の commit・入力の一覧が無い: {path.name}")
    if not rec.get("races_used"):
        raise StyleRecordError(f"作成成功の記録の使えたレースが0件: {path.name}")
    return rec


def day_records(target: str, output_dir: Path = OUTPUT_DIR) -> list:
    """その日の記録を全部検査して [(path, rec)]（実行IDの順）。1つでも通らなければ StyleRecordError"""
    d = style_dir(target, output_dir)
    if not d.exists():
        return []
    return [(p, check_record(p, target)) for p in sorted(d.glob("*.json"))]


def ref_of(path: Path, rec: dict) -> dict:
    """採点の quality に入れる参照"""
    return {"run": rec["run_id"], "sha256": file_sha256(path), "status": rec["status"],
            "version": rec["style_build_version"],
            "failure_reason": (rec.get("failure") or {}).get("reason") if rec["status"] == STATUS_FAILED else None}


def confirmed(records: list):
    """検査済みの記録から、確定した作成成功（無ければ None）。2つ以上あれば StyleRecordError"""
    ok = [(p, r) for p, r in records if r["status"] == STATUS_OK]
    if len(ok) > 1:
        raise StyleRecordError(f"作成成功の記録が2つ以上ある: {[p.name for p, _ in ok]}")
    return ok[0] if ok else None


def _write_new(path: Path, rec: dict) -> None:
    """一時ファイルに書いてから正式な名前にする（同じ名前があれば上書きしない）"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.link(tmp, path)
    os.unlink(tmp)


class StyleLock:
    """output/{対象日}/horse_style/.lock（gitignore）に fcntl.flock（排他・待たない）。prev_inputs.DateLock と同じ考え方"""

    def __init__(self, target: str, output_dir: Path = OUTPUT_DIR):
        import fcntl
        d = style_dir(target, output_dir)
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / ".lock"
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self._fd)
            raise StyleRecordError(f"同じ日の脚質の作成が動いている（ロック {self.path}）")

    def release(self):
        import fcntl
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)


def make_record(target: str, repo: Path = BASE_DIR, now=now_jst) -> dict:
    """1回作る（保存しない）。作成失敗も記録の形で返す"""
    from prev_inputs import new_run_id
    started = now()
    rec = {"status": None, "target_date": target, "run_id": new_run_id(started),
           "style_build_version": STYLE_BUILD_VERSION,
           "code": {"commit": None, "build_horse_style_sha256": file_sha256(Path(__file__).resolve())},
           "base_commit": None, "started_at": started.isoformat(timespec="seconds"),
           "inputs_fixed_at": None, "finished_at": None}
    try:
        if started >= deadline(target):
            raise RuntimeError("期限（対象日 00:00）の後に作ろうとした")
        commit = git(repo, "rev-parse", "HEAD").strip()
        rec["code"]["commit"] = rec["base_commit"] = commit
        listing, data = fix_inputs(target, commit, repo)
        rec["inputs"] = listing
        rec["inputs_fixed_at"] = now().isoformat(timespec="seconds")
        built = build_from_inputs(target, data)
        rec.update(built)
        if not built["races_used"]:
            raise RuntimeError("使えたレースが0件（空の脚質を作成成功にしない）")
        fin = now()
        rec["finished_at"] = fin.isoformat(timespec="seconds")
        if fin >= deadline(target):
            raise RuntimeError(f"作り終えた時刻 {rec['finished_at']} が期限（対象日 00:00）以上")
        rec["status"] = STATUS_OK
    except Exception as e:
        for k in ("horses", "races_excluded_list"):
            rec.pop(k, None)
        rec["status"] = STATUS_FAILED
        rec["finished_at"] = rec["finished_at"] or now().isoformat(timespec="seconds")
        rec["failure"] = {"reason": f"{type(e).__name__}: {e}" if not isinstance(e, RuntimeError) else str(e)}
    return rec


def ensure_style(target: str, output_dir: Path = OUTPUT_DIR, repo: Path = BASE_DIR, now=now_jst) -> tuple:
    """その日の脚質の記録を用意する（run_pickup_all.py の最初で呼ぶ）。戻り値 (記録, 参照)。
    確定した作成成功があればそれを使う。無ければ作る（作成失敗も記録して返す）。
    記録の検査に通らない・保存できないときは StyleRecordError（呼び出し元は HORSE_STYLE_RECORD_FAILED で知らせる）"""
    lock = StyleLock(target, output_dir)
    try:
        records = day_records(target, output_dir)
        got = confirmed(records)
        if got:
            path, rec = got
            log.info(f"脚質: 確定した記録を使う {path.name}（{rec['counts']['horses']}頭）")
            return rec, ref_of(path, rec)
        rec = make_record(target, repo, now)
        path = style_dir(target, output_dir) / f"{rec['run_id']}.json"
        try:
            _write_new(path, rec)
        except Exception as e:
            raise StyleRecordError(f"脚質の記録を保存できない（{rec['status']}）: {type(e).__name__}: {e}")
        check_record(path, target)   # 書いたものを読み直して検査する
        if rec["status"] == STATUS_OK:
            log.info(f"脚質: 作成成功 {path.name}（{rec['counts']['horses']}頭・レース {rec['races_used']}）")
        else:
            log.error(f"脚質: 作成失敗 {path.name}: {rec['failure']['reason']}")
        return rec, ref_of(path, rec)
    finally:
        lock.release()


def load_for_rerun(target: str, output_dir: Path = OUTPUT_DIR) -> tuple:
    """部分の再実行用: 作らない。確定した作成成功、無ければ最新の作成失敗。どちらも無ければ StyleRecordError"""
    records = day_records(target, output_dir)
    got = confirmed(records)
    if not got:
        failed = [(p, r) for p, r in records if r["status"] == STATUS_FAILED]
        if not failed:
            raise StyleRecordError(f"{target} の脚質の記録が無い（部分の再実行では作らない）")
        got = failed[-1]
    path, rec = got
    return rec, ref_of(path, rec)


# ── レースごとの結び付け（設計 4.1）──
def bind_race(rec, active_ids: dict) -> dict:
    """出走馬（取消・除外を除く {馬番: 馬ID}）を全体に結び付ける。作成失敗・記録なしなら {}"""
    if not rec or rec.get("status") != STATUS_OK:
        return {}
    horses = rec["horses"]
    out = {}
    for num, hid in sorted(active_ids.items(), key=lambda x: int(x[0])):
        if not hid:
            out[num] = {"horse_id": hid or "", "status": HS_NO_ID, "style": None, "n_races": 0, "avg_ratio": None}
            continue
        v = horses.get(hid)
        if not v:
            out[num] = {"horse_id": hid, "status": HS_NONE, "style": None, "n_races": 0, "avg_ratio": None}
        else:
            out[num] = {"horse_id": hid, "status": HS_OK if v["n_races"] >= 2 else HS_ONE,
                        "style": v["style"], "n_races": v["n_races"], "avg_ratio": v["avg_ratio"]}
    return out


def style_db_from_bound(bound: dict) -> dict:
    """score_horses に渡す {馬ID: {style, n_races, avg_ratio}}（脚質のある馬だけ）"""
    return {b["horse_id"]: {"style": b["style"], "n_races": b["n_races"], "avg_ratio": b["avg_ratio"]}
            for b in bound.values() if b["style"]}


# ── 旧方式（比べるためだけ。書かない）──
def legacy_build(output_dir: Path = OUTPUT_DIR) -> dict:
    """2026-06-14 までの作り方: 全日付の race_results.json（採点したレースだけ）、着順のある馬の数で割る"""
    horse_data = defaultdict(list)
    for d in sorted(x for x in os.listdir(output_dir) if re.match(r'^\d{8}$', x)):
        rr_path = output_dir / d / "race_results.json"
        if not rr_path.exists():
            continue
        rr = json.loads(rr_path.read_text(encoding="utf-8"))
        for label, horses in rr.items():
            n = len(horses)
            if n < 8:
                continue
            for h in horses:
                hid = h.get("horse_id")
                corners = h.get("corners", [])
                if not hid or not corners:
                    continue
                horse_data[hid].append((corners[0] - 1) / (n - 1) if n > 1 else 0.5)
    return {hid: {"style": classify(sum(r) / len(r)), "avg_ratio": round(sum(r) / len(r), 3), "n_races": len(r)}
            for hid, r in horse_data.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    m = sub.add_parser("make")
    m.add_argument("date")
    m.add_argument("--dry-run", action="store_true")
    sub.add_parser("legacy")
    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.print_help()
        print("\n引数なしでは何もしない（output/horse_style.json は書き換えない。2026-11-12〜）")
        return 2
    if args.cmd == "legacy":
        res = legacy_build()
        print(json.dumps({"horses": len(res), "2走以上": sum(1 for v in res.values() if v["n_races"] >= 2),
                          **Counter(v["style"] for v in res.values())}, ensure_ascii=False))
        return 0
    if args.dry_run:
        rec = make_record(args.date)
        summary = {k: rec.get(k) for k in ("status", "failure", "base_commit", "period", "days_excluded",
                                           "races_used", "races_excluded", "counts")}
        summary["days_used"] = len(rec.get("days_used") or [])
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0 if rec["status"] == STATUS_OK else 1
    try:
        rec, ref = ensure_style(args.date)
    except StyleRecordError as e:
        print(f"HORSE_STYLE_RECORD_FAILED: {e}")
        return 1
    print(json.dumps(ref, ensure_ascii=False))
    return 0 if rec["status"] == STATUS_OK else 1


if __name__ == "__main__":
    sys.exit(main())
