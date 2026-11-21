"""对网格里几组候选做体检：空仓期到底躲不躲得掉下跌（平坦期 vs 持仓期日收益）。

用法：venv\\Scripts\\python.exe tools\\nt_rule_diag.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nt_rule_backtest import (VARIANTS, SECTORS, L, K, COST, load_panel, load_price,
                              daily_pct, build_state, yearly)

SHORTLIST = [
    ("all", 14, -10.0, "现行冻结"),
    ("small", 14, -10.0, "小盘合计"),
    ("small", 14, -12.0, "小盘合计"),
    ("small", 10, -3.0, "小盘合计（低阈值）"),
    ("small1000", 14, -10.0, "中证1000 单只"),
    ("small500", 14, -10.0, "中证500 双只"),
    ("hs300", 14, -8.0, "沪深300 三只"),
    ("worst4", 20, -5.0, "四个板块取最差"),
]


def stats(px: pd.Series, pos: np.ndarray) -> dict:
    ret = px.pct_change().fillna(0.0).values
    hold = np.roll(pos, 1)
    hold[0] = 1
    cost = np.abs(np.diff(np.concatenate([[1], pos]))) * COST
    strat = (1 + ret * hold) * (1 - cost)
    eq = np.cumprod(strat)
    years = len(eq) / 252
    cagr = eq[-1] ** (1 / years) - 1
    mdd = float((eq / np.maximum.accumulate(eq) - 1).min())

    flat = (pos == 0) & (hold == 0)
    mkt = ret
    f = mkt[flat]
    h = mkt[hold == 1]
    t = f.mean() / (f.std(ddof=1) / np.sqrt(len(f))) if len(f) > 2 and f.std(ddof=1) > 0 else np.nan
    return dict(total=eq[-1] - 1, cagr=cagr, mdd=mdd, flat_pct=(pos == 0).mean(),
                flat_days=int(flat.sum()), flat_mean_ann=f.mean() * 252 if len(f) else np.nan,
                hold_mean_ann=h.mean() * 252 if len(h) else np.nan, t=t)


def seg(px: pd.Series, pos: np.ndarray, lo: str, hi: str) -> tuple[float, float]:
    m = (px.index >= lo) & (px.index <= hi)
    ret = px.pct_change().fillna(0.0).values
    hold = np.roll(pos, 1)
    hold[0] = 1
    sub = 1 + ret * hold
    eq = np.cumprod(sub[m])
    mdd = float((eq / np.maximum.accumulate(eq) - 1).min())
    return eq[-1] - 1, mdd


def main() -> None:
    panel = load_panel()
    px = load_price().reindex(panel.index).ffill()
    dates = px.index

    pct = {k: daily_pct(panel, v) for k, v in SECTORS.items()}
    pct["all"] = daily_pct(panel, VARIANTS["all"])
    pct["small"] = daily_pct(panel, VARIANTS["small"])
    pct["kc50"] = daily_pct(panel, VARIANTS["kc50"])
    key_of = {"all": "all", "sz50": "SZ50", "hs300": "HS300", "small500": "SM500",
              "small1000": "SM1000", "small": "small", "kc50": "kc50", "worst4": "worst4"}

    bh_ret = px.pct_change().fillna(0.0)
    eq_bh = np.cumprod(1 + bh_ret.values)
    bh_mdd = float((eq_bh / np.maximum.accumulate(eq_bh) - 1).min())
    s1, s2 = seg(px, np.ones(len(px), dtype=int), "2019-01-02", "2023-12-29")
    s3, s4 = seg(px, np.ones(len(px), dtype=int), "2024-01-02", "2026-11-13")
    print("买入持有：全期 %+.1f%% / 年化 %+.1f%% / mdd %.1f%%｜2019-2023 %+.1f%%（mdd %.1f%%）｜2024-2026 %+.1f%%（mdd %.1f%%）"
          % ((eq_bh[-1] - 1) * 100, (eq_bh[-1] ** (252 / len(eq_bh)) - 1) * 100, bh_mdd * 100,
             s1 * 100, s2 * 100, s3 * 100, s4 * 100))

    print("\n%-10s %3s %6s %-14s %8s %8s %8s %7s %9s %9s %6s %8s %8s"
          % ("口径", "n", "m", "备注", "总收益", "年化", "回撤", "空仓%", "空仓期年化", "持仓期年化", "t值", "19-23", "24-26"))
    for variant, n, m, note in SHORTLIST:
        if variant == "worst4":
            cum = pd.concat({s: pct[s].rolling(n).sum() for s in SECTORS}, axis=1).min(axis=1)
        else:
            cum = pct[key_of[variant]].rolling(n).sum()
        pos = build_state(cum, m)
        st = stats(px, pos)
        a1, _ = seg(px, pos, "2019-01-02", "2023-12-29")
        a2, _ = seg(px, pos, "2024-01-02", "2026-11-13")
        ep = int(((cum <= m) & (cum.shift(1) > m)).sum())
        print("%-10s %3d %6.1f %-14s %+7.1f%% %+7.1f%% %+7.1f%% %6.1f%% %+8.1f%% %+9.1f%% %6.2f %+7.1f%% %+7.1f%%"
              % (variant, n, m, "%s/段%d" % (note, ep), st["total"] * 100, st["cagr"] * 100,
                 st["mdd"] * 100, st["flat_pct"] * 100, st["flat_mean_ann"] * 100,
                 st["hold_mean_ann"] * 100, st["t"], a1 * 100, a2 * 100))


if __name__ == "__main__":
    main()
