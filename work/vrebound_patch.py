# -*- coding: utf-8 -*-
"""补丁：急跌 V 反快速回场。基准 = VT15 × 用户减仓规则（30 日线阶梯）。

空仓后满足以下任一条件，立刻回到 50% 仓（仍受 VT15 上限约束）：
  A 反弹幅度 (x / 空仓以来最低 - 1) >= R
  B 收回本轮跌幅的 Q 比例： (x - low) / (ref_high - low) >= Q
  C 收盘站上 5 日均线且连续 2 日不创新低
产物：E:\\Code\\quant\\work\\vrebound_patch.txt
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
sys.path.insert(0, r"E:\Code\quant\work")

from position.engine import Config, Rule, VolTarget, features, perf, simulate  # noqa: E402
from user_dd_rule import exposure_series, long_panel  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\work\vrebound_patch.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


def step_cut(close, ma=30):
    b = close.mean(axis=1)
    b = b / b.iloc[0]
    ms = b.rolling(ma, min_periods=ma).mean()
    ml = b.rolling(60, min_periods=60).mean()
    e = pd.Series(1.0, index=b.index)
    e[b < ml] = 0.0
    e[(b >= ml) & (b < ms)] = 0.5
    return e.fillna(1.0), b


def patch_expo(base, level, R=0.08, Q=0.5, use_ma5=True, ref_win=60):
    """在 base 敞口上打 V 反快速回场补丁。"""
    ma5 = level.rolling(5, min_periods=5).mean()
    out = base.copy()
    low = np.inf
    ref_high = np.nan
    flat_days = 0
    for i in range(len(level)):
        x = float(level.iloc[i])
        if base.iloc[i] <= 0.001:
            if flat_days == 0:
                ref_high = float(level.iloc[max(0, i - ref_win):i + 1].max())
                low = x
            low = min(low, x)
            flat_days += 1
            rebound = x / low - 1 if low > 0 else 0.0
            recover = (x - low) / (ref_high - low) if ref_high > low > 0 else 0.0
            cond_a = rebound >= R
            cond_b = recover >= Q
            cond_c = use_ma5 and np.isfinite(ma5.iloc[i]) and x > float(ma5.iloc[i]) and flat_days >= 2
            if flat_days >= 1 and (cond_a or cond_b or cond_c):
                out.iloc[i] = 0.5
        else:
            flat_days = 0
            low = np.inf
    return out


class Fix(Rule):
    def __init__(self, e, name):
        self.e = e.to_numpy()
        self.name = name

    def reset(self):
        pass

    def scale(self, j, ctx):
        return float(self.e[min(j, len(self.e) - 1)])


close = long_panel()
f = features(close)
idx = close.index
lo = f["start"]
mask = idx >= pd.Timestamp("2014-01-01")

r = simulate(close, f, Config(reb=10, k=5, buy_cost=BUY, sell_cost=BUY,
                              rule=VolTarget(target=0.15, window=60, every=10)))
vt = pd.Series(r["scale"], index=idx)
cut, level = step_cut(close, 30)
base = (vt * cut).clip(0, 1)

variants = {
    "VT15": vt,
    "VT15+减仓(基准)": base,
    "补丁 R8%": (vt * patch_expo(base, level, R=0.08, use_ma5=False)).clip(0, 1),
    "补丁 R5%": (vt * patch_expo(base, level, R=0.05, use_ma5=False)).clip(0, 1),
    "补丁 回收50%": (vt * patch_expo(base, level, R=0.99, Q=0.5, use_ma5=False)).clip(0, 1),
    "补丁 MA5+不新低": (vt * patch_expo(base, level, R=0.99, Q=0.99, use_ma5=True)).clip(0, 1),
}

log("=== V 反快速回场补丁（2014-2026，指数池，5bp）===")
log(f"{'配置':<18}{'年化':>9}{'回撤':>10}{'卡玛':>7}{'仓位':>6} | 2019  2020  2024")
rets = {}
for tag, e in variants.items():
    rule = Fix(e, tag)
    res = simulate(close, f, Config(reb=10, k=5, buy_cost=BUY, sell_cost=BUY, rule=rule))
    m = perf(res["ret"][mask])
    y = {}
    s = pd.Series(res["ret"], index=idx)
    for yy in (2019, 2020, 2024):
        msk = idx.year == yy
        y[yy] = (1 + s[msk]).prod() - 1
    rets[tag] = s
    log(f"  {tag:<18}{m['cagr']:>8.2%}{m['mdd']:>10.2%}{m['calmar']:>7.2f}"
        f"{np.mean(res['scale'][mask]):>6.0%} | {y[2019]:>+6.1%} {y[2020]:>+6.1%} {y[2024]:>+6.1%}")

open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("\n写出:", OUT)
