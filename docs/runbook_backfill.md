# 過去開催のスクレイプ（未取得週末の遡及）

CLAUDE.md から移動（2026-10-30）。「過去開催の1週末分スクレイプして」と言われたらこの手順に従う。


「過去開催の1週末分スクレイプして」と言われたら以下の手順で実行する。

> **重要**: JRA開催は土日2日とは限らない。祝日がある週は土日月の3日開催、日月のみ、日のみ等がある。
> **必ずJRAカレンダーを参照して開催日を確認してから実行すること。**

#### ① 未取得週末を特定し、開催日を確認する

カレンダーファイル（`jra_calendar_YYYY.json`）が対象年に存在すれば使う。なければ netkeiba で直接確認。

```python
import json, os, glob
from pathlib import Path

# 最古の未取得データを確認
have = sorted(p.parent.name for p in Path('output').glob('*/pickup_scores.json') if p.parent.name.isdigit())
print('最古pickup:', have[0] if have else 'なし')

# 全カレンダーファイルを検索し、未取得日を列挙
for cal_file in sorted(glob.glob('jra_calendar_????.json')):
    with open(cal_file) as f:
        all_dates = sorted(json.load(f)['dates'])
    missing = [d for d in all_dates if not os.path.exists(f'output/{d}/pickup_scores.json')]
    if missing:
        print(f'{cal_file}: 未取得 {len(missing)}日 → 先頭: {missing[:5]}')
```

**カレンダーファイルがない年の開催日確認**（候補の土〜月を直接問い合わせ）:

```python
from playwright.sync_api import sync_playwright
from scraper import load_cookies, get_race_ids, load_env
load_env()
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(); load_cookies(ctx)
    page = ctx.new_page()
    for d in ['YYYYMMDD', 'YYYYMMDD', 'YYYYMMDD']:  # 候補の土・日・月を列挙
        races = get_race_ids(page, d)
        print(d, len(races), '件')
    browser.close()
```

レース数 > 0 の日が開催日。それらが「1週末」のセット。

#### ② 開催日ごとに順番にスクレイプ（1日ずつ）

```bash
AUTO_MODE=1 bash run.sh YYYYMMDD  # 1日目（完了を待ってから2日目へ）
AUTO_MODE=1 bash run.sh YYYYMMDD  # 2日目
AUTO_MODE=1 bash run.sh YYYYMMDD  # 3日目（3日開催の場合のみ）
```

- **平日実行でも OK**（`AUTO_MODE=1` が警告をスキップ）
- 過去レース（約6ヶ月以上前）は `type=rank` がサブスク誘導ページを返す仕様だが、
  `shutuba fallback` が自動で使われるため正常動作する（scraper.py 修正済み 2026-05-21）
- run.sh は 1 日ずつ順番に実行する（同時実行不可）

#### ③ レース結果をスクレイプしてpush

```bash
python3 scrape_results.py YYYYMMDD  # 開催日ごとに実行
# 全日完了後まとめてpush
git add output/ && git commit -m "results: YYYYMMDD YYYYMMDD ..." && git push
```
