import pandas as pd
import numpy as np

d = pd.read_csv(r"E:\Code\quant\data\national_team\etf_shares_hist.csv", parse_dates=["date"])
p = d.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4
tot = p.sum(axis=1)
x = tot.pct_change() * 100  # daily % change
sd = x.rolling(60).std()
z = x / sd

# windows cumulative %
for w in (3, 5, 7, 10, 14, 20):
    cum = (tot / tot.shift(w) - 1) * 100
    q = cum.rank(pct=True)
    print(f"{w:2d}d cum={cum.iloc[-1]:+7.2f}%  now-pct={cum.iloc[-1]:+7.2f}  pctile(all)={q.iloc[-1]*100:.0f}%  pctile(250d)={cum.iloc[-250:].rank(pct=True).iloc[-1]*100:.0f}%")

# streak
neg = (x < 0).astype(int)
run = 0
for v in neg.iloc[::-1]:
    if v:
        run += 1
    else:
        break
print("current consecutive negative days:", run)

# CUSUM downside, k=0.5 std, h=5
s = 0.0
start = None
alarms = []
smax = []
for i, (dt, zz) in enumerate(z.items()):
    if np.isnan(zz):
        s = 0.0
        start = None
        smax.append(0.0)
        continue
    prev = s
    s = max(0.0, s - (zz + 0.5))
    if s == 0:
        start = dt
    if prev < 5 <= s:
        alarms.append((dt, start))
    smax.append(s)
print("CUSUM now", round(s, 2), "since", str(start)[:10], "last alarms", [(str(a)[:10], str(b)[:10]) for a, b in alarms[-3:]])

# stealth: 14d cum vs top-3 negative daily moves in window
win = tot.diff().iloc[-14:]
cum14 = tot.iloc[-1] - tot.iloc[-15]
big = win.nsmallest(3).sum()
print("14d cum", round(cum14, 2), "top3 down days", round(big, 2), "share", round(big / cum14 * 100, 1), "%")

# history: quiet 3d/5d but 14d bleed
c3 = (tot / tot.shift(3) - 1) * 100
c5 = (tot / tot.shift(5) - 1) * 100
c14 = (tot / tot.shift(14) - 1) * 100
c7 = (tot / tot.shift(7) - 1) * 100
quiet = (c3.abs() <= 1.0) & (c5.abs() <= 1.5) & (c14 <= -4)
print("quiet-short but 14d<=-4% days:", quiet.sum())
print(c14[quiet].tail(10).round(1).to_string())
