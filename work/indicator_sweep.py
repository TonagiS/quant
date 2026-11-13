# -*- coding: utf-8 -*-
"""三个新指标叠加在 VT15 上：ADX 趋势强度、ATR 动态仓位、横截面离散度、量能确认。

口径：都以池等权指数/横截面截面统计为基础，最终敞口 = VT15 × 指标调整（只减不加）。
双窗口：2014-2026 指数池（5bp）、2019-2026 52 ETF（场外分档）。
产物：E:\\Code\\quant\\work\\indicator_sweep.txt
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
sys.path.insert(0, r"E:\Code\quant\work")

from position.engine import Config, Rule, VolTarget, features, load_panel, perf, simulate  # noqa: E402
from user_dd_rule import long_panel  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\work\indicator_sweep.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


def load_amounts():
    amts = {'idx': {}, 'etf': {}}
    for p in glob.glob(r"E:\Code\quant\data\cache2007\idx_*.csv"):
        code = os.path.basename(p).split("_")[1]
        df = pd.read_csv(p, parse_dates=["date"])
        if "amount" in df.columns and len(df) > 300:
            amts['idx'][code] = df.drop_duplicates("date").set_index("date")["amount"].astype(float)
    for p in glob.glob(r"E:\Code\quant\data\cache2019\*.csv"):
        code = os.path.basename(p)[:-4]
        df = pd.read_csv(p, parse_dates=["date"])
        if "amount" in df.columns:
            amts['etf'][code] = df.drop_duplicates("date").set_index("date")["amount"].astype(float)
    return amts


AMT = load_amounts()


def adx(level, n=14):
    h = level.rolling(2).max()
    l = level.rolling(2).min()
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - level.shift()).abs(), (l - level.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus_dm, index=level.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus_dm, index=level.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def atr_pct(level, n=14):
    h = level.rolling(2).max()
    l = level.rolling(2).min()
    tr = pd.concat([h - l, (h - level.shift()).abs(), (l - level.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean() / level


def dispersion(close, n=20):
    r = close.pct_change(n)
    return r.std(axis=1)


class Fix(Rule):
    def __init__(self, e, name):
        self.e = e.to_numpy()
        self.name = name

    def reset(self):
        pass

    def scale(self, j, ctx):
        return float(self.e[min(j, len(self.e) - 1)])


def run_window(label, close, amt_map, fee_kw, start_ts):
    f = features(close)
    idx = close.index
    lo = f["start"]
    mask = idx >= pd.Timestamp(start_ts)
    level = close.mean(axis=1)
    level = level / level.iloc[0]

    base = simulate(close, f, Config(reb=10, k=5, rule=VolTarget(target=0.15, window=60, every=10), **fee_kw))
    vt = pd.Series(base["scale"], index=idx)

    a = adx(level)
    at = atr_pct(level)
    disp = dispersion(close)
    # 量能：池合计成交额 5 日均 / 20 日均
    if amt_map:
        tot = pd.DataFrame(amt_map).reindex(idx).sum(axis=1)
        volr = tot.rolling(5).mean() / tot.rolling(20).mean()
    else:
        volr = pd.Series(1.0, index=idx)

    variants = {
        "VT15（基线）": vt,
        "ADX<20 减半": (vt * pd.Series(np.where(a < 20, 0.5, 1.0), index=idx)).clip(0, 1),
        "ADX<15 减半": (vt * pd.Series(np.where(a < 15, 0.5, 1.0), index=idx)).clip(0, 1),
        "ADX<20 空仓": (vt * pd.Series(np.where(a < 20, 0.0, 1.0), index=idx)).clip(0, 1),
        "ATR目标2%/日": pd.Series(np.clip(0.02 / at, 0, 1), index=idx).fillna(1.0),
        "ATR目标3%/日": pd.Series(np.clip(0.03 / at, 0, 1), index=idx).fillna(1.0),
        "离散度低于中位减半": (vt * pd.Series(np.where(disp < disp.rolling(250, min_periods=120).median(), 0.5, 1.0), index=idx)).clip(0, 1),
        "量能<0.8 减半": (vt * pd.Series(np.where(volr < 0.8, 0.5, 1.0), index=idx)).clip(0, 1),
        "量能>1 才满仓": (vt * pd.Series(np.where(volr > 1.0, 1.0, 0.5), index=idx)).clip(0, 1),
    }

    log(f"\n=== {label} ===")
    log(f"{'配置':<24}{'年化':>9}{'回撤':>10}{'卡玛':>7}{'仓位':>6}")
    for tag, e in variants.items():
        res = simulate(close, f, Config(reb=10, k=5, rule=Fix(e, tag), **fee_kw))
        m = perf(res["ret"][mask])
        log(f"  {tag:<24}{m['cagr']:>8.2%}{m['mdd']:>10.2%}{m['calmar']:>7.2f}{np.mean(res['scale'][mask]):>6.0%}")


run_window("2014-2026 · 指数池 · 5bp", long_panel(), AMT['idx'],
           dict(buy_cost=BUY, sell_cost=BUY), "2014-01-01")
run_window("2019-2026 · 52 ETF · 场外分档", load_panel("ext"), AMT['etf'],
           dict(buy_cost=BUY, fee_tier=True), "2019-01-01")

open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print("\n写出:", OUT)
