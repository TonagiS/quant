import sys
sys.path.insert(0, r"E:\Code\quant\tools")
import nt_signal_shadow as m
import pandas as pd

dates, cum = m.load_shares()
d = pd.DataFrame({"date": pd.to_datetime(dates), "cum14": cum}).dropna()
d = d[d.date >= "2026-01-05"].reset_index(drop=True)
state = ["normal"] * len(d)
for i, v in enumerate(d.cum14):
    if v <= -10:
        lo, hi = i + 5, min(len(d), i + 25)
        for j in range(lo, hi):
            state[j] = "defense"
d["state"] = state

px = pd.read_csv(r"E:\Code\quant\data\cache2019\sh510300.csv", encoding="utf-8-sig")
px["date"] = pd.to_datetime(px["date"])
px = px.set_index("date").sort_index()["close"].reindex(d.date).ffill()
ret = px.pct_change().fillna(0)
pos = (d.state == "normal").astype(int).shift(1).fillna(1)
strat = (1 + ret * pos).prod() - 1
bh = px.iloc[-1] / px.iloc[0] - 1
print("2026 510300 buy&hold %.2f%% | rule %.2f%% | defense %d/%d"
      % (bh * 100, strat * 100, (d.state == "defense").sum(), len(d)))
