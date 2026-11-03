"""保存済み output/YYYYMMDD.xlsx を {レース: {mode_key: DataFrame}} に戻す（type=rank の DataFrame と同じ形）"""
import pandas as pd
MODE_KEYS = {"近走平均": "average", "当該距離": "distance", "当該コース": "course"}

def load_day(path):
    races = {}
    for sh, raw in pd.read_excel(path, sheet_name=None, header=None, dtype=str).items():
        raw = raw.fillna("")
        i = 0
        while i < len(raw):
            c0 = raw.iat[i, 0].strip()
            if c0.startswith("■"):
                label, mode = c0[1:].split()
                hdr = [str(x) for x in raw.iloc[i + 1]]
                j = i + 2; rows = []
                while j < len(raw) and raw.iat[j, 0].strip() != "" and not raw.iat[j, 0].startswith("■"):
                    rows.append(list(raw.iloc[j])); j += 1
                races.setdefault((sh, label), {})[MODE_KEYS[mode]] = pd.DataFrame(rows, columns=hdr)
                i = j
            else:
                i += 1
    return races