import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import SPLIT, load_universe, stats  # noqa: E402

uni = load_universe()
rows = []
for code, (grp, df) in uni.items():
    key = "ETF" if grp == "ETF" else "长样本"
    df = df[df["date"] >= SPLIT[key]].reset_index(drop=True)
    if len(df) < 500:
        continue
    r = df["close"].pct_change()
    s = stats(r)
    rows.append(dict(code=code, group=key, cagr=s["cagr"], mdd=s["mdd"], calmar=s["calmar"]))
b = pd.DataFrame(rows)
print("满仓基准：")
for g, s in b.groupby("group"):
    print("  %-4s n=%d cagr %.2f%% mdd %.2f%% 卡玛 %.3f" % (
        g, len(s), s.cagr.median() * 100, s.mdd.median() * 100, s.calmar.median()))
print("  全部 n=%d cagr %.2f%% mdd %.2f%% 卡玛 %.3f" % (
    len(b), b.cagr.median() * 100, b.mdd.median() * 100, b.calmar.median()))
b.to_csv(r"E:\Code\quant\runs\2026-11-15_single_asset_state\buyhold_summary.csv", index=False)
