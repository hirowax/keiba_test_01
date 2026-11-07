"""
3項目（各データ上位3頭・データ分析ピックアップ・出走馬分析）の読み方の確認（docs/data_sources.md の要確認2）。2026-11-07。
成績・着順は見ない。ページは取りに行かない（タスク8の試しで保存した research/rebuild_trial_202611/pages/race/ を読む）。

2026-11-07 外部レビューを反映して書き直した:
- 出馬表の行と照合できなかった項目は数えない（照合済みの項目だけで数え、照合できなかった項目は理由とともに別に出す）。
- 短い名前は、同じ先頭の文字を持つ別の馬がそのレースにいないことも確かめる（いれば照合できないとする）。
- 騎手の項目は、出馬表の騎手名が略されている（例「小沢」）ため、名前では照合済みにしない。馬番と枠が合うことだけを記録し、
  「騎手名は未照合」とする（騎手IDはページの上位3頭の欄に無い）。
- ピックアップはページに明示された馬番と馬IDで照合する。
- 出走馬分析は、表の中の見出しで種類を決める。数える種類は下の HORSE_TITLES に明示する（6種類。保存した5レースで見た見出し）。
  知らない見出し・「推奨なし」・欄がない・読めない形は分けて記録する。同じ表に同じ馬番が2回あれば記録する。
- 今のコードが騎手の表を馬の表として扱っていることと、その騎手名が実際に馬に対応して加点されたかを分ける
  （後者は、今のコードの名前の対応付け（name_to_num_fuzzy と同じ処理）に騎手名を通して確かめる）。

やること:
1. ページの要素を明示して読む（ここでの読み方）。
   - 出馬表の行: td.Umaban* の馬番、td.Waku* の枠番、td.HorseInfo の馬名・馬ID、td.Jockey の騎手（略称）。
   - 各データ上位3頭: section.top3data の table.AnaBestTable ごとに、表の中の見出しと、div.Kyaku_Type_box の
     span.Kyaku_Type_Num（数字）・WakuBanN のクラス（枠の色）・span.UmaName（短い名前）。
   - ピックアップ: .DataPickupHorseWrap の dl ごとに、span.Umaban_Num・WakuN のクラス・a.data_top_horse_link（馬IDと馬名）。
   - 出走馬分析: table.PickupHorseTable* ごとに、表の中の見出しと、div.Umaban_InBox の span.Umaban_Num・WakuN・短い名前。
2. 数字が馬番かを出馬表の行と照らす。
3. 今の本番のコード（race_pickup.scrape_shutuba / scrape_data_top）に保存したページを渡した結果と比べる。
4. 保存値（pickup_scores.json）と並べる（ページは後で取ったので参考）。

出力: このフォルダの result_20261107.json と .txt
実行: python3 research/reading_check_3items_20261107/check_reading.py
"""
import ast
import gzip
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bs4 import BeautifulSoup  # noqa: E402
import race_pickup  # noqa: E402

HERE = Path(__file__).resolve().parent
TRIAL = ROOT / "research" / "rebuild_trial_202611"
PAGES = TRIAL / "pages" / "race"
# 今のコードが出走馬分析として数える見出しの言葉（race_pickup.scrape_data_top と同じ）
CODE_KW = ["得意な馬", "実績がある馬", "調教評価", "クッション", "レース間隔"]
# ここで馬の表として数える見出し（表の中の見出し。保存した5レースで見た6種類）
HORSE_TITLES = ["このコースが得意な馬", "この距離が得意な馬", "この競馬場が得意な馬", "今回の馬場状態が得意な馬",
                "今回のレース間隔で実績がある馬", "今回の調教評価で実績がある馬"]
# 馬の表ではないと分かっている見出し
OTHER_TITLES = ["このコースで有利な枠順", "このコースで有利な脚質", "このコースが得意な騎手", "このコースが得意な調教師",
                "このコースに実績がある種牡馬", "このコースに実績がある母父", "今回のオッズで期待できる騎手",
                "今回の調教師と相性の良い騎手", "今回の馬主と相性の良い騎手"]


class SavedPage:
    def __init__(self, html):
        self.html = html

    def goto(self, *a, **k):
        return None

    def content(self):
        return self.html


def gz(p):
    return gzip.open(p, "rt", encoding="utf-8").read()


def norm(s):
    return race_pickup._norm_name(s)


def cls_num(tag, prefix):
    for c in tag.get("class") or []:
        m = re.fullmatch(prefix + r"(\d+)", c)
        if m:
            return m.group(1)
    return None


