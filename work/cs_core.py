#!/usr/bin/env python3
"""core_stress 公共部分：底仓 DIV-IV / C3（可带改动）带每日权重的回放、冲击叠加、指标。样本内研究。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import components_core as K  # noqa: E402

RC, PB = K.RC, K.PB
ROOT = K.ROOT
RUN = ROOT / "runs" / "2026-11-21_core_stress"
CS = ROOT / "data" / "cache_core_stress"
BOND, SHORT = "sh511010", "sh511360"
ASSETS = K.BASE8 + [SHORT]
SLEEVE = {"sh510300": "A股", "sh510500": "A股", "sz159915": "A股", "sh588000": "A股", "sh511010": "国债",
          "sh511360": "短债", "sh518880": "黄金", "sh513100": "美股", "sh513500": "美股"}
S0_DATE = "2013-01-04"     # 与 W2013 相同
_S = {}


def R0():
    if "R" not in _S:
        _S["R"] = K.core_returns()[ASSETS].copy()
    return _S["R"]


def s0(index=None):
    idx = R0().index if index is None else index
    return int(idx.searchsorted(pd.Timestamp(S0_DATE)))


def tweak_w(w, tweak):
    w = w.copy()
    if tweak in (None, "none"):
        return w
    b = float(w.get(BOND, 0.0))
    if tweak.startswith("BCAP"):
        cap = int(tweak[4:]) / 100
        if b > cap:
            ex = b - cap
            w[BOND] = cap
            oth = [c for c in w.index if c != BOND]
            tot = float(w[oth].sum())
            if tot > 0:
                w[oth] = w[oth] + ex * w[oth] / tot
    elif tweak.startswith("SD"):
        f = int(tweak[2:]) / 100
        w[BOND] = b * (1 - f)
        w[SHORT] = w.get(SHORT, 0.0) + b * f
    elif tweak.startswith("BC") and tweak.endswith("S"):   # BC50S：国债上限 50%，超出部分转短债
        cap = int(tweak[2:-1]) / 100
        if b > cap:
            w[BOND] = cap
            w[SHORT] = w.get(SHORT, 0.0) + b - cap
    return w


def targets(R, kind="C3", tweak=None, start=None, mult=None):
    st = s0(R.index) if start is None else start
    if kind == "C3" and mult is None:
        mult = K.erp_mult()[0].reindex(R.index).ffill().fillna(1.0)
    tg, _ = K.core_targets(R, st, mult=mult if kind == "C3" else None)
    return {s: tweak_w(w, tweak) for s, w in tg.items()}, st


def sim_w(R, tg, st, cost):
    """同 PB.sim_targets，另外返回每日（当日收益前）的权重矩阵。"""
    Rv = np.nan_to_num(R.to_numpy(), nan=0.0)
    T, N = Rv.shape
    col = {c: k for k, c in enumerate(R.columns)}
    ex = {}
    for s, w in tg.items():
        if s + 1 < T:
            v = np.zeros(N)
            for c, x in w.items():
                v[col[c]] = x
            ex[s + 1] = v
    w = np.zeros(N)
    net, turn = np.zeros(T), np.zeros(T)
    W = np.zeros((T, N))
    for t in range(st, T):
        W[t] = w
        g = float(w @ Rv[t])
        if w.sum() > 0:
            w = w * (1 + Rv[t]) / (1 + g)
        c = 0.0
        if t in ex:
            tv = ex[t]
            tr = float(np.abs(tv - w).sum())
            c = cost * tr
            turn[t] = tr
            w = tv.copy()
        net[t] = g - c
    return net, turn, W


def core(R=None, kind="C3", tweak=None, cost=0.0005, start=None, mult=None):
    R = R0() if R is None else R
    tg, st = targets(R, kind, tweak, start, mult)
    net, turn, W = sim_w(R, tg, st, cost)
    contrib = W * np.nan_to_num(R.to_numpy(), nan=0.0)
    return dict(ret=net, turn=turn, W=W, contrib=contrib, lo=st, cols=list(R.columns), index=R.index)


def nav(r):
    return np.cumprod(1 + np.asarray(r))


def perf(r):
    r = np.asarray(r, float)
    if len(r) < 20:
        return dict(cagr=np.nan, mdd=np.nan, calmar=np.nan)
    eq = nav(r)
    cagr = eq[-1] ** (252 / len(r)) - 1 if eq[-1] > 0 else -1.0
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    return dict(cagr=float(cagr), mdd=dd, calmar=float(cagr / abs(dd)) if dd < 0 else np.nan)


def episode(r, index, a, b, lo):
    """区间 [a,b]：区间收益、区间内最大回撤（相对全历史峰值，从 lo 起算）、谷底日期、从谷底回到该峰值所需交易日。"""
    r = np.asarray(r, float).copy()
    r[:lo] = 0.0
    eq = nav(r)
    pk = np.maximum.accumulate(eq)
    dd = eq / pk - 1
    ia, ib = int(index.searchsorted(pd.Timestamp(a))), int(index.searchsorted(pd.Timestamp(b), side="right")) - 1
    seg = dd[ia: ib + 1]
    k = ia + int(np.argmin(seg))
    peak_val = pk[k]
    rec = np.flatnonzero(eq[k:] >= peak_val - 1e-12)
    rec_days = int(rec[0]) if len(rec) else None
    # 区间内自身的最大回撤（峰值从区间起点开始算）
    e2 = eq[ia: ib + 1] / eq[ia - 1] if ia > 0 else eq[ia: ib + 1]
    mdd_in = float((e2 / np.maximum.accumulate(np.r_[1.0, e2])[1:] - 1).min())
    return dict(ret=float(eq[ib] / eq[ia - 1] - 1), mdd_in=mdd_in, dd_trough=float(dd[k]), trough=str(index[k].date()),
                rec_days=rec_days, rec_date=str(index[k + rec_days].date()) if rec_days is not None else "未修复")
