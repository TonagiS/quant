import pandas as pd
import numpy as np

d = pd.read_csv(r"E:\Code\quant\data\national_team\etf_shares_hist.csv", parse_dates=["date"])
p = d.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4
tot = p.sum(axis=1)

# slope of log shares last 10d
y = np.log(tot.iloc[-10:].values)
t = np.arange(len(y))
coef = np.polyfit(t, y, 1)
beta = coef[0]
resid = y - np.polyval(coef, t)
se = np.sqrt((resid ** 2).sum() / (len(y) - 2) / ((t - t.mean()) ** 2).sum())
print("slope10 %+.4f/day t=%+.2f -> %+.2f%%/day" % (beta, beta / se, (np.exp(beta) - 1) * 100))

# daily z distribution
x = tot.pct_change() * 100
sd = x.rolling(60).std()
z = (x / sd).dropna()
print("daily x sd(60) now", round(sd.iloc[-1], 2), "last z", z.iloc[-5:].round(2).tolist())

# CUSUM downside k=0.5 h=5, on z
s, start, alarms = 0.0, None, []
for dt, zz in z.items():
    prev = s
    s = max(0.0, s - (zz + 0.5))
    if s == 0:
        start = dt
    if prev < 5 <= s:
        alarms.append((str(dt)[:10], str(start)[:10]))
print("cusum now %.2f since %s" % (s, str(start)[:10]), "alarms", alarms[-4:])

# quiet-short + 14d bleed forward returns on 510300
c3 = (tot / tot.shift(3) - 1) * 100
c5 = (tot / tot.shift(5) - 1) * 100
c14 = (tot / tot.shift(14) - 1) * 100
px = pd.read_csv(r"E:\Code\quant\data\cache2019\sh510300.csv", encoding="utf-8-sig")
print(px.columns.tolist())
px["date"] = pd.to_datetime(px["date"])
px = px.set_index("date").sort_index()
close = px["close"] if "close" in px.columns else px.iloc[:, 1]
base = pd.DataFrame({"c3": c3, "c5": c5, "c14": c14}).dropna()
quiet = base[(base.c3.abs() <= 1.0) & (base.c5.abs() <= 1.5) & (base.c14 <= -4)]
fwd20 = (close.shift(-20) / close - 1) * 100
fwd10 = (close.shift(-10) / close - 1) * 100
sel = fwd20.reindex(quiet.index).dropna()
print("quiet bleed days:", len(quiet), "with fwd:", len(sel), "median fwd20 %.2f%%" % sel.median())
print("baseline median fwd20 %.2f%%" % fwd20.dropna().median())
sel10 = fwd10.reindex(quiet.index).dropna()
print("median fwd10 %.2f%% vs base %.2f%%" % (sel10.median(), fwd10.dropna().median()))