def shutuba_rows(soup):
    rows = {}
    for tr in soup.find_all("tr"):
        info = tr.find("td", class_="HorseInfo")
        if not info:
            continue
        u = tr.find("td", class_=re.compile(r"^Umaban"))
        w = tr.find("td", class_=re.compile(r"^Waku"))
        a = info.find("a", href=re.compile(r"/horse/\d+"))
        j = tr.find("td", class_="Jockey")
        ja = j.find("a", href=re.compile(r"/jockey/")) if j else None
        rows[u.get_text(strip=True)] = {
            "waku": w.get_text(strip=True) if w else None,
            "name": norm(a.get_text(strip=True)) if a else None,
            "horse_id": re.search(r"/horse/(\d+)", a["href"]).group(1) if a else None,
            "jockey": norm(j.get_text(strip=True)) if j else None,
            "jockey_id": re.search(r"/(\d{5})/", ja["href"]).group(1) if ja and re.search(r"/(\d{5})/", ja["href"]) else None,
        }
    return rows


def check_entry(rows, num, waku, short, kind, horse_id=None):
    """
    出馬表の行と照らす。(状態, 理由) を返す。
    状態: "照合済み" / "照合できない" / "騎手名は未照合"（馬番と枠は合う。騎手名は略称なので照合済みにしない）
    """
    r = rows.get(num)
    if r is None:
        return "照合できない", ["その馬番の行が無い"]
    bad = []
    if waku != r["waku"]:
        bad.append(f"枠が違う（ページ {waku}・行 {r['waku']}）")
    if horse_id is not None and horse_id != r["horse_id"]:
        bad.append(f"馬IDが違う（ページ {horse_id}・行 {r['horse_id']}）")
    if kind == "jockey":
        if bad:
            return "照合できない", bad
        return "騎手名は未照合", [f"馬番・枠は合う。騎手名 ページ {short}・行 {r['jockey']}（騎手ID {r['jockey_id']}）"]
    s = norm(short or "")
    if not s or not r["name"] or not r["name"].startswith(s):
        bad.append(f"名前が先頭と合わない（ページ {short}・行 {r['name']}）")
    elif horse_id is None:
        others = [n for n, x in rows.items() if n != num and x["name"] and x["name"].startswith(s)]
        if others:
            bad.append(f"同じ先頭の名前の別の馬がいる（{s}: 馬番 {others}）")
    return ("照合できない", bad) if bad else ("照合済み", [])


def read_top3(soup, rows):
    sec = soup.find(class_="top3data")
    if not sec:
        return None
    out = []
    for tb in sec.find_all("table", class_="AnaBestTable"):
        t = tb.find(class_="PickupHorseTableTitle")
        title = t.get_text(strip=True) if t else None
        kind = "jockey" if title == "騎手" else "horse"
        ents = []
        for box in tb.find_all("div", class_="Kyaku_Type_box"):
            sp = box.find("span", class_="Kyaku_Type_Num")
            un = box.find("span", class_="UmaName")
            if not sp or not un:
                ents.append({"state": "照合できない", "why": ["読めない形"], "html": str(box)[:200]})
                continue
            num, waku, short = sp.get_text(strip=True), cls_num(sp, "WakuBan"), un.get_text(strip=True)
            st, why = check_entry(rows, num, waku, short, kind)
            ents.append({"num": num, "waku": waku, "short": short, "state": st, "why": why})
        dup = [n for n, k in Counter(e.get("num") for e in ents).items() if n and k > 1]
        out.append({"title": title, "kind": kind, "entries": ents, "dup_in_table": dup})
    return out


def read_pickup(soup, rows):
    wrap = soup.find(class_="DataPickupHorseWrap")
    if not wrap:
        return None
    out = []
    for dl in wrap.find_all("dl"):
        sp = dl.find("span", class_="Umaban_Num")
        a = dl.find("a", class_="data_top_horse_link")
        m = re.search(r"/horse/(\d+)", a["href"]) if a else None
        if not sp or not m:
            out.append({"state": "照合できない", "why": ["読めない形"], "html": str(dl)[:200]})
            continue
        num, waku = sp.get_text(strip=True), cls_num(sp, "Waku")
        st, why = check_entry(rows, num, waku, a.get_text(strip=True), "horse", m.group(1))
        out.append({"num": num, "waku": waku, "name": a.get_text(strip=True), "horse_id": m.group(1), "state": st, "why": why})
    return out


