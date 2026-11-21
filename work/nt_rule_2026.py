import sys
sys.path.insert(0, r"E:\Code\quant\tools")
import nt_signal_shadow as m

dates, cum = m.load_shares()
import pandas as pd

df = pd.DataFrame({"date": pd.to_datetime(dates), "cum14": cum})
df = df.dropna()
df["signal"] = (df.cum14 <= -10).astype(int)
# episodes of consecutive signal days
sig = df[df.signal == 1]
print("total signal days:", len(sig), "first", sig.date.min().date(), "last", sig.date.max().date())
ep = (df.signal.diff().fillna(0) != 0) & (df.signal == 1)
starts = df[ep].date.tolist()
print("episodes:", len(starts))
for s in starts:
    print("  start", s.date())
print("\n2026 signal days:", len(sig[sig.date.dt.year == 2026]))
print(sig[sig.date.dt.year == 2026].groupby(sig.date.dt.to_period("M")).size())
# defense windows (l=5, k=20)
state = ["正常"] * len(df)
for i, v in enumerate(df.cum14):
    if v <= -10:
        lo = i + 5
        hi = min(len(df), lo + 20)
        for j in range(lo, hi):
            state[j] = "防守"
df["state"] = state
d26 = df[(df.date >= "2026-01-01") & (df.state == "防守")]
print("2026 defense days:", len(d26), d26.date.min().date() if len(d26) else "-", d26.date.max().date() if len(d26) else "-")
print("2026 defense months:", d26.groupby(d26.date.dt.to_period("M")).size().to_dict())
