import pandas as pd
import numpy as np

d = pd.read_csv(r"E:\Code\quant\data\national_team\etf_shares_hist.csv", parse_dates=["date"])
p = d.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4
tot = p.sum(axis=1)

# recent totals
print(tot.tail(10).round(1).to_string())

# 60d high drawdown
h60 = tot.iloc[-60:]
print("60d high", str(h60.idxmax())[:10], round(h60.max(), 1), "off", round(tot.iloc[-1] - h60.max(), 2), round((tot.iloc[-1] / h60.max() - 1) * 100, 2))

# down-day share last 10
x = tot.diff()
print("down days last10", int((x.iloc[-10:] < 0).sum()), "last5", int((x.iloc[-5:] < 0).sum()))

# windows with z
for w in (3, 5, 7, 10, 14, 20):
    cum = (tot / tot.shift(w) - 1) * 100
    h = cum.iloc[-251:-1]
    print("%2dd  cum %+.2f%%  pctile250 %3.0f%%  z %+.2f" % (w, cum.iloc[-1], h.rank(pct=True).iloc[-1] * 100, (cum.iloc[-1] - h.mean()) / h.std()))

# drip detector history
c3 = (tot / tot.shift(3) - 1) * 100
c5 = (tot / tot.shift(5) - 1) * 100
c14 = (tot / tot.shift(14) - 1) * 100
down10 = x.rolling(10).apply(lambda s: (s < 0).sum())
drip = (c14 <= -4) & (c3.abs() <= 1.5) & (c5.abs() <= 2) & (down10 >= 6)
print("drip days total", int(drip.sum()))
print("last drip dates", [str(t)[:10] for t in drip[drip].index[-6:]])
# example: Feb 2026
print((tot.loc["2026-01-26":"2026-02-20"] / 1e0).round(1).to_string())
