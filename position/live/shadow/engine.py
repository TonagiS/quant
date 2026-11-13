"""Shadow 规则 v1 的纯函数实现（不依赖回测状态，可回放）。

与 backtest_composite 的口径对齐：
  valid = 累计 bar>=150 & 收盘非空 & mom120 非空 & vol20 非空
  rm    = (20/60/120 日动量的截面 pct rank 均值)，要求当日有效数 >= min_names
  候选   = valid & rm 非空 & 收盘 > 60 日线
  目标   = 候选按 rm 取前 k，权重按 20 日波动逆波动归一
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_NAMES = 8
K = 5
VOL_WIN = 20
BARS_MIN = 150


def features(close: pd.DataFrame) -> dict:
    ret = close.pct_change()
    vol20 = ret.rolling(VOL_WIN, min_periods=15).std()
    mom = {n: close / close.shift(n) - 1.0 for n in (20, 60, 120)}
    ma60 = close.rolling(60, min_periods=60).mean()
    bars = close.notna().cumsum()
    valid = (bars >= BARS_MIN) & close.notna() & mom[120].notna() & vol20.notna()
    rk = {n: mom[n].where(valid).rank(axis=1, pct=True) for n in (20, 60, 120)}
    rm = (rk[20] + rk[60] + rk[120]) / 3.0
    return {"ret": ret, "vol20": vol20, "mom": mom, "ma60": ma60, "valid": valid, "rm": rm, "rk": rk}


def targets(close: pd.DataFrame, min_names: int = MIN_NAMES, k: int = K) -> dict:
    """以 close 最后一行为决策日，返回目标权重与诊断。"""
    f = features(close)
    i = len(close) - 1
    asof = close.index[i]
    v = f["valid"].iloc[i]
    n_valid = int(v.sum())
    rm = f["rm"].iloc[i]
    enough = n_valid >= min_names and rm.notna().any()
    out = {"asof": str(asof.date()), "n_valid": n_valid, "enough": bool(enough),
           "targets": {}, "ranks": {}, "cand": [], "vol": {}}
    if not enough:
        return out
    cand = v & rm.notna() & (close.iloc[i] > f["ma60"].iloc[i])
    codes = list(cand[cand].index)
    out["cand"] = codes
    out["ranks"] = {c: float(rm[c]) for c in codes}
    if not codes:
        return out
    top = sorted(codes, key=lambda c: -rm[c])[:k]
    vol = f["vol20"].iloc[i]
    iv = np.array([1.0 / max(float(vol[c]) if np.isfinite(vol[c]) else 1e-4, 1e-4) for c in top])
    w = iv / iv.sum()
    out["targets"] = {c: float(x) for c, x in zip(top, w)}
    out["vol"] = {c: float(vol[c]) for c in top}
    return out


def equal_weight_index(close: pd.DataFrame) -> pd.Series:
    ret = close.pct_change()
    day = ret.mean(axis=1).fillna(0.0)
    return (1.0 + day).cumprod()


def cash_gate(close: pd.DataFrame, window: int = 120) -> dict:
    """信息用：等权指数是否在 120 日线之上（不影响仓位）。"""
    lvl = equal_weight_index(close)
    sma = lvl.rolling(window, min_periods=window).mean()
    ok = bool(lvl.iloc[-1] >= sma.iloc[-1]) if np.isfinite(sma.iloc[-1]) else True
    return {"above_ma": ok, "level": float(lvl.iloc[-1]),
            "sma": None if not np.isfinite(sma.iloc[-1]) else float(sma.iloc[-1])}
