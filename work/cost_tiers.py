# -*- coding: utf-8 -*-
"""赎回费分档口径：场外（<7d 1.5% / 7-30d 0.5% / >=30d 0.15%）vs 场内（佣金 5bp）。

reb(交易日) → 自然日 ≈ reb*7/5，据此映射赎回费档位。
输出：E:\\Code\\quant\\work\\cost_tiers.txt
"""
import os
import sys

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, Fixed, VolTarget, features, load_panel, simulate, slice_metrics  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\work\cost_tiers.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


def sell_cost_for(reb, venue):
    """场外按持有自然日分档；场内只有佣金。"""
    if venue == "场内":
        return BUY
    cal_days = reb * 7 / 5
    if cal_days < 7:
        return BUY + 0.015
    if cal_days < 30:
        return BUY + 0.005
    return BUY + 0.0015


for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    n = len(close)
    log("=" * 84)
    log(f"[{pool}] {close.index[0].date()} ~ {close.index[-1].date()}  分档赎回费")
    log(f"{'配置':<26}{'年化':>9}{'回撤':>10}{'卡玛':>7}{'双边换手':>9}{'年化成本':>9}")
    cases = []
    for venue in ("场内", "场外"):
        for reb in (5, 10, 20, 25, 30):
            for label, rule in (
                ("S2满仓", Fixed(1.0)),
                ("VT15", VolTarget(target=0.15, window=60, every=reb)),
            ):
                sc = sell_cost_for(reb, venue)
                cases.append((f"{label} {venue} reb{reb}({sc - BUY:.2%})",
                              Config(reb=reb, buy_cost=BUY, sell_cost=sc, rule=rule)))
    for label, cfg in cases:
        res = simulate(close, f, cfg)
        m = slice_metrics(res, 60, n)
        log(f"{label:<26}{m['cagr']:>8.2%}{m['mdd']:>10.2%}{m['calmar']:>7.2f}"
            f"{m['turn_two_way']:>9.1f}x{m['cost_drag']:>9.2%}")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
