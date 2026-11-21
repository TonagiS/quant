"""规则敏感性与更细档位（研究用，只读）。

用法：venv\\Scripts\\python.exe work\\nt_rule_sens.py --shares work\\nt_shares_ext.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant\tools")
from nt_rule_backtest import (VARIANTS, SECTORS, load_panel, load_price,  # noqa: E402
                              daily_pct, build_state, run, yearly)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shares", default=r"E:\Code\quant\work\nt_shares_ext.csv")
    ap.add_argument("--from", dest="start", default="2015-01-01")
    a = ap.parse_args()

    panel = load_panel(Path(a.shares))
    px = load_price().reindex(panel.index).ffill()
    panel = panel[panel.index >= pd.Timestamp(a.start)]
    px = px[px.index >= pd.Timestamp(a.start)]
    dates = px.index
    eq_bh = np.cumprod(1 + px.pct_change().fillna(0).values)
    print("区间 %s ~ %s（%d 个交易日）买入持有 %+.1f%%，年化 %+.1f%%，回撤 %.1f%%"
          % (dates[0].date(), dates[-1].date(), len(dates), (eq_bh[-1] - 1) * 100,
             (eq_bh[-1] ** (252 / len(eq_bh)) - 1) * 100,
             ((eq_bh / np.maximum.accumulate(eq_bh)) - 1).min() * 100))

    pct = {k: daily_pct(panel, v) for k, v in SECTORS.items()}
    pct["all"] = daily_pct(panel, VARIANTS["all"])
    pct["small"] = daily_pct(panel, VARIANTS["small"])
    key = {"all": "all", "small": "small", "small500": "SM500", "small1000": "SM1000",
           "hs300": "HS300", "sz50": "SZ50"}

    def grid(variant: str, n: int, m: float, l: int, k: int):
        cum = pct[key[variant]].rolling(n).sum()
        pos = build_state(cum, m, l, k)
        r = run(px, pos)
        ep = int(((cum <= m) & (cum.shift(1) > m)).sum())
        return r, ep

    print("\n== 细档（n=14, l=5, k=20）：小盘合计 vs 现行合计 vs 单1000 ==")
    print(" m      small: 总/年化/回撤/空仓/段   |   all: 总/年化/回撤/段   |   small1000: 总/回撤/段")
    for m in range(-2, -21, -1):
        out = []
        for v in ("small", "all", "small1000"):
            r, ep = grid(v, 14, float(m), 5, 20)
            out.append("%+7.1f%%/%+5.1f%%/%+6.1f%%" % (r["total"] * 100, r["cagr"] * 100, r["mdd"] * 100)
                       + ("/%4.1f%%/%3d" % (r["flat_pct"] * 100, ep) if v == "small" else "/--/%3d" % ep))
        print("%4d  %s" % (m, "  |  ".join(out)))

    print("\n== 延迟 l / 防守期 k 敏感性（n=14, m=-10）==")
    print("variant  l   k    总收益    年化    回撤   空仓%")
    for v in ("small", "all"):
        for l in (0, 2, 5, 10):
            for k in (10, 20, 40):
                r, _ = grid(v, 14, -10.0, l, k)
                print("%-8s %2d %3d  %+7.1f%% %+6.1f%% %+6.1f%% %5.1f%%"
                      % (v, l, k, r["total"] * 100, r["cagr"] * 100, r["mdd"] * 100, r["flat_pct"] * 100))

    print("\n== 逐年（n=14, m=-10, l=5, k=20）==")
    for v in ("all", "small", "small1000"):
        cum = pct[key[v]].rolling(14).sum()
        pos = build_state(cum, -10.0, 5, 20)
        r, ep = grid(v, 14, -10.0, 5, 20)
        ys = yearly(r["eq"], dates)
        print("%-10s 段%2d " % (v, ep) + " ".join("%d:%+.1f%%" % (y, x * 100) for y, x in ys.items()))


if __name__ == "__main__":
    main()
