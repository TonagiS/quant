import sys
sys.path.insert(0, r"E:\Code\quant\tools")
import nt_signal_shadow as m
import pandas as pd

dates, cum = m.load_shares()
df = pd.DataFrame({"date": pd.to_datetime(dates), "cum14": cum}).dropna()
df = df[df.date >= "2025-10-01"]
df["signal"] = df.cum14 <= -10
print(df[df.signal].head(3).to_string())
print(df[df.signal].tail(3).to_string())
print("\n2026 monthly min cum14:")
print(df[df.date.dt.year == 2026].groupby(df.date.dt.to_period("M")).cum14.agg(["min", "max"]).round(1).to_string())

# defense windows
state = ["正常"] * len(df)
windows = []
for i, v in enumerate(df.cum14):
    if v <= -10:
        lo = i + 5
        hi = min(len(df), lo + 20)
        for j in range(lo, hi):
            state[j] = "防守"
        windows.append((df.date.iloc[i].date(), df.date.iloc[min(lo, len(df) - 1)].date(), df.date.iloc[hi - 1].date() if hi - 1 < len(df) else "?"))
print("\nwindows (signal, from, until):")
for w in windows:
    print(" ", w)
df["state"] = state
d26 = df[(df.date >= "2026-01-01") & (df.state == "防守")]
print("2026 defense days", len(d26), "share", round(len(d26) / len(df[df.date.dt.year == 2026]) * 100, 1))
