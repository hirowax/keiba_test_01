#!/usr/bin/env python3
"""
買い目の軸（◎）の再検証の記録・欠けの検査・記録の復元。
計画: docs/axis_recheck_plan_202611.md（2026-11-12 承認）。設計: docs/axis_record_design_202611.md。

使い方:
  python3 axis_record.py record 20261114
      run.sh の採点の後に動かす。対象日の 00:00（日本時間）より前だけ記録する。
      予定のレース（3指数の候補の CSV に載ったレース）を先に保存し、そのあとレースごとに候補1〜3・比べる基準の軸を決める。
  python3 axis_record.py check
      run_cron.sh が毎日動かす。開始日からの開催日について、記録の欠けと、結果・返還・複勝の払戻の欠けを調べる（成績は出さない）。
  python3 axis_record.py restore 20261114 --reason "17:00 の記録が失敗したため"
      期限前に GitHub の main にあった commit（GitHub 側の push の記録で確かめる）の入力とコードだけで計算し直し、
      記録の無いレースだけを足す。確かめられなければ断る。
  python3 axis_record.py compute 20261114 --out 結果.json
      内部用（restore が取り出した commit の中で呼ぶ）。ファイルを読んで計算するだけで、何も書き換えない。

この記録は、v5.2 で買い目を止めていることとは独立した研究用の記録（買い目の有無に関係なく記録する）。
"""
import argparse
import csv
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path

from data_quality import load_invalidated, race_quality_issues

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"

# 計画の版。数字・軸の決め方・除外の条件などを変えたら上げる（計画書の改訂の記録にも書く）
# 2026-11-12-2: 採点エラーのレースを全候補「軸なし（採点エラー）」として記録する（保留にしない）
AXIS_PLAN_VERSION = "2026-11-12-2"
# 計画の commit（2026-11-12 承認の c43a7cc に、同じ日の追記を足したもの。計画書の本文の確かめ用）
AXIS_PLAN_COMMIT = "b0eeb46788f25578782c04c4f5ef98dffbccaefa"
# 記録と検査を始める日（計画 4.3）。None のあいだは検査をしない
# 2026-11-12 決定。同じ日に、正式な評価の開始日は未定に変え、この日からの記録は試運転にした（計画書の「開始日の記録」）。
# 正式な開始日より前の記録も、欠けの検査の対象にする（試運転で仕組みを確かめるため）
AXIS_START_DATE = "20261114"

N_MIN = 8                      # 候補2の N（計画 4.1）
CHECK_STALE_DAYS = 8           # 検査の最後の成功がこれより古ければ、記録のときに知らせる（計画 4.3）
JST = timezone(timedelta(hours=9))
CANDIDATES = ("候補1", "候補2", "候補3", "比べる基準")

RECORD_NAME = "axis_candidates.json"
LOCK_NAME = ".axis_record.lock"
STATUS_NAME = "axis_check_status.json"
CSV_NAME = "全場_3指数重複馬.csv"
CSV_COLUMNS = ["開催場", "レース番号", "馬番", "馬名", "近走平均指数", "近走平均順位",
               "当該距離指数", "当該距離順位", "当該コース指数", "当該コース順位"]
RANK_COLUMNS = ("近走平均順位", "当該距離順位", "当該コース順位")
RANK_RE = re.compile(r"([1-9][0-9]*)位")   # 保存済みの CSV の表記は「1位」「2位」「3位」だけ（2026-11-12 確認）
REPO_SLUG = "hirowax/keiba_test_01"
HORSE_ID_RE = re.compile(r"[0-9A-Za-z]+")   # netkeiba の馬ID（英字を含む ID もあるので英数字）


class RecordError(Exception):
    """そのレースを記録できない（入力の破損・必須の項目の欠け）。軸なしにはしない"""


class RecordFailure(Exception):
    """記録・検査・復元の処理を続けられない"""


# ── 小さな道具 ────────────────────────────────────────────────

def now_jst() -> datetime:
    return datetime.now(JST)


def deadline(date: str) -> datetime:
    """入力の確定の期限 = 対象日の 00:00（日本時間）"""
    return datetime.strptime(date, "%Y%m%d").replace(tzinfo=JST)


def payout_deadline(date: str) -> datetime:
    """払戻の取得の期限 = 対象日より後の最初の水曜日の終わり（その次の木曜日の 00:00 日本時間）。
    例: 土曜 11/14・日曜 11/15 → 水曜 11/18 の終わり。月曜 11/23 → 水曜 11/25 の終わり。水曜の開催 → 翌週の水曜の終わり"""
    d = datetime.strptime(date, "%Y%m%d").date()
    ahead = (2 - d.weekday()) % 7 or 7
    wed = d + timedelta(days=ahead)
    return datetime(wed.year, wed.month, wed.day, tzinfo=JST) + timedelta(days=1)


def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path):
    if not path.exists():
        return None
    return sha256_bytes(path.read_bytes())


def iso(dt: datetime) -> str:
    return dt.astimezone(JST).isoformat(timespec="seconds")


def norm_name(s) -> str:
    """馬名の違いで許すのは空白だけ（CLAUDE.md の照合の決まり）"""
    return re.sub(r"\s+", "", str(s or ""))


def atomic_write_json(path: Path, data) -> None:
    """一時ファイルに書いてから置き換える（途中で止まっても、元のファイルは壊れない）"""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextmanager
def file_lock(path: Path):
    """同じ日の記録・復元を同時に動かさない（OS のファイルロック。プロセスが終われば外れる）"""
    f = open(path, "a")
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RecordFailure(f"ほかの記録・復元が動いている（{path}）")
        yield
    finally:
        f.close()


def load_json_file(path: Path):
    """無ければ None。壊れていれば RecordFailure（壊れたファイルを空として扱わない）"""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RecordFailure(f"ファイルが壊れている: {path}（{e}）")


def load_record(path: Path):
    """記録ファイル。無ければ None。読めない・形が違う（{} や null を含む）ときは RecordFailure（上書きしない）"""
    rec = load_json_file(path)
    if rec is None and not path.exists():
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("races"), dict) or not isinstance(rec.get("runs"), list) \
            or not (rec.get("schedule") is None or isinstance(rec.get("schedule"), dict)):
        raise RecordFailure(f"記録ファイルの形が違う（上書きしない）: {path}")
    return rec


# ── 予定のレース（計画 4.2）─────────────────────────────────────

def load_schedule(date_dir: Path):
    """3指数の候補の CSV から予定のレースを決める。
    戻り値: (schedule, rows_by_label)。schedule["status"] は「確定」か「確定できない」。
    CSV が正常で候補が0件なら「確定」で races=[]。CSV が無い・壊れているときは「確定できない」（対象0件にしない）"""
    path = date_dir / CSV_NAME

    def unknown(reason):
        return {"status": "確定できない", "reason": reason}, {}

    if not path.exists():
        return unknown("3指数の候補の CSV が無い")
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except (UnicodeDecodeError, csv.Error) as e:
        return unknown(f"3指数の候補の CSV を読めない: {e}")
    if not rows or rows[0] != CSV_COLUMNS:
        return unknown("3指数の候補の CSV の見出しの行が違う")
    order, by = [], {}
    for i, row in enumerate(rows[1:], start=2):
        if len(row) != len(CSV_COLUMNS):
            return unknown(f"3指数の候補の CSV の {i} 行目の列の数が違う")
        d = dict(zip(CSV_COLUMNS, row))
        if not d["開催場"] or not re.fullmatch(r"[1-9][0-9]?R", d["レース番号"]) or not d["馬番"].isdigit():
            return unknown(f"3指数の候補の CSV の {i} 行目の開催場・レース番号・馬番が読めない")
        label = d["開催場"] + d["レース番号"]
        if label not in by:
            by[label] = []
            order.append(label)
        if any(r["馬番"] == d["馬番"] for r in by[label]):
            return unknown(f"3指数の候補の CSV で {label} の馬番 {d['馬番']} が重複している")
        by[label].append(d)
    return {"status": "確定", "races": order, "csv_sha256": sha256_file(path)}, by


# ── 軸の決め方（計画 4.1）───────────────────────────────────────

def _none(reason, **extra):
    return {"state": "軸なし", "reason": reason, **extra}