def code_name_to_num(horse_map):
    """race_pickup.scrape_data_top の名前の対応付け（name_to_num_fuzzy）と同じ処理"""
    name_to_num = {}
    for num, name in horse_map.items():
        name_to_num[name] = num
        if len(name) >= 4:
            name_to_num[name[:4]] = num

    def f(short_name):
        sn = norm(short_name)
        if sn in name_to_num:
            return name_to_num[sn]
        for full, num in name_to_num.items():
            if sn in full or full in sn:
                return num
        return ""
    return f


def read_analysis(soup, rows, code_horse_map):
    fuzzy = code_name_to_num(code_horse_map)
    out = []
    for tb in soup.find_all("table", class_=lambda c: c and "PickupHorseTable" in str(c)):
        own = tb.find(class_="PickupHorseTableTitle")
        prev = tb.find_previous(class_="PickupHorseTableTitle")
        title = own.get_text(strip=True) if own else None
        title_code = prev.get_text(strip=True) if prev else None
        if title in HORSE_TITLES:
            kind = "馬の表"
        elif title in OTHER_TITLES:
            kind = "馬ではない表"
        else:
            kind = "知らない見出し"
        txt = [s.get_text(strip=True) for s in tb.select("span.Txt")]
        boxes = [b for b in tb.find_all("div", class_="Umaban_InBox")]
        ents = []
        for box in boxes:
            sp = box.find("span", class_="Umaban_Num")
            if sp is None:
                ents.append({"state": "照合できない", "why": ["読めない形"], "html": str(box)[:200]})
                continue
            if sp.find(class_="WakuTxt"):
                continue  # 枠順の表（数字の後に「枠」）。馬ではない
            num = sp.get_text(strip=True)
            short = box.get_text(strip=True)[len(num):]
            st, why = check_entry(rows, num, cls_num(sp, "Waku"), short, "horse")
            ents.append({"num": num, "waku": cls_num(sp, "Waku"), "short": short, "state": st, "why": why})
        if kind == "馬の表":
            if ents:
                content = "馬あり"
            elif txt == ["推奨なし"]:
                content = "推奨なし"
            else:
                content = "読めない形"
        else:
            content = "対象外"
        # 今のコードがこの表を数えるか・数えたときに、騎手名などの文字が馬に対応したか
        code_counts = any(k in (title_code or "") for k in CODE_KW)
        code_text_match = None
        if code_counts and not ents and txt and txt != ["推奨なし"]:
            code_text_match = {t: (fuzzy(t) or None) for t in txt}
        dup = [n for n, k in Counter(e.get("num") for e in ents).items() if n and k > 1]
        out.append({"own_title": title, "kind": kind, "content": content, "title_code_uses": title_code,
                    "code_counts_this_table": code_counts, "code_text_to_num": code_text_match,
                    "texts": [td.get_text("/", strip=True) for td in tb.find_all("td")],
                    "entries": ents, "dup_in_table": dup})
    return out


def srt(d):
    return dict(sorted(d.items(), key=lambda x: int(x[0])))


