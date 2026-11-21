# -*- coding: utf-8 -*-
import sys

import numpy as np

sys.path.insert(0, r"E:\Code\quant\tools")
import single_asset_daily as s  # noqa: E402

cl = s.load_codes([c for _, c in s.WATCH], (s.DATA, s.DATA_FULL)).loc[:"2026-11-13"]
P = s.parts(cl)
i = len(cl) - 1
ret = cl.pct_change(fill_method=None)
vol60 = ret.rolling(60, min_periods=42).std() * np.sqrt(252)
for lab, c in s.WATCH:
    print("%-8s pos=%.2f G1=%.2f volcap=%.2f vol60=%.1f%% T=T%d taper=%.2f"
          % (lab, P["pos"][c].iloc[i], P["g1"][c].iloc[i], P["volcap"][c].iloc[i],
             vol60[c].iloc[i] * 100, int(P["T"][c].iloc[i]), P["taper"][c].iloc[i]))
