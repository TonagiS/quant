# -*- coding: utf-8 -*-
"""场外新口径（按每只真实持有天数分档）下，8~12 交易日调仓的结果。

产物：E:\\Code\\quant\\work\\reb8_10_offex.txt
"""
import os
import sys

import numpy as np

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, Fixed, VolTarget, features, load_panel, simulate, slice_metrics  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\work\reb8_10_offex.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


def avg_hold(w_hist):
    cur, runs = {}, []
    for d, w in enumerate(w_hist):
        live = {c for c, v in w.items() if v > 1e-9}
        for c in list(cur):
            if c not in live:
                runs.append(d - cur.pop(c))
        for c in live:
            cur.setdefault(c, d)
    return float(np.mean(runs)) if runs else float("nan")


def tiered(reb, rule, fee_mid=0.005, fee_short=0.015, fee_long=0.0015):
    return Config(reb=reb, buy_cost=BUY, fee_tier=True,
                  fee_short=fee_short, fee_mid=fee_mid, fee_long=fee_long, rule=rule)


RULES = {
    "S2满仓": lambda r: Fixed(1.0),
    "VT15": lambda r: VolTarget(target=0.15, window=60, every=r),
}

for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    n = len(close)
    log("=" * 92)
    log(f"[{pool}] 场外新口径（真实持有天数分档：<7d 1.5% / 7-30d 0.5% / >=30d 0.15%）")
    log(f"{'配置':<18}{'年化':>9}{'回撤':>10}{'卡玛':>7}{'换手':>8}{'年化成本':>9}{'平均持有':>9}")
    for reb in (5, 6, 7, 8, 9, 10, 11, 12):
        for rname, mk in RULES.items():
            res = simulate(close, f, tiered(reb, mk(reb)), keep_weights=True)
            m = slice_metrics(res, 60, n)
            log(f"{rname + ' reb' + str(reb):<18}{m['cagr']:>8.2%}{m['mdd']:>10.2%}{m['calmar']:>7.2f}"
                f"{m['turn_two_way']:>7.1f}x{m['cost_drag']:>9.2%}{avg_hold(res['w_hist'][60:]):>9.1f}")

    log(f"\n[{pool}] 中档费率影响（reb=10，其余档不变；按真实持有天数）")
    for label, fm in (("0.5%(最差)", 0.005), ("0.2%", 0.002), ("0.10%", 0.001), ("0% (7天免费)", 0.0)):
        for rname, mk in RULES.items():
            res = simulate(close, f, tiered(10, mk(10), fee_mid=fm))
            m = slice_metrics(res, 60, n)
            log(f"  reb10 {rname:<8} 中档 {label:<12} 年化 {m['cagr']:>7.2%}  回撤 {m['mdd']:>8.2%}  卡玛 {m['calmar']:.2f}")

    log(f"\n[{pool}] 场内对照（只佣金 5bp，无赎回费）")
    for reb in (5, 8, 10):
        for rname, mk in RULES.items():
            res = simulate(close, f, Config(reb=reb, buy_cost=BUY, sell_cost=BUY, rule=mk(reb)))
            m = slice_metrics(res, 60, n)
            log(f"  reb{reb:<3}{rname:<8} 年化 {m['cagr']:>7.2%}  回撤 {m['mdd']:>8.2%}  卡玛 {m['calmar']:.2f}  换手 {m['turn_two_way']:.1f}x")
    log("")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
