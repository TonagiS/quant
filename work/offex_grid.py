# -*- coding: utf-8 -*-
"""场外费率下的仓位层网格：找出"场外最优"配置。

费率：买入佣金 5bp；卖出按持有自然日分档（<7d 1.5% / 7~30d 0.5% / >=30d 0.15%）+ 5bp 佣金。
持有自然日按 reb 交易日 ×7/5 估算。
产物：runs/2026-11-04_POS-03_offex/
"""
import itertools
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")

from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, features, load_panel, monthly, perf,
    simulate, slice_metrics, stationary_bootstrap_ci, yearly_table,
)

BUY = 0.0005
OUTDIR = os.path.join(r"E:\Code\quant", "runs", f"{date.today():%Y-%m-%d}_POS-03_offex")
os.makedirs(OUTDIR, exist_ok=True)


def sell_cost(reb: int) -> float:
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
    "VT20": lambda reb: VolTarget(target=0.20, window=60, every=reb),
}
REBS = [10, 15, 20, 25, 30, 35]
CAPS = [1.0, 0.4]

rows = []
for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    n = len(close)
    for (rname, mk), reb, cap in itertools.product(RULES.items(), REBS, CAPS):
        sc = sell_cost(reb)
        cfg = Config(reb=reb, buy_cost=BUY, sell_cost=sc, cap=cap, rule=mk(reb))
        res = simulate(close, f, cfg)
        m = slice_metrics(res, 60, n)
        rows.append({
            "pool": pool, "rule": rname, "reb": reb, "cap": cap,
            "sell_cost": round(sc, 5), "cagr": m["cagr"], "mdd": m["mdd"],
            "calmar": m["calmar"], "sharpe": m["sharpe"], "turn": m["turn_two_way"],
            "cost_drag": m["cost_drag"], "avg_expo": m["avg_expo"],
        })
    print(f"[{pool}] 网格完成 {len(rows)} 行")

df = pd.DataFrame(rows)
df.to_csv(os.path.join(OUTDIR, "summary_all.csv"), index=False, encoding="utf-8-sig")

# 每个池按卡玛排序的前 8
lines = []
for pool in ("ext", "base"):
    d = df[df.pool == pool].sort_values("calmar", ascending=False).head(8)
    lines.append(f"\n## {pool} 池：按卡玛前 8\n")
    lines.append("| 规则 | reb(交易日) | 单票上限 | 年化 | 回撤 | 卡玛 | 换手 | 年化成本 | 平均仓位 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for _, r in d.iterrows():
        lines.append(f"| {r['rule']} | {int(r['reb'])} | {r['cap']:.0%} | {r['cagr']:.2%} | "
                     f"{r['mdd']:.2%} | {r['calmar']:.2f} | {r['turn']:.1f}x | {r['cost_drag']:.2%} | {r['avg_expo']:.0%} |")

# 双池都进前 8 的稳健候选
best = {}
for pool in ("ext", "base"):
    d = df[df.pool == pool].sort_values("calmar", ascending=False).head(8)
    for _, r in d.iterrows():
        best.setdefault((r["rule"], int(r["reb"]), float(r["cap"])), {})[pool] = r["calmar"]
stable = {k: v for k, v in best.items() if len(v) == 2}
lines.append("\n## 两池同时进前 8 的稳健候选\n")
for k, v in sorted(stable.items(), key=lambda kv: -(kv[1]["ext"] + kv[1]["base"])):
    lines.append(f"- {k[0]} / reb={k[1]} / cap={k[2]:.0%} → ext 卡玛 {v['ext']:.2f}、base 卡玛 {v['base']:.2f}")

# 对最优候选做三段 + 逐年 + bootstrap（vs 满仓 S2 同费率）
lines.append("\n## 入选候选的分段检验\n")
picks = sorted(stable.items(), key=lambda kv: -(kv[1]["ext"] + kv[1]["base"]))[:2]
if not picks:
    picks = sorted(best.items(), key=lambda kv: -sum(kv[1].values()))[:2]

for (rname, reb, cap), v in picks:
    for pool in ("ext", "base"):
        close = load_panel(pool)
        f = features(close)
        n = len(close)
        sc = sell_cost(reb)
        res = simulate(close, f, Config(reb=reb, buy_cost=BUY, sell_cost=sc, cap=cap, rule=RULES[rname](reb)))
        base = simulate(close, f, Config(reb=reb, buy_cost=BUY, sell_cost=sc, rule=Fixed(1.0)))
        lo, hi = 60, n
        cut1 = lo + int((hi - lo) * 0.5)
        cut2 = cut1 + int((hi - lo) * 0.25)
        seg = [slice_metrics(res, lo, cut1), slice_metrics(res, cut1, cut2), slice_metrics(res, cut2, hi)]
        allm = slice_metrics(res, lo, hi)
        lines.append(f"\n### {rname} / reb={reb} / cap={cap:.0%} — {pool}")
        lines.append(f"全样本：年化 {allm['cagr']:.2%}、回撤 {allm['mdd']:.2%}、卡玛 {allm['calmar']:.2f}、"
                     f"换手 {allm['turn_two_way']:.1f}x、成本 {allm['cost_drag']:.2%}、平均仓位 {allm['avg_expo']:.0%}")
        lines.append("三段卡玛：" + " / ".join(f"{s['calmar']:.2f}" for s in seg))
        dates = close.index
        mon_a = monthly(res["ret"], dates, lo)
        mon_b = monthly(base["ret"], dates, lo)
        ci = stationary_bootstrap_ci(mon_a, mon_b)
        lines.append(f"vs 同费率满仓 S2：Sharpe 差 {ci.get('sharpe_diff', float('nan'))}")
        yt = yearly_table(res, dates, lo)
        lines.append("逐年：" + "、".join(f"{int(r.year)} {r.ret:+.1%}" for _, r in yt.iterrows()))

report = "\n".join(lines)
with open(os.path.join(OUTDIR, "REPORT.md"), "w", encoding="utf-8") as fh:
    fh.write("# POS-03 场外费率下的仓位层网格\n\n" + report)
json.dump({"fee": {"buy": BUY, "tiers": {"<7d": 0.015, "7-30d": 0.005, ">=30d": 0.0015}},
           "rebs": REBS, "rules": list(RULES), "caps": CAPS},
          open(os.path.join(OUTDIR, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(report)
print("\n产物:", OUTDIR)