def decide_race(rdata: dict, csv_rows: list, invalid_reason=None):
    """1レースの候補1〜3・比べる基準。戻り値: ({候補: 結果}, 品質の問題のリスト)。
    入力が壊れている・必須の項目が無いときは RecordError（軸なしにしない）"""
    scratched = rdata.get("scratched_nums") or {}
    names = rdata.get("horse_name_full")
    ids = rdata.get("horse_id_map") or {}
    scored = rdata.get("scored")
    if not isinstance(scratched, dict) or not isinstance(ids, dict):
        raise RecordError("取消の馬・馬IDの記録の形が違う")
    if not isinstance(names, dict) or not names:
        raise RecordError("出馬表の馬の一覧（horse_name_full）が無い")
    if not isinstance(scored, list):
        raise RecordError("採点の候補（scored）が無い")
    scores = {}
    for h in scored:
        num = str((h or {}).get("馬番", "")).strip()
        s = (h or {}).get("score")
        if not num.isdigit() or isinstance(s, bool) or not isinstance(s, int) or num in scores:
            raise RecordError(f"採点の候補の馬番・点数が読めない、または重複している（馬番 {num!r}）")
        if num not in names:
            raise RecordError(f"採点の候補の馬番 {num} が出馬表の馬の一覧に無い")
        if norm_name((h or {}).get("馬名")) != norm_name(names[num]):
            raise RecordError(f"採点の候補の馬番 {num} の馬名が出馬表と違う（{h.get('馬名')!r} / {names[num]!r}）")
        scores[num] = s
    # CSV の馬は、馬番で出馬表の馬に結び付け、馬名も一致することを確かめる（許す違いは空白だけ）
    for r in csv_rows:
        if r["馬番"] not in names:
            raise RecordError(f"CSV の馬番 {r['馬番']} が出馬表の馬の一覧に無い")
        if norm_name(r["馬名"]) != norm_name(names[r["馬番"]]):
            raise RecordError(f"CSV の馬番 {r['馬番']} の馬名が出馬表と違う（{r['馬名']!r} / {names[r['馬番']]!r}）")
    csv_nums = {r["馬番"] for r in csv_rows}
    if set(scores) != csv_nums - set(scratched):
        raise RecordError(f"3指数の候補が CSV（取消を除く {sorted(csv_nums - set(scratched), key=int)}）と"
                          f"採点の候補（{sorted(scores, key=int)}）で違う")

    issues = race_quality_issues(rdata, invalid_reason)
    if issues:
        # 品質の判定が正常にでき、その結果として問題がある → 比べる基準も含め、すべて軸なし
        return {c: _none("品質の問題") for c in CANDIDATES}, issues

    def axis(num, **extra):
        hid = ids.get(num)
        if not isinstance(hid, str) or not HORSE_ID_RE.fullmatch(hid):
            raise RecordError(f"軸の馬（馬番 {num}）の馬IDが無い・形が違う: {hid!r}")
        return {"state": "軸あり", "num": num, "name": names[num], "horse_id": hid, **extra}

    out = {}
    # 候補1・2: 最上位の同点だけ軸なし（下位の同点は関係しない）
    if not scores:
        out["候補1"] = _none("候補がいない")
        out["候補2"] = _none("候補がいない")
    else:
        top = max(scores.values())
        tops = sorted((n for n, s in scores.items() if s == top), key=int)
        if len(tops) > 1:
            out["候補1"] = _none("同点で決められない", tied=tops, score=top)
            out["候補2"] = _none("同点で決められない", tied=tops, score=top)
        else:
            out["候補1"] = axis(tops[0], score=top)
            out["候補2"] = axis(tops[0], score=top) if top >= N_MIN else _none(f"{N_MIN}点未満", score=top)

    # 候補3: 3指数のページの順位の合計が最も小さい馬。1頭でも順位が読めなければ、候補3全体を確認不能
    rows = [r for r in csv_rows if r["馬番"] not in scratched]
    if not rows:
        out["候補3"] = _none("候補がいない")
    else:
        sums, bad = {}, []
        for r in rows:
            ms = [RANK_RE.fullmatch(r[k].strip()) for k in RANK_COLUMNS]
            if not all(ms):
                bad.append(r["馬番"])
            else:
                sums[r["馬番"]] = sum(int(m.group(1)) for m in ms)
        if bad:
            out["候補3"] = _none("確認不能", detail=f"順位が読めない馬がいる: {sorted(bad, key=int)}")
        else:
            low = min(sums.values())
            lows = sorted((n for n, s in sums.items() if s == low), key=int)
            out["候補3"] = (_none("同点で決められない", tied=lows, rank_sum=low) if len(lows) > 1
                           else axis(lows[0], rank_sum=low))

    # 比べる基準: 出馬表の出走全馬（取消・除外を除く）から、採点した時点の人気が1の馬
    runners = sorted(set(names) - set(scratched), key=int)
    pop = rdata.get("pop_map")
    if not isinstance(pop, dict) or not pop:
        out["比べる基準"] = _none("確認不能", detail="人気（pop_map）が無い")
    else:
        missing = [n for n in runners if not str(pop.get(n, "")).strip().isdigit()]
        if missing:
            out["比べる基準"] = _none("確認不能", detail=f"人気が取れていない出走馬がいる: {missing}")
        else:
            ones = [n for n in runners if int(str(pop[n]).strip()) == 1]
            if not ones:
                out["比べる基準"] = _none("確認不能", detail="人気が1の馬がいない")
            elif len(ones) > 1:
                out["比べる基準"] = _none("同点で決められない", tied=ones)
            else:
                out["比べる基準"] = axis(ones[0], pop=1)
    return out, []


# ── 1日分の計算（何も書き換えない）──────────────────────────────

def code_versions() -> dict:
    """記録する版（計画 4.2）。取り込めなければ RecordFailure"""
    try:
        import race_pickup
        import prev_inputs
        import scraper
        import index_table_check
    except Exception as e:  # noqa: BLE001
        raise RecordFailure(f"版を読むためのモジュールを取り込めない: {e}")
    return {
        "SCORING_VERSION": race_pickup.SCORING_VERSION,
        "DATA_TOP_READER_VERSION": race_pickup.DATA_TOP_READER_VERSION,
        "COLLECTOR_VERSION": race_pickup.COLLECTOR_VERSION,
        "PREV_FETCH_VERSION": prev_inputs.PREV_FETCH_VERSION,
        "TOP1_RULE": prev_inputs.TOP1_RULE,
        "INDEX_READER_VERSION": scraper.INDEX_READER_VERSION,
        "CANDIDATE_RULE_VERSION": scraper.CANDIDATE_RULE_VERSION,
        "INDEX_TABLE_CHECK_VERSION": index_table_check.INDEX_TABLE_CHECK_VERSION,
    }


def git_out(args, cwd=BASE_DIR):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


RULE_FILES = ("axis_record.py", "data_quality.py", "race_pickup.py", "prev_inputs.py", "scraper.py",
              "index_table_check.py")


def code_state(cwd=BASE_DIR) -> dict:
    """使ったコードの commit と、未コミットの変更があるか（追跡している .py の変更と、
    判定に使うファイルが commit されていない場合）"""
    rc, head = git_out(["rev-parse", "HEAD"], cwd)
    rc2, changed = git_out(["status", "--porcelain", "--untracked-files=no", "--", "*.py"], cwd)
    rc3, untracked = git_out(["status", "--porcelain", "--untracked-files=all", "--", *RULE_FILES], cwd)
    ok = rc2 == 0 and rc3 == 0
    return {"code_commit": head if rc == 0 else None,
            "code_dirty": (bool(changed or untracked) if ok else None)}


