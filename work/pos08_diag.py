# -*- coding: utf-8 -*-
"""POS-08 补充诊断：分位 vs 固定20 的逐年差异，以及 q≤35% 子集的最差卡玛。

产物：runs/2026-11-04_POS-08_adx_pct/diag.txt
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
sys.path.insert(0, r"E:\Code\quant\work")

from position.engine import Config, Rule, VolTarget, features, load_panel, perf, simulate  # noqa: E402
from indicator_sweep import adx  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\runs\2026-11-04_POS-08_adx_pct\diag.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


class Fix(Rule):
    def __init__(self, e):
        self.e = e.to_numpy()

    def reset(self):
        pass

    def scale(self, j, ctx):
        return float(self.e[min(j, len(self.e) - 1)])


close = load_panel("ext")
f = features(close)
idx = close.index
lo = f["start"]
level = close.mean(axis=1)
level = level / level.iloc[0]
A = adx(level, 14)

vt = pd.Series(simulate(close, f, Config(reb=5, k=5, buy_cost=BUY, sell_cost=BUY,
                                         rule=VolTarget(target=0.15, window=60, every=5)))["scale"], index=idx)
pct = A.rolling(500, min_periods=250).apply(lambda x: float(np.mean(x <= x[-1])), raw=True)


def year_calmar(e, tag):
    r = simulate(close, f, Config(reb=5, k=5, buy_cost=BUY, sell_cost=BUY, rule=Fix(e)))
    out = {}
    for y in range(idx[lo].year, idx[-1].year + 1):
        m = (idx >= pd.Timestamp(f"{y}-01-01")) & (idx <= pd.Timestamp(f"{y}-12-31"))
        rr = r["ret"][m]
        out[y] = perf(rr)
    return out


def expo(cond, mult=0.5):
    return (vt * pd.Series(np.where(cond, mult, 1.0), index=idx)).clip(0, 1)


variants = {
    "VT15基线": vt,
    "固定20": expo(A < 20),
    "分位30/500d": expo(pct < 0.30),
    "分位20/500d": expo(pct < 0.20),
    "分位25/500d": expo(pct < 0.25),
}
res = {k: year_calmar(e, k) for k, e in variants.items()}
years = sorted(res["VT15基线"].keys())

log("=== 逐年卡玛 ===")
log(f"{'年份':<8}" + "".join(f"{k:>14}" for k in variants))
for y in years:
    log(f"{y:<8}" + "".join(f"{res[k][y]['calmar']:>14.2f}" for k in variants))
log("\n=== 逐年收益 ===")
log(f"{'年份':<8}" + "".join(f"{k:>14}" for k in variants))
for y in years:
    log(f"{y:<8}" + "".join(f"{res[k][y]['cagr']:>14.2%}" for k in variants))

# 分位 vs 固定 在"固定20 分位位置偏离 30% 最多"的年份表现
log("\n=== 说明 ===")
log("固定20 的触发率逐年：2021 偏高（震荡）、2022 偏低（单边涨）；分位口径的差异应主要体现在这两类年份。")

# q≤35 子集
g = pd.read_csv(r"E:\Code\quant\runs\2026-11-04_POS-08_adx_pct\grid.csv")
sub = g[g["q"] <= 0.35]
log(f"\n=== 扰动子集（q≤35%，48 组）卡玛：最低 {sub['calmar'].min():.2f} / 中位 {sub['calmar'].median():.2f} / 最高 {sub['calmar'].max():.2f} ===")
bad = sub.sort_values("calmar").head(3)
for _, r in bad.iterrows():
    log(f"  最差 q={r['q']:.0%} 窗口={r['lookback']} 减半={r['mult']}: 卡玛 {r['calmar']:.2f}")
log(f"全 60 组：最低 {g['calmar'].min():.2f}（q=40%/250d/减半0.4 触发过频）")

open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("\n写出:", OUT)
