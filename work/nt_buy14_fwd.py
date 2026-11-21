import sys
sys.path.insert(0, r"E:\Code\quant\tools")
import nt_signal_shadow as m
import pandas as pd
import numpy as np

dates, cum = m.load_shares()
d = pd.DataFrame({"date": pd.to_datetime(dates), "cum14": cum}).dropna().reset_index(drop=True)

px = pd.read_csv(r"E:\Code\quant\data\cache2019\sh510300.csv", encoding="utf-8-sig")
px["date"] = pd.to_datetime(px["date"])
px = px.set_index("date").sort_index()["close"]

def fwd(days):
    out = {}
    pos = {dt: i for i, dt in enumerate(px.index)}
    for dt in d.date:
        i = pos.get(dt)
        if i is not None and i + days < len(px):
            out[dt] = px.iloc[i + days] / px.iloc[i] - 1
    return out

for k in [5, 10, 20]:
    d["f%d" % k] = d.date.map(fwd(k))

print("baseline n=%d" % d.f20.notna().sum())
for k in [5, 10, 20]:
    v = d["f%d" % k].dropna()
    print("  f%d mean %+.2f%% median %+.2f%% win %.0f%%"
          % (k, v.mean() * 100, v.median() * 100, (v > 0).mean() * 100))

for thr in [15, 20, 25, 30]:
    s = d[d.cum14 >= thr]
    print("\ncum14>=%d n=%d" % (thr, len(s)))
    for k in [5, 10, 20]:
        v = s["f%d" % k].dropna()
        print("  f%d mean %+.2f%% median %+.2f%% win %.0f%% n=%d"
              % (k, v.mean() * 100, v.median() * 100, (v > 0).mean() * 100, len(v)))

s = d[d.cum14 >= 20]
gap = s.date.diff().dt.days.fillna(999)
eps = s[gap > 20]
print("\nepisodes(>=20, gap>20d):", len(eps))
for _, r in eps.iterrows():
    print(" ", r.date.date(), round(r.cum14, 1),
          "f20 %s" % ("%.2f%%" % (r.f20 * 100) if pd.notna(r.f20) else "na"))
print("episode f20 mean %.2f median %.2f win %.0f%%"
      % (eps.f20.mean() * 100, eps.f20.median() * 100, (eps.f20 > 0).mean() * 100))