def rules_identity() -> dict:
    """同じ入力かどうかの判定に入れる規則（計画の版・N・判定のコード）"""
    return {
        "plan_version": AXIS_PLAN_VERSION,
        "n_min": N_MIN,
        "axis_record_sha256": sha256_file(Path(__file__).resolve()),
        "data_quality_sha256": sha256_file(BASE_DIR / "data_quality.py"),
    }


def load_prev_entry(date_dir: Path, date: str, label: str, rdata: dict, cache: dict):
    """そのレースの採点に使った前走データの記録（prev_inputs/{実行ID}.json のそのレースの部分）。
    採点結果が前走データの記録を指していなければ None（品質の判定で問題になる）。
    指しているのに、ファイルが無い・形が違う・実行ID・対象日・レース・race_id・候補の馬が合わなければ RecordError"""
    q = rdata.get("quality") or {}
    run = q.get("prev_inputs_run")
    if not run:
        if q.get("prev_fetch"):
            raise RecordError("前走データの取り方の版があるのに、前走データの記録（prev_inputs_run）を指していない")
        return None
    if not re.fullmatch(r"[0-9A-Za-z_]+", str(run)):
        raise RecordError(f"前走データの記録の実行IDの形が違う: {run!r}")
    if run not in cache:
        path = date_dir / "prev_inputs" / f"{run}.json"
        if not path.exists():
            raise RecordError(f"前走データの記録が無い: prev_inputs/{run}.json")
        try:
            cache[run] = load_json_file(path)
        except RecordFailure as e:
            raise RecordError(str(e))
    data = cache[run]
    if not isinstance(data, dict) or not isinstance(data.get("races"), dict):
        raise RecordError(f"前走データの記録の形が違う: prev_inputs/{run}.json")
    if data.get("run_id") != run or data.get("target_date") != date:
        raise RecordError(f"前走データの記録の実行ID・対象日が合わない: prev_inputs/{run}.json")
    entry = data["races"].get(label)
    if not isinstance(entry, dict) or not isinstance(entry.get("horses"), dict):
        raise RecordError(f"前走データの記録にこのレースが無い: prev_inputs/{run}.json")
    if rdata.get("race_id") and entry.get("race_id") != rdata.get("race_id"):
        raise RecordError(f"前走データの記録の race_id が採点結果と違う: {entry.get('race_id')} / {rdata.get('race_id')}")
    ids = rdata.get("horse_id_map") or {}
    missing = [str(h.get("馬番")) for h in (rdata.get("scored") or [])
               if ids.get(str(h.get("馬番"))) not in entry["horses"]]
    if missing:
        raise RecordError(f"前走データの記録に候補の馬が無い（馬番 {missing}）")
    return entry


def compute_day(date: str, output_dir: Path = OUTPUT_DIR) -> dict:
    """ファイルを読んで1日分を計算する。戻り値の races は記録（採点エラーのレースは全候補「軸なし（採点エラー）」）、
    cannot_record は記録できないレース（理由と入力のハッシュ）"""
    date_dir = output_dir / date
    schedule, rows_by = load_schedule(date_dir)
    result = {"date": date, "plan_version": AXIS_PLAN_VERSION, "plan_commit": AXIS_PLAN_COMMIT,
              "schedule": schedule, "races": {}, "cannot_record": {}, "files": {}}
    if schedule["status"] != "確定":
        return result
    pickup_path = date_dir / "pickup_scores.json"
    try:
        pickup = load_json_file(pickup_path)
        if not isinstance(pickup, dict) or not isinstance(pickup.get("races"), dict):
            raise RecordFailure("採点結果（pickup_scores.json）が無い、または形が違う")
        if pickup.get("date") != date:
            raise RecordFailure(f"採点結果の対象日（{pickup.get('date')}）が {date} と違う")
        invalid = load_invalidated(date, output_dir)
    except (RecordFailure, json.JSONDecodeError) as e:
        for label in schedule["races"]:
            result["cannot_record"][label] = {"reason": str(e), "input_key": None}
        return result
    races = pickup["races"]
    rules = rules_identity()
    common = {**rules, "pickup_top": {k: v for k, v in pickup.items() if k != "races"}}
    prev_runs = sorted({(r.get("quality") or {}).get("prev_inputs_run")
                        for r in races.values() if isinstance(r, dict)} - {None})
    result["files"] = {
        "pickup_scores.json": sha256_file(pickup_path),
        CSV_NAME: schedule["csv_sha256"],
        "invalidated.json": sha256_file(date_dir / "invalidated.json"),
        "prev_inputs": {run: sha256_file(date_dir / "prev_inputs" / f"{run}.json") for run in prev_runs},
        "data_quality.py": rules["data_quality_sha256"],
        "axis_record.py": rules["axis_record_sha256"],
    }
    prev_cache = {}
    for label in schedule["races"]:
        rdata = races.get(label)
        prev_part = None
        try:
            if rdata is None:
                raise RecordError("採点結果にこのレースが無い")
            if not isinstance(rdata, dict):
                raise RecordError("採点結果のこのレースの形が違う")
            if "error" not in rdata:
                prev_part = load_prev_entry(date_dir, date, label, rdata, prev_cache)
        except RecordError as e:
            prev_part = {"読めない": str(e)}
            key = race_key(common, rdata, rows_by[label], invalid.get(label), prev_part)
            result["cannot_record"][label] = {"reason": str(e), "input_key": key}
            continue
        # 同じ入力かどうかには、そのレースで使った前走データの記録も入れる
        key = race_key(common, rdata, rows_by[label], invalid.get(label), prev_part)
        try:
            if "error" in rdata:
                # 採点エラー: 記録の時点の入力で、全候補を「軸なし（採点エラー）」として明示的に記録する（計画 4.2 追記）
                result["races"][label] = {
                    "input_key": key, "quality_issues": race_quality_issues(rdata, invalid.get(label)),
                    "candidates": {c: _none("採点エラー", detail=str(rdata["error"])) for c in CANDIDATES}}
                continue
            cands, issues = decide_race(rdata, rows_by[label], invalid.get(label))
            result["races"][label] = {"input_key": key, "quality_issues": issues, "candidates": cands}
        except RecordError as e:
            result["cannot_record"][label] = {"reason": str(e), "input_key": key}
    return result