def main():
    races = json.load(open(TRIAL / "selected_races.json", encoding="utf-8"))
    res, lines = {}, []
    totals = Counter()
    for race in races:
        rid, d, lab = race["race_id"], race["date"], race["label"]
        sh_html = gz(PAGES / f"{rid}_shutuba.html.gz")
        dt_html = gz(PAGES / f"{rid}_data_top.html.gz")
        sh, dt = BeautifulSoup(sh_html, "html.parser"), BeautifulSoup(dt_html, "html.parser")
        rows = shutuba_rows(sh)
        code_sh = race_pickup.scrape_shutuba(SavedPage(sh_html), rid)
        code_dt = race_pickup.scrape_data_top(SavedPage(dt_html), rid, code_sh["horse_map"])
        top3 = read_top3(sh, rows)
        pickup = read_pickup(dt, rows)
        analysis = read_analysis(dt, rows, code_sh["horse_map"])

        all_ents = [("上位3頭", t["title"], e) for t in (top3 or []) for e in t["entries"]] + \
            [("ピックアップ", None, e) for e in (pickup or [])] + \
            [("出走馬分析", t["own_title"], e) for t in analysis for e in t["entries"]]
        st_count = Counter(e["state"] for _, _, e in all_ents)
        totals.update(f"{k}・{e['state']}" for k, _, e in all_ents)
        # ここでの読み方で数える（照合済みと、騎手名は未照合（馬番・枠は合う）の項目。照合できない項目は数えない）
        ok = ("照合済み", "騎手名は未照合")
        top3_count = Counter(e["num"] for t in (top3 or []) for e in t["entries"] if e["state"] in ok)
        top3_count_strict = Counter(e["num"] for t in (top3 or []) for e in t["entries"] if e["state"] == "照合済み")
        pickup_set = sorted({e["num"] for e in (pickup or []) if e["state"] == "照合済み"}, key=int)
        horse_tables = [t for t in analysis if t["kind"] == "馬の表"]
        analysis_count = Counter(e["num"] for t in horse_tables for e in t["entries"] if e["state"] == "照合済み")
        pk = json.load(open(ROOT / "output" / d / "pickup_scores.json", encoding="utf-8"))["races"].get(lab, {})
        pn = pk.get("pickup_nums")
        pn = sorted(ast.literal_eval(pn), key=int) if isinstance(pn, str) and pn != "set()" else ([] if pn in (None, "set()") else sorted(pn, key=int))
        shift = [t for t in analysis if t["own_title"] != t["title_code_uses"]]
        jockey_tables_counted = [t for t in analysis if t["code_counts_this_table"] and t["kind"] == "馬ではない表"]
        jockey_hits = {k: v for t in jockey_tables_counted for k, v in (t["code_text_to_num"] or {}).items() if v}
        res[f"{d} {lab}"] = {
            "date": d, "label": lab, "race_id": rid, "states": dict(st_count),
            "top3": top3, "pickup": pickup, "analysis": analysis,
            "top3_this_reading": srt(top3_count), "top3_this_reading_strict": srt(top3_count_strict),
            "top3_code": code_sh["top3_hits"], "top3_saved": pk.get("top3_hits"),
            "pickup_this_reading": pickup_set, "pickup_code": sorted(code_dt["pickup_nums"], key=int), "pickup_saved": pn,
            "analysis_this_reading": srt(analysis_count), "analysis_code": code_dt["analysis_hits"],
            "analysis_saved": pk.get("analysis_hits"),
            "analysis_tables": len(analysis), "analysis_tables_title_differs": len(shift),
            "code_counts_non_horse_tables": [t["own_title"] for t in jockey_tables_counted],
            "code_non_horse_text_matched_horse": jockey_hits,
            "horse_table_content": dict(Counter(t["content"] for t in horse_tables)),
            "unknown_titles": [t["own_title"] for t in analysis if t["kind"] == "知らない見出し"],
            "dup_in_table": {"top3": [t["dup_in_table"] for t in (top3 or []) if t["dup_in_table"]],
                             "analysis": [t["dup_in_table"] for t in analysis if t["dup_in_table"]]},
        }
        r = res[f"{d} {lab}"]
        lines.append(f"## {d} {lab} {rid}")
        lines.append(f"  照らした項目: {dict(st_count)}")
        for k, title, e in all_ents:
            if e["state"] != "照合済み":
                lines.append(f"    {k}（{title}） {e['state']}: 馬番 {e.get('num')} {e.get('short') or e.get('name')} {e['why']}")
        lines.append(f"  上位3頭  ここでの読み方 {r['top3_this_reading']}（騎手名は未照合の項目を除くと {r['top3_this_reading_strict']}）")
        lines.append(f"           今のコード     {srt(code_sh['top3_hits'])}")
        lines.append(f"           保存値         {pk.get('top3_hits')}")
        lines.append(f"  ピックアップ ここでの読み方 {pickup_set}・今のコード {r['pickup_code']}・保存値 {pn}"
                     + ("（欄がない）" if pickup is None else ""))
        lines.append(f"  出走馬分析 表 {len(analysis)}・今のコードが別の表の見出しで判定している表 {len(shift)}"
                     + ("（欄がない）" if not analysis else ""))
        lines.append(f"    馬の表の中身: {r['horse_table_content']}・知らない見出し: {r['unknown_titles']}・同じ表に同じ馬番: {r['dup_in_table']}")
        lines.append(f"    今のコードが馬の表として扱った馬ではない表: {r['code_counts_non_horse_tables']}"
                     f"・その文字が馬番に対応した: {jockey_hits or 'なし'}")
        lines.append(f"    ここでの読み方（表の中の見出しで、馬の表の照合済みの馬を全頭） {r['analysis_this_reading']}")
        lines.append(f"    今のコード     {srt(code_dt['analysis_hits'])}")
        lines.append(f"    保存値         {pk.get('analysis_hits')}")
    lines.append("## 合計（種類・状態ごとの項目数）")
    for k, v in sorted(totals.items()):
        lines.append(f"  {k}: {v}")
    (HERE / "result_20261107.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    (HERE / "result_20261107.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
