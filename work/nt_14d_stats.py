import sys
sys.path.insert(0, r"E:\Code\quant\tools")
import nt_signal_shadow as m
import pandas as pd
import numpy as np

dates, cum = m.load_shares()
d = pd.DataFrame({"date": pd.to_datetime(dates), "cum14": cum}).dropna().reset_index(drop=True)

# total shares via hist
h = pd.read_csv(r"E:\Code\quant\data\national_team\etf_shares_hist.csv", parse_dates=["date"])
tot = h.pivot(index="date", columns="code", values="shares_wan").sort_index().sum(axis=1) / 1e4
d["tot"] = d.date.map(tot)
d["chg14_100m"] = d.tot - d.tot.shift(14)
d["chg14_pct"] = d.tot / d.tot.shift(14) * 100 - 100
d["chg3_100m"] = d.tot - d.tot.shift(3)
print(d.tail(3)[["date", "cum14", "tot", "chg3_100m", "chg14_100m", "chg14_pct"]].to_string())
print("14d chg percentile of latest:", round((d.chg14_pct.dropna() < d.chg14_pct.iloc[-1]).mean() * 100, 1))
print("top 5 14d pct:", d.chg14_pct.nlargest(5).round(1).to_dict())
print("top 5 14d 亿份:", d.chg14_100m.nlargest(5).round(0).to_dict())