def race_key(common, rdata, csv_rows, invalid_reason, prev_part) -> str:
    return sha256_bytes(canonical({**common, "race": rdata, "csv_rows": csv_rows,
                                   "invalid_reason": invalid_reason, "prev_inputs_race": prev_part}))


def run_meta() -> dict:
    return {"versions": code_versions(), **code_state()}


# ── 記録ファイルへの反映（書き換えない・同じなら何もしない・違えば止める）───

def new_record(date: str) -> dict:
    return {"date": date, "plan_version": AXIS_PLAN_VERSION, "plan_commit": AXIS_PLAN_COMMIT,
            "schedule": None, "races": {}, "runs": []}


def merge_schedule(rec: dict, schedule: dict, at: str):
    """予定を記録に入れる。すでに確定した予定と違えば、元のまま残して問題を返す"""
    old = rec.get("schedule")
    if old and old.get("status") == "確定":
        if schedule.get("status") != "確定":
            return f"予定のレースが確定していたのに、今は確定できない（{schedule.get('reason')}）"
        if old.get("races") != schedule.get("races") or old.get("csv_sha256") != schedule.get("csv_sha256"):
            return "予定のレース（3指数の候補の CSV）が、先に保存した予定と違う"
        return None
    rec["schedule"] = {**schedule, "saved_at": at}
    return None


def merge_computed(rec: dict, computed: dict, mode: str, meta: dict, at: str) -> list:
    """計算結果を記録に足す。戻り値は問題のリスト（空なら問題なし）"""
    problems, added, unchanged = [], [], []
    for label, r in computed["races"].items():
        old = rec["races"].get(label)
        if old is not None:
            if old.get("input_key") == r["input_key"]:
                unchanged.append(label)
            else:
                problems.append(f"{label}: すでにある記録と入力・規則が違う → 書き換えない")
            continue
        rec["races"][label] = {**r, "mode": mode, "recorded_at": at, **meta}
        added.append(label)
    for label, c in computed["cannot_record"].items():
        old = rec["races"].get(label)
        if old is None:
            problems.append(f"{label}: 記録できない（{c['reason']}）")
        elif old.get("input_key") != c["input_key"]:
            # すでに記録があるのに入力が変わり、今は記録できない → 書き換えず、問題として返す
            problems.append(f"{label}: すでにある記録と入力・規則が違う（今は記録できない: {c['reason']}）→ 書き換えない")
        else:
            unchanged.append(label)
    rec["runs"].append({"at": at, "mode": mode, "added": added, "unchanged": unchanged, "problems": problems,
                        "files": computed.get("files"), **meta})
    return problems


def check_status_stale(now: datetime, output_dir: Path = OUTPUT_DIR):
    """検査の最後の成功が古い（または一度も無い）なら、その理由を返す"""
    if AXIS_START_DATE is None:
        return None
    try:
        st = load_json_file(output_dir / STATUS_NAME)
    except RecordFailure as e:
        return str(e)
    if not st or not st.get("last_completed_at"):
        return "検査が一度も最後まで動いていない"
    last = datetime.fromisoformat(st["last_completed_at"])
    if now - last > timedelta(days=CHECK_STALE_DAYS):
        return f"検査の最後の成功が {st['last_completed_at']}（{CHECK_STALE_DAYS}日より前）"
    return None


