# -*- coding: utf-8 -*-
"""场外费率：调仓周期延长测试（看 35 交易日是不是平台还是斜坡）+ 选定配置的 bootstrap。"""
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")

from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, features, load_panel, monthly, simulate,
    slice_metrics, stationary_bootstrap_ci,
)

BUY = 0.0005
OUTDIR = os.path.join(r"E:\Code\quant", "runs", f"{date.today():%Y-%m-%d}_POS-03_offex")
os.makedirs(OUTDIR, exist_ok=True)


def sell_cost(reb):
    cal = reb * 7 / 5
    if cal < 7:
        return BUY + 0.015
    if cal < 30:
        return BUY + 0.005
    return BUY + 0.0015


RULES = {
    "S2满仓": lambda reb: Fixed(1.0),
    "VT12": lambda reb: VolTarget(target=0.12, window=60, every=reb),
    "VT15": lambda reb: VolTarget(target=0.15, window=60, every=reb),
}
REBS = [35, 40, 45, 50, 60, 75]
rows, lines = [], ["\n## 延长期限测试（场外分档费率，单票上限 100%）\n",
                   "| 池 | 规则 | reb | 年化 | 回撤 | 卡玛 | 换手 | 年化成本 |",
                   "|---|---|---|---|---|---|---|---|"]

for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    n = len(close)
    for rname, mk in RULES.items():
        for reb in REBS:
            cfg = Config(reb=reb, buy_cost=BUY, sell_cost=sell_cost(reb), rule=mk(reb))
            res = simulate(close, f, cfg)
            m = slice_metrics(res, 60, n)
            rows.append({"pool": pool, "rule": rname, "reb": reb, **{k: m[k] for k in
                         ("cagr", "mdd", "calmar", "sharpe", "turn_two_way", "cost_drag", "avg_expo")}})
            lines.append(f"| {pool} | {rname} | {reb} | {m['cagr']:.2%} | {m['mdd']:.2%} | "
                         f"{m['calmar']:.2f} | {m['turn_two_way']:.1f}x | {m['cost_drag']:.2%} |")

pd.DataFrame(rows).to_csv(os.path.join(OUTDIR, "summary_ext.csv"), index=False, encoding="utf-8-sig")

# bootstrap：VT15/reb=35 场外 vs 满仓 S2 同费率、vs 满仓 S2 场内 5 日
lines.append("\n## 入选配置 VT15 / reb=35 / 场外（vs 两个基准的月度 bootstrap 90% 区间）\n")
for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    n, dates = len(close), close.index
    lo = 60
    cand = simulate(close, f, Config(reb=35, buy_cost=BUY, sell_cost=sell_cost(35),
                                     rule=VolTarget(target=0.15, window=60, every=35)))
    bench_same = simulate(close, f, Config(reb=35, buy_cost=BUY, sell_cost=sell_cost(35), rule=Fixed(1.0)))
    bench_in = simulate(close, f, Config(reb=5, buy_cost=BUY, sell_cost=BUY, rule=Fixed(1.0)))
    for label, bench in (("同费率满仓 reb35", bench_same), ("场内满仓 reb5(万5)", bench_in)):
        ci = stationary_bootstrap_ci(monthly(cand["ret"], dates, lo), monthly(bench["ret"], dates, lo))
        lines.append(f"- {pool} vs {label}：Sharpe 差 {ci['sharpe_med']:+.3f} "
                     f"[{ci['sharpe_lo']:+.3f}, {ci['sharpe_hi']:+.3f}]，"
                     f"CAGR 差 {ci['cagr_med']:+.2%} [{ci['cagr_lo']:+.2%}, {ci['cagr_hi']:+.2%}]")

text = "\n".join(lines)
with open(os.path.join(OUTDIR, "REPORT_EXT.md"), "w", encoding="utf-8") as fh:
    fh.write(text)
print(text)
print("\n产物:", OUTDIR)
