# -*- coding: utf-8 -*-
"""今日单支清仓/加仓的因子拆解（复现播报口径）。"""
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant\tools")
sys.path.insert(0, r"E:\Code\dsh-agent")
import single_asset_daily as sa
import group_brief as gb

CODES = ["sh512290", "sz159992", "sh512980", "sh562500", "sh515170",
         "sh512800", "sz159865", "sh510880"]
ds = "2026-11-17"

raw = json.load(open(r"E:\Code\quant\data\daily\20261117.json", encoding="utf-8-sig"))
px = dict(raw["codes"])
got = gb.live_px(CODES)
px.update({k: v for k, v in got.items() if v})

close = sa.load_codes(CODES, (sa.DATA, sa.DATA_FULL))
ts = pd.Timestamp(ds)
if (close.index == ts).any():
    close = close.loc[:ts]
else:
    row = {c: float(px.get(c, close[c].iloc[-1])) for c in close.columns}
    close.loc[ts] = row
    close = close.sort_index().loc[:ts]
P = sa.parts(close)
i = len(close) - 1
nm = sa.names_map()
mom20 = close / close.shift(20) - 1.0
ma200 = close.rolling(200, min_periods=200).mean()
slope = ma200.pct_change(60)
ret = close.pct_change(fill_method=None)
vol60 = ret.rolling(60, min_periods=42).std() * np.sqrt(252)

print("date row:", close.index[i].date(), " prev row:", close.index[i-1].date())
hdr = "%-8s %-10s %7s %7s %8s %9s %7s %7s %6s %6s %6s %6s %6s %6s" % (
    "code", "name", "px", "chg%", "px/MA200", "ma200slope", "mom20", "mom20p", "g1p", "g1n", "vol", "Tp", "Tn", "pos")
print(hdr)
for c in close.columns:
    lc = c[2:]
    p = float(close[c].iloc[i]); pp = float(close[c].iloc[i-1])
    m = float(mom20[c].iloc[i]); mp = float(mom20[c].iloc[i-1])
    ma = float(ma200[c].iloc[i]); sl = float(slope[c].iloc[i])
    print("%-8s %-10s %7.3f %+7.2f %8.3f %9.4f %6.1f%% %6.1f%% %6.2f %6.2f %6.2f %6d %6d %6.2f" % (
        lc, nm.get(c, c)[:10], p, (p/pp-1)*100, p/ma, sl, m*100, mp*100,
        float(P["g1"][c].iloc[i-1]), float(P["g1"][c].iloc[i]),
        float(P["volcap"][c].iloc[i]), int(P["T"][c].iloc[i-1]), int(P["T"][c].iloc[i]),
        float(P["pos"][c].iloc[i])))