def cmd_record(date: str, now: datetime = None, output_dir: Path = OUTPUT_DIR, meta: dict = None) -> int:
    now = now or now_jst()
    stale = check_status_stale(now, output_dir)
    if stale:
        print(f"AXIS_CHECK_STALE: 軸の記録の欠けの検査が止まっているかもしれない: {stale}")
    if now >= deadline(date):
        print(f"{date}: 入力の確定の期限（{date} 00:00）を過ぎているので記録しない（記録の欠けは check が知らせる。復元は restore）")
        return 0
    date_dir = output_dir / date
    if not date_dir.is_dir():
        raise RecordFailure(f"出力のフォルダが無い: {date_dir}")
    at = iso(now)
    with file_lock(date_dir / LOCK_NAME):
        path = date_dir / RECORD_NAME
        rec = load_record(path) or new_record(date)
        # 1. 予定のレースを、軸の計算より先に保存する
        schedule, _ = load_schedule(date_dir)
        sched_problem = merge_schedule(rec, schedule, at)
        atomic_write_json(path, rec)
        if sched_problem:
            raise RecordFailure(sched_problem)
        if schedule["status"] != "確定":
            rec["runs"].append({"at": at, "mode": "期限内", "problems": [f"予定のレースを確定できない: {schedule['reason']}"]})
            atomic_write_json(path, rec)
            raise RecordFailure(f"予定のレースを確定できない: {schedule['reason']}")
        # 2. 軸を決めて足す
        computed = compute_day(date, output_dir)
        meta = meta if meta is not None else run_meta()
        problems = merge_computed(rec, computed, "期限内", meta, at)
        atomic_write_json(path, rec)
    n = len(rec["schedule"]["races"])
    print(f"{date}: 予定 {n}レース・記録 {len(rec['races'])}")
    for p in problems:
        print(f"  ⚠️ {p}")
    return 1 if problems else 0


# ── 欠けの検査（計画 4.3。成績は出さない）────────────────────────

def load_calendar_dates(years, base_dir: Path = BASE_DIR) -> list:
    dates = []
    for y in sorted(years):
        cf = base_dir / f"jra_calendar_{y}.json"
        data = load_json_file(cf)
        if data is None:
            raise RecordFailure(f"カレンダーのファイルが無い: {cf.name}")
        dates.extend(data["dates"])
    return sorted(set(dates))


def purchase_gap(date_dir: Path, label: str, num: str, name: str, cache: dict):
    """軸ありの購入が確定できるか。できなければ理由（当たり外れは返さない）"""
    def get(fname):
        if fname not in cache:
            cache[fname] = load_json_file(date_dir / fname)
        return cache[fname]
    results = get("race_results.json")
    if results is None:
        return "結果（race_results.json）が無い"
    race = results.get(label)
    if race is None:
        return "結果にこのレースが無い"
    payouts = get("payouts_jra.json")
    rp = (payouts or {}).get(label)
    runner = next((h for h in race if str(h.get("num")) == num), None)
    if runner is not None:
        if norm_name(runner.get("name")) != norm_name(name):
            return "結果の馬名が記録と違う（照合できない）"
        if not rp or not rp.get("fukusho"):
            return "複勝の払戻が無い（取れていないか、発売されなかったかを区別できない）"
        return None
    if rp and num in {str(n) for n in ((rp.get("refund") or {}).get("umaban") or [])}:
        return None   # 返還（取消・除外）
    nonfin = get("race_nonfinishers.json") or {}
    for o in nonfin.get(label, []):
        if str(o.get("num")) == num and o.get("status") in ("中止", "失格") and norm_name(o.get("name")) == norm_name(name):
            # 中止・失格は不的中だが、評価する購入が成立したか（複勝が発売され、払戻を取れているか）は別に確かめる
            if not rp or not rp.get("fukusho"):
                return "中止・失格だが、複勝の払戻が無い（取れていないか、発売されなかったかを区別できない）"
            return None
    if not rp:
        return "払戻が無く、返還かどうか確かめられない"
    return "結果に無く、返還・中止・失格の記録も無い"


AXIS_STATES = ("軸あり", "軸なし")


def race_record_problems(r) -> list:
    """1レースの記録の形の検査。候補1〜3・比べる基準がそろい、状態が正しく、必須の項目があること"""
    if not isinstance(r, dict):
        return ["レースの記録の形が違う"]
    out = []
    if not re.fullmatch(r"[0-9a-f]{64}", str(r.get("input_key") or "")):
        out.append("入力のハッシュ（input_key）が無い・形が違う")
    if r.get("mode") not in ("期限内", "復元"):
        out.append(f"記録のモードが違う: {r.get('mode')!r}")
    cands = r.get("candidates")
    if not isinstance(cands, dict):
        return out + ["候補の記録が無い"]
    if set(cands) != set(CANDIDATES):
        out.append(f"候補がそろっていない（あるもの: {sorted(cands)}）")
    for c in CANDIDATES:
        a = cands.get(c)
        if not isinstance(a, dict) or a.get("state") not in AXIS_STATES:
            out.append(f"{c}: 状態が無い・知らない状態（{(a or {}).get('state') if isinstance(a, dict) else a!r}）")
        elif a["state"] == "軸あり":
            hid = a.get("horse_id")
            if not str(a.get("num") or "").isdigit() or not str(a.get("name") or "").strip() \
                    or not isinstance(hid, str) or not HORSE_ID_RE.fullmatch(hid):
                out.append(f"{c}: 軸ありなのに馬番・馬名・馬IDが無い・形が違う")
        elif not str(a.get("reason") or "").strip():
            out.append(f"{c}: 軸なしなのに理由が無い")
    return out


