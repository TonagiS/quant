# -*- coding: utf-8 -*-
"""批量比较：叠加补丁 + 几类比较朴素的仓位/择时方案（2014-2026，指数池，5bp）。

方案：
 1 VT15
 2 VT12 / VT18
 3 减仓基准（VT15 × 30日线阶梯）
 4 补丁：MA5不新低 OR 回收50%（叠加）
 5 补丁（MA5不新低）
 6 补丁（回收50%）
 7 VT15 + 双均线(MA20>MA60 才在场)
 8 VT15 + MA200 半仓
 9 三重取小：VT15 × 减仓补丁 × MA200半仓
产物：E:\\Code\\quant\\work\\strategy_sweep.txt
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
sys.path.insert(0, r"E:\Code\quant\work")

from position.engine import Config, Rule, VolTarget, features, perf, simulate  # noqa: E402
from user_dd_rule import long_panel  # noqa: E402
from vrebound_patch import step_cut, patch_expo  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\work\strategy_sweep.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


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
level = close.mean(axis=1)
level = level / level.iloc[0]
ma20 = level.rolling(20, min_periods=20).mean()
ma60 = level.rolling(60, min_periods=60).mean()
ma200 = level.rolling(200, min_periods=200).mean()


def vt_scale(target):
    r = simulate(close, f, Config(reb=10, k=5, buy_cost=BUY, sell_cost=BUY,
                                  rule=VolTarget(target=target, window=60, every=10)))
    return pd.Series(r["scale"], index=idx)


vt12, vt15, vt18 = vt_scale(0.12), vt_scale(0.15), vt_scale(0.18)
cut, _ = step_cut(close, 30)
base = (vt15 * cut).clip(0, 1)

p_ma5 = pd.Series(patch_expo(base, level, R=0.99, Q=0.99, use_ma5=True), index=idx)
p_rec = pd.Series(patch_expo(base, level, R=0.99, Q=0.5, use_ma5=False), index=idx)
p_both = pd.Series(np.maximum(p_ma5.to_numpy(), p_rec.to_numpy()), index=idx)
p_both = (p_both * vt15).clip(0, 1)

double_ma = pd.Series(np.where((ma20 > ma60), 1.0, 0.0), index=idx).fillna(1.0)
ma200_half = pd.Series(np.where(level > ma200, 1.0, 0.5), index=idx).fillna(1.0)

variants = {
    "VT15": vt15,
    "VT12": vt12.clip(0, 1),
    "VT18": vt18.clip(0, 1),
    "减仓基准(30日)": base,
    "补丁MA5不新低": p_ma5,
    "补丁回收50%": p_rec,
    "补丁叠加(MA5∨回收)": p_both,
    "VT15×双均线": (vt15 * double_ma).clip(0, 1),
    "VT15×MA200半仓": (vt15 * ma200_half).clip(0, 1),
    "三重取小(VT15×减仓补丁×MA200)": (p_both * ma200_half).clip(0, 1),
}

log("=== 策略批量对比（2014-01 ~ 2026-11，指数池，5bp 佣金）===")
log(f"{'配置':<32}{'年化':>9}{'回撤':>10}{'卡玛':>7}{'仓位':>6} | 2019   2020   2024")
rows = []
for tag, e in variants.items():
    res = simulate(close, f, Config(reb=10, k=5, buy_cost=BUY, sell_cost=BUY, rule=Fix(e, tag)))
    m = perf(res["ret"][mask])
    s = pd.Series(res["ret"], index=idx)
    y = {yy: (1 + s[idx.year == yy]).prod() - 1 for yy in (2019, 2020, 2024)}
    rows.append((tag, m, y, np.mean(res["scale"][mask])))
    log(f"  {tag:<32}{m['cagr']:>8.2%}{m['mdd']:>10.2%}{m['calmar']:>7.2f}"
        f"{np.mean(res['scale'][mask]):>6.0%} | {y[2019]:>+6.1%} {y[2020]:>+6.1%} {y[2024]:>+6.1%}")

rows.sort(key=lambda r: -r[1]["calmar"])
log("\n按卡玛排序前三：" + "、".join(f"{r[0]}({r[1]['calmar']:.2f})" for r in rows[:3]))
rows.sort(key=lambda r: -r[1]["cagr"])
log("按年化排序前三：" + "、".join(f"{r[0]}({r[1]['cagr']:.2%})" for r in rows[:3]))

open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("\n写出:", OUT)
