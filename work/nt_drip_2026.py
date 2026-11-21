import sys
sys.path.insert(0, r"E:\Code\quant\tools")
import pandas as pd
import numpy as np

d = pd.read_csv(r"E:\Code\quant\data\national_team\etf_shares_hist.csv", parse_dates=["date"])
p = d.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4
tot = p.sum(axis=1)
x = tot.diff()
c3 = (tot / tot.shift(3) - 1) * 100
c5 = (tot / tot.shift(5) - 1) * 100
c14 = (tot / tot.shift(14) - 1) * 100
down10 = x.rolling(10).apply(lambda s: (s < 0).sum())
maxdrop = (x / tot.shift(1) * 100).rolling(14).min()

cond = pd.DataFrame({"c3": c3, "c5": c5, "c14": c14, "down10": down10, "maxdrop": maxdrop})
cand = cond[(cond.c14 <= -4) & (cond.index >= "2026-01-01")]
print(cand.round(2).to_string())

strict = (cand.c14 <= -4) & (cand.c3.abs() <= 1.5) & (cand.c5.abs() <= 2) & (cand.down10 >= 6) & (cand.maxdrop > -1.5)
print("\nstrict drip 2026 days:", int(strict.sum()))
loose = (cand.c14 <= -4) & (cand.c3.abs() <= 1.5) & (cand.c5.abs() <= 2)
print("loose 2026 days:", int(loose.sum()))
