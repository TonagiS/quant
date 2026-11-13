# -*- coding: utf-8 -*-
"""回答两件事：① 换起始点/不同样本时长，VT15 还比 S2 强吗；② ETF 赎回费(0.5%/1.5%)下 S2 还活不活。

用法：E:\Code\quant\venv\Scripts\python.exe E:\Code\quant\work\cost_start_sens.py
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = r"E:\Code\quant"
sys.path.insert(0, ROOT)

from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, features, load_panel, simulate, slice_metrics,
)

TD = 252
OUT = os.path.join(ROOT, "work", "cost_start_sens.txt")
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


def run(pool, cfg):
    close = load_panel(pool)
    f = features(close)
    res = simulate(close, f, cfg)
    return close, res


def metrics(res, lo, hi):
    m = slice_metrics(res, lo, hi)
    return m


for pool in ("ext", "base"):
    close, _ = run(pool, Config(rule=Fixed(1.0)))
    f = features(close)
    n = len(close)
    dates = close.index
    r_s2 = simulate(close, f, Config(rule=Fixed(1.0)))
    r_vt = simulate(close, f, Config(rule=VolTarget(target=0.15, window=60, every=5)))

    log("=" * 70)
    log(f"[{pool}] 全样本 {dates[0].date()} ~ {dates[-1].date()}  {n} 交易日")
    m2, mv = metrics(r_s2, 60, n), metrics(r_vt, 60, n)
    log(f"  S2 满仓     年化 {m2['cagr']:.2%}  回撤 {m2['mdd']:.2%}  卡玛 {m2['calmar']:.2f}  双边换手 {m2['turn_two_way']:.1f}x/年")
    log(f"  VT15       年化 {mv['cagr']:.2%}  回撤 {mv['mdd']:.2%}  卡玛 {mv['calmar']:.2f}  平均仓位 {mv['avg_expo']:.0%}")

    log("  -- 起始点滚动（每半年一个起点，各跑满到样本末）--")
    log("      start        天数   S2卡玛  VT15卡玛  卡玛差  S2年化  VT15年化")
    worst = None
    for lo in range(60, n - 250, 125):
        m2s, m2v = metrics(r_s2, lo, n), metrics(r_vt, lo, n)
        d = m2v["calmar"] - m2s["calmar"]
        log(f"      {dates[lo].date()}  {n-lo:5d}   {m2s['calmar']:6.2f}   {m2v['calmar']:7.2f}  {d:+6.2f}  {m2s['cagr']:7.2%}  {m2v['cagr']:8.2%}")
        if worst is None or d < worst[0]:
            worst = (d, dates[lo].date())
    log(f"      最差起点: {worst[1]} 卡玛差 {worst[0]:+.2f}")

    log("  -- 固定窗口长度（3 年 = 756 交易日，滑动每半年）--")
    log("      start         S2卡玛  VT15卡玛  卡玛差")
    for lo in range(60, n - 756, 125):
        m2s, m2v = metrics(r_s2, lo, lo + 756), metrics(r_vt, lo, lo + 756)
        log(f"      {dates[lo].date()}   {m2s['calmar']:6.2f}   {m2v['calmar']:7.2f}  {m2v['calmar']-m2s['calmar']:+6.2f}")

    log(f"  -- 赎回费 / 调仓周期（S2 满仓信号，cost 每单位换手）--")
    log("      reb  成本成本项   年化      回撤     卡玛   双边换手  年化成本拖累")
    for reb in (5, 10, 20):
        for cost in (0.0005, 0.005, 0.015):
            rr = simulate(close, f, Config(reb=reb, cost=cost, rule=Fixed(1.0)))
            m = metrics(rr, 60, n)
            log(f"      {reb:3d}  {cost:7.2%}  {m['cagr']:8.2%}  {m['mdd']:8.2%}  {m['calmar']:6.2f}  {m['turn_two_way']:7.1f}x  {m['cost_drag']:8.2%}")

    log(f"  -- 同条件下 VT15（reb=10 / 20，cost 0.5% / 1.5%）--")
    for reb in (10, 20):
        for cost in (0.005, 0.015):
            rr = simulate(close, f, Config(reb=reb, cost=cost, rule=VolTarget(target=0.15, window=60, every=reb)))
            m = metrics(rr, 60, n)
            log(f"      {reb:3d}  {cost:7.2%}  {m['cagr']:8.2%}  {m['mdd']:8.2%}  {m['calmar']:6.2f}  {m['turn_two_way']:7.1f}x  仓位 {m['avg_expo']:.0%}")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