def run_check(now: datetime, output_dir: Path = OUTPUT_DIR, start: str = None, base_dir: Path = BASE_DIR) -> dict:
    gaps = []
    today = now.astimezone(JST).strftime("%Y%m%d")
    years = range(int(start[:4]), int(today[:4]) + 1)
    days = [d for d in load_calendar_dates(years, base_dir) if start <= d <= today]
    for d in days:
        if now < deadline(d):
            continue
        date_dir = output_dir / d
        schedule, _ = load_schedule(date_dir)   # 予定は CSV から決める（記録の出力からは決めない）
        try:
            rec = load_record(date_dir / RECORD_NAME)
        except RecordFailure as e:
            gaps.append({"date": d, "race": None, "kind": "記録なし", "reason": str(e), "notify": True})
            continue
        if schedule["status"] != "確定":
            gaps.append({"date": d, "race": None, "kind": "記録なし",
                         "reason": f"予定のレースを確定できない: {schedule['reason']}", "notify": True})
            continue
        if rec is None:
            gaps.append({"date": d, "race": None, "kind": "記録なし", "reason": "記録のファイルが無い", "notify": True})
            continue
        rs = rec.get("schedule") or {}
        if rs.get("races") != schedule["races"]:
            gaps.append({"date": d, "race": None, "kind": "記録なし",
                         "reason": "記録の予定のレースが CSV と違う", "notify": True})
        cache = {}
        over = now >= payout_deadline(d)
        for label in sorted(set(rec["races"]) - set(schedule["races"])):
            gaps.append({"date": d, "race": label, "kind": "記録不正", "reason": "予定に無いレースの記録がある", "notify": True})
        for label in schedule["races"]:
            r = rec["races"].get(label)
            if r is None:
                gaps.append({"date": d, "race": label, "kind": "記録なし", "reason": "このレースの記録が無い", "notify": True})
                continue
            bad = race_record_problems(r)
            if bad:
                gaps.append({"date": d, "race": label, "kind": "記録不正", "reason": " / ".join(bad), "notify": True})
                continue
            if d >= today:
                continue   # 結果はまだ出ていない
            for c in CANDIDATES:
                a = r["candidates"][c]
                if a["state"] != "軸あり":
                    continue
                reason = purchase_gap(date_dir, label, a["num"], a["name"], cache)
                if reason:
                    gaps.append({"date": d, "race": label, "candidate": c, "kind": "結果・払戻の欠け",
                                 "reason": reason, "overdue": over, "notify": over})
    return {"gaps": gaps}


def cmd_check(now: datetime = None, output_dir: Path = OUTPUT_DIR, start: str = "default",
              base_dir: Path = BASE_DIR) -> int:
    now = now or now_jst()
    start = AXIS_START_DATE if start == "default" else start
    status_path = output_dir / STATUS_NAME
    if start is None:
        atomic_write_json(status_path, {"last_completed_at": iso(now), "start_date": None,
                                        "has_gaps": False, "note": "開始日が未設定なので検査していない", "gaps": []})
        print("軸の記録の検査: 開始日が未設定なので検査していない")
        return 0
    res = run_check(now, output_dir, start, base_dir)   # 失敗したら例外。状態のファイルは書き換えない
    gaps = res["gaps"]
    rec_gaps = [g for g in gaps if g["kind"] in ("記録なし", "記録不正")]
    over = [g for g in gaps if g["kind"] == "結果・払戻の欠け" and g["overdue"]]
    waiting = [g for g in gaps if g["kind"] == "結果・払戻の欠け" and not g["overdue"]]
    atomic_write_json(status_path, {
        "last_completed_at": iso(now), "start_date": start, "has_gaps": bool(gaps),
        "record_gaps": len(rec_gaps), "payout_gaps_overdue": len(over), "payout_gaps_waiting": len(waiting),
        "payout_note": "複勝の払戻が無い欠けには、払戻を取れていない場合と、複勝が発売されなかった場合が混ざる（区別できない）",
        "gaps": gaps,
    })
    for g in gaps:
        print(f"  {g['date']} {g.get('race') or ''} {g.get('candidate') or ''} {g['kind']}: {g['reason']}"
              + ("（期限切れ）" if g.get("overdue") else ""))
    print(f"軸の記録の検査: 記録なし・記録不正 {len(rec_gaps)}・払戻などの欠け 期限切れ {len(over)}・期限前 {len(waiting)}")
    notify = [g for g in gaps if g.get("notify")]
    if notify:
        def where(gs):
            return " ".join(sorted({f"{g['date']}{g.get('race') or ''}" for g in gs})[:8])
        parts = []
        if rec_gaps:
            parts.append(f"記録なし・記録不正 {len(rec_gaps)}件 {where(rec_gaps)}")
        if over:
            parts.append(f"払戻などの欠け（水曜の期限切れ） {len(over)}件 {where(over)}")
        # LINE の通知の本文に使うので、引用符・改行を入れない
        print("AXIS_NOTIFY: " + " / ".join(parts).replace('"', "").replace("\\", ""))
        return 2
    return 0


# ── 記録の復元（計画 4.2）────────────────────────────────────────

