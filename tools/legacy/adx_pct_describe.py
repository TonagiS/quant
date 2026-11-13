# -*- coding: utf-8 -*-
"""ADX(14) 分位描述统计（不看收益，只看分布漂移）。

产物：work/adx_pct_describe.txt
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
sys.path.insert(0, r"E:\Code\quant\work")

from position.engine import load_panel  # noqa: E402
from indicator_sweep import adx  # noqa: E402

OUT = r"E:\Code\quant\work\adx_pct_describe.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


close = load_panel("ext")
level = close.mean(axis=1)
level = level / level.iloc[0]
a = adx(level, 14).dropna()

log(f"=== ADX(14) 分位描述 · ext 52 ETF 等权指数 · {a.index[0].date()}~{a.index[-1].date()} · {len(a)} 个交易日 ===")
log("\n【全样本分位数】")
for q in (1, 5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 75, 80, 85, 90, 95, 99):
    log(f"  P{q:<3d} = {np.percentile(a, q):5.1f}")
log(f"\n  ADX < 20 的交易日占比：{(a < 20).mean():.1%}")
log(f"  ADX < 20 对应的全样本分位：{np.mean(a < 20) * 100:.1f}%（即固定阈值 20 ≈ 全样本 P{np.mean(a < 20) * 100:.0f}）")

log("\n【逐年：中位数 / P25 / 低于 20 的占比】")
for y, g in a.groupby(a.index.year):
    log(f"  {y}: 中位 {g.median():5.1f}  P25 {np.percentile(g, 25):5.1f}  P75 {np.percentile(g, 75):5.1f}  "
        f"<20 占 {np.mean(g < 20):5.1%}  <18 占 {np.mean(g < 18):5.1%}  <22 占 {np.mean(g < 22):5.1%}")

# 滚动 500 日（约 2 年）分位：当日 ADX 在过去 500 日中的位置
pct500 = a.rolling(500, min_periods=250).apply(lambda x: (x <= x[-1]).mean(), raw=True)
log("\n【滚动 500 日分位：固定 20 落在第几分位（逐年中位）】")
pct_of_20 = a.rolling(500, min_periods=250).apply(lambda x: (x <= 20).mean(), raw=True)
for y, g in pct_of_20.dropna().groupby(pct_of_20.dropna().index.year):
    log(f"  {y}: 20 分位位置 中位 {g.median():.0%}（区间 {g.min():.0%}~{g.max():.0%}）")

log("\n【滚动分位口径下，ADX 处于后 25% 的占比（逐年）】")
low = pct500 < 0.25
for y, g in low.dropna().groupby(low.dropna().index.year):
    log(f"  {y}: {g.mean():5.1%}")

corr = pct_of_20.dropna()
log(f"\n【稳定性】固定 20 在滚动 2 年窗口中的分位位置：均值 {corr.mean():.0%}、标准差 {corr.std():.0%}、"
    f"最低 {corr.min():.0%}、最高 {corr.max():.0%}")
log(f"  → 分位位置波动 {corr.max() - corr.min():.0%} 个百分点；若差异大，说明同一绝对阈值在不同年份含义不同。")

open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("\n写出:", OUT)
