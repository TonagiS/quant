# -*- coding: utf-8 -*-
"""同一批仓位方案，分别跑 2014-2026（指数池，5bp）和 2019-2026（52 ETF，场外分档）。

产物：E:\\Code\\quant\\work\\sweep_two_windows.txt
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
sys.path.insert(0, r"E:\Code\quant\work")

from position.engine import Config, Rule, VolTarget, features, load_panel, perf, simulate  # noqa: E402
from user_dd_rule import long_panel  # noqa: E402
from vrebound_patch import step_cut, patch_expo  # noqa: E402

OUT = r"E:\Code\quant\work\sweep_two_windows.txt"
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


def build_variants(close, fee_kw, start_ts):
    f = features(close)
    idx = close.index
    lo = f["start"]
    mask = idx >= pd.Timestamp(start_ts)
    level = close.mean(axis=1)
    level = level / level.iloc[0]
    ma200 = level.rolling(200, min_periods=200).mean()

    def vt(t):
        r = simulate(close, f, Config(reb=10, k=5, rule=VolTarget(target=t, window=60, every=10), **fee_kw))
        return pd.Series(r["scale"], index=idx)

    vt12, vt15, vt18 = vt(0.12), vt(0.15), vt(0.18)
    cut, _ = step_cut(close, 30)
    base = (vt15 * cut).clip(0, 1)
    p_ma5 = pd.Series(patch_expo(base, level, R=0.99, Q=0.99, use_ma5=True), index=idx)
    p_rec = pd.Series(patch_expo(base, level, R=0.99, Q=0.5, use_ma5=False), index=idx)
    p_both = (pd.Series(np.maximum(p_ma5.to_numpy(), p_rec.to_numpy()), index=idx) * vt15).clip(0, 1)
    ma200_half = pd.Series(np.where(level > ma200, 1.0, 0.5), index=idx).fillna(1.0)

    variants = {
        "VT18": vt18.clip(0, 1),
        "VT15": vt15,
        "VT12": vt12.clip(0, 1),
        "VT15+MA200半仓": (vt15 * ma200_half).clip(0, 1),
        "减仓(30日阶梯)": base,
        "补丁叠加(MA5∨回收)": p_both,
        "VT12+补丁叠加": (pd.Series(np.maximum(p_ma5.to_numpy(), p_rec.to_numpy()), index=idx) * vt12).clip(0, 1),
        "三重取小(VT15×补丁×MA200)": (p_both * ma200_half).clip(0, 1),
    }

    out = []
    for tag, e in variants.items():
        res = simulate(close, f, Config(reb=10, k=5, rule=Fix(e, tag), **fee_kw))
        m = perf(res["ret"][mask])
        s = pd.Series(res["ret"], index=idx)
        y = {yy: (1 + s[idx.year == yy]).prod() - 1 for yy in (2019, 2020, 2024)}
        out.append((tag, m, np.mean(res["scale"][mask]), y))
    return out


def report(title, rows):
    log(f"\n=== {title} ===")
    log(f"{'配置':<28}{'年化':>9}{'回撤':>10}{'卡玛':>7}{'仓位':>6} | 2019   2020   2024")
    for tag, m, avg, y in rows:
        log(f"  {tag:<28}{m['cagr']:>8.2%}{m['mdd']:>10.2%}{m['calmar']:>7.2f}{avg:>6.0%}"
            f" | {y[2019]:>+6.1%} {y[2020]:>+6.1%} {y[2024]:>+6.1%}")
    best = max(rows, key=lambda r: r[1]["calmar"])
    log(f"  卡玛最高：{best[0]} {best[1]['calmar']:.2f}")


log("=== 两个窗口对比（同一批仓位方案）===")
report("2014-2026 · 指数池 15 只 · 5bp 佣金",
       build_variants(long_panel(), dict(buy_cost=0.0005, sell_cost=0.0005), "2014-01-01"))
report("2019-2026 · 52 ETF 池 · 场外分档赎回费",
       build_variants(load_panel("ext"), dict(buy_cost=0.0005, fee_tier=True), "2019-01-01"))

open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("\n写出:", OUT)
