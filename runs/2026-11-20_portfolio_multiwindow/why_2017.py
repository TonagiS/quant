# -*- coding: utf-8 -*-
"""为什么宽 ETF 池 2017 起，无策略(等权)回撤 -27.9% 比 VT15 的 -28.6% 还小。"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = r"E:\Code\quant"
sys.path.insert(0, ROOT)
from position.engine import Config, Fixed, VolTarget, features, run_with_base  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "runs", "2026-11-20_portfolio_multiwindow"))
from bt import clean, load_etf_panel  # noqa: E402

COST = 0.0005
close, _ = clean(load_etf_panel())
f = features(close, min_names=8)
dates = close.index

common = dict(k=5, weight_mode="invvol", buy_cost=COST, sell_cost=COST, min_names=8)
arms = {
    "S2": run_with_base(close, f, Config(reb=10, reb_mode="rolling", rule=Fixed(1.0), **common)),
    "VT15": run_with_base(close, f, Config(reb=10, reb_mode="rolling", rule=VolTarget(0.15, 60, every=1), **common)),
}
bh = np.nan_to_num(f["ret"].mean(axis=1).fillna(0.0).to_numpy())

lo = int(dates.searchsorted(pd.Timestamp("2017-01-01")))
seg = slice(lo, len(dates))
d = dates[lo:]


def curve(r):
    return np.cumprod(1.0 + r)


def ddinfo(r, label):
    eq = curve(r)
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    k = int(np.argmin(dd))
    p = int(np.argmax(eq[: k + 1]))
    up = eq / eq[0]
    print("%-6s 期末 %.2fx  年化 %.1f%%  MDD %.1f%%  峰 %s  谷 %s  峰→谷 %.2f"
          % (label, up[-1], (up[-1] ** (252.0 / len(r)) - 1) * 100, dd[k] * 100,
             d[p].date(), d[k].date(), dd[k]))
    return eq, p, k


print("=== 2017-01 起，宽 ETF 池 92 只 ===")
e_bh, p_bh, k_bh = ddinfo(bh[seg], "等权BH")
for name, res in arms.items():
    eq, p, k = ddinfo(res["ret"][seg], name)
    if name == "VT15":
        ex = res["expo"][lo:]
        print("   敞口：峰日 %.0f%%  峰→谷均值 %.0f%%  谷日 %.0f%%  全期均值 %.0f%%"
              % (ex[p] * 100, ex[p:k + 1].mean() * 100, ex[k] * 100, ex.mean() * 100))
        for ts in ("2021-02-18", "2021-12-31", "2022-04-26", "2022-10-31", "2024-02-02", "2025-12-31"):
            i = int(d.searchsorted(pd.Timestamp(ts)))
            if i < len(d):
                print("     %s 敞口 %.0f%%  净值 %.3f  等权净值 %.3f"
                      % (ts, ex[i] * 100, eq[i], e_bh[i]))

# 分段：哪一段把差拉开了
print("\n=== 分段年化（2017-01 起）===")
segs = [("2017-01-01", "2021-02-18"), ("2021-02-18", "2024-02-02"), ("2024-02-02", "2026-11-13")]
for a, b in segs:
    i0 = int(d.searchsorted(pd.Timestamp(a)))
    i1 = int(d.searchsorted(pd.Timestamp(b)))
    if i1 <= i0:
        continue
    row = [a, b, round((i1 - i0) / 252.0, 2)]
    for name, r in (("等权BH", bh[seg]), ("S2", arms["S2"]["ret"][seg]), ("VT15", arms["VT15"]["ret"][seg])):
        rr = r[i0:i1]
        row.append("%s %.1f%% (DD %.0f%%)" % (name, ((np.prod(1 + rr)) ** (252.0 / len(rr)) - 1) * 100,
                                              (np.min(np.cumprod(1 + rr) / np.maximum.accumulate(np.cumprod(1 + rr))) - 1) * 100))
    print(" | ".join(map(str, row)))

# 峰日 VT15 拿了什么
print("\n=== 峰日持仓 / 那几天谁在跌 ===")
p = p_bh
print("等权峰日", d[p].date())
for name, res in arms.items():
    pass