def fetch_push_activity() -> list:
    """GitHub 側で記録された main の更新（時刻はサーバーの時刻）。種類は絞らない
    （push・force_push・pr_merge・merge_queue_merge・branch_creation・branch_deletion。
    https://docs.github.com/en/rest/repos/repos#list-repository-activities）。[{"timestamp", "after", "activity_type"}]"""
    r = subprocess.run(["gh", "api", "--paginate",
                        f"repos/{REPO_SLUG}/activity?per_page=100&ref=refs/heads/main",
                        "--jq", ".[] | {timestamp, after, activity_type}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RecordFailure(f"GitHub の push の記録を取れない: {r.stderr.strip()}")
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip()]


def parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def run_compute_in_worktree(repo: Path, commit: str, date: str) -> dict:
    """commit を一時的な作業場所に取り出し、その中のコードで compute だけを動かす（採点・買い目・公開は動かさない）"""
    tmp = Path(tempfile.mkdtemp(prefix="axis_restore_"))
    wt = tmp / "wt"
    try:
        rc, out = git_out(["worktree", "add", "--detach", str(wt), commit], repo)
        if rc != 0:
            raise RecordFailure(f"commit {commit} を取り出せない: {out}")
        out_json = tmp / "computed.json"
        r = subprocess.run([sys.executable, "axis_record.py", "compute", date, "--out", str(out_json)],
                           cwd=wt, capture_output=True, text=True)
        if r.returncode != 0:
            raise RecordFailure(f"取り出した commit のコードで計算できなかった: {r.stderr.strip()[-500:]}")
        return json.loads(out_json.read_text(encoding="utf-8"))
    finally:
        git_out(["worktree", "remove", "--force", str(wt)], repo)
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_restore(date: str, reason: str, now: datetime = None, output_dir: Path = OUTPUT_DIR, repo: Path = BASE_DIR,
                activity_fetcher=fetch_push_activity, runner=run_compute_in_worktree) -> int:
    now = now or now_jst()
    if not (reason or "").strip():
        raise RecordFailure("復元の理由（--reason）が無い")
    limit = deadline(date)
    acts = activity_fetcher()
    before = [a for a in acts if parse_utc(a["timestamp"]) < limit]
    if not before:
        raise RecordFailure(f"{date} 00:00 より前の main への push を、GitHub の記録で確かめられない（記録が残る範囲より古い、など）→ 復元しない")
    # 種類を問わず、期限より前の最後の main の更新を使う（main を消した更新なら、そのときの main は無い）
    last = max(before, key=lambda a: parse_utc(a["timestamp"]))
    commit = last.get("after")
    if not re.fullmatch(r"[0-9a-f]{40}", commit or "") or set(commit) == {"0"}:
        raise RecordFailure(f"期限前の最後の main の更新（{last.get('activity_type')}）の commit の ID の形が違う・main が無い: {commit!r}")
    if git_out(["cat-file", "-e", f"{commit}^{{commit}}"], repo)[0] != 0:
        raise RecordFailure(f"commit {commit} が手元に無い（git fetch origin をしてからやり直す）")
    for p in (f"output/{date}/{CSV_NAME}", f"output/{date}/pickup_scores.json", "axis_record.py"):
        if git_out(["cat-file", "-e", f"{commit}:{p}"], repo)[0] != 0:
            raise RecordFailure(f"期限前の commit {commit[:7]} に {p} が無い → 復元しない")
    if git_out(["merge-base", "--is-ancestor", AXIS_PLAN_COMMIT, commit], repo)[0] != 0:
        raise RecordFailure(f"期限前の commit {commit[:7]} に、承認した計画（{AXIS_PLAN_COMMIT[:7]}）が含まれていない → 復元しない")
    got = runner(repo, commit, date)
    computed, inner_meta = got["computed"], got["meta"]
    if inner_meta.get("code_commit") != commit or inner_meta.get("code_dirty"):
        raise RecordFailure("取り出した commit と、計算に使ったコードが一致しない → 復元しない")
    at = iso(now)
    date_dir = output_dir / date
    date_dir.mkdir(parents=True, exist_ok=True)
    with file_lock(date_dir / LOCK_NAME):
        path = date_dir / RECORD_NAME
        rec = load_record(path) or new_record(date)
        sched_problem = merge_schedule(rec, computed["schedule"], at)
        if sched_problem:
            raise RecordFailure(f"復元: {sched_problem}")
        if computed["schedule"]["status"] != "確定":
            atomic_write_json(path, rec)
            raise RecordFailure(f"期限前の commit でも予定のレースを確定できない: {computed['schedule']['reason']}")
        meta = {**inner_meta, "restore": {"restored_at": at, "reason": reason.strip(), "input_commit": commit,
                                          "push_timestamp": last["timestamp"],
                                          "activity_type": last.get("activity_type")}}
        problems = merge_computed(rec, computed, "復元", meta, at)
        atomic_write_json(path, rec)
    print(f"{date}: 復元（期限前の push {last['timestamp']}・commit {commit[:7]}）。"
          f"記録 {len(rec['races'])} / 予定 {len(rec['schedule']['races'])}")
    for p in problems:
        print(f"  ⚠️ {p}")
    return 1 if problems else 0


def cmd_compute(date: str, out: str) -> int:
    computed = compute_day(date)
    Path(out).write_text(json.dumps({"computed": computed, "meta": run_meta()}, ensure_ascii=False), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record")
    p.add_argument("date")
    sub.add_parser("check")
    p = sub.add_parser("restore")
    p.add_argument("date")
    p.add_argument("--reason", required=True)
    p = sub.add_parser("compute")
    p.add_argument("date")
    p.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "record":
            return cmd_record(args.date)
        if args.cmd == "check":
            return cmd_check()
        if args.cmd == "restore":
            return cmd_restore(args.date, args.reason)
        return cmd_compute(args.date, args.out)
    except RecordFailure as e:
        print(f"❌ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
