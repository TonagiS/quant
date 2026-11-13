# -*- coding: utf-8 -*-
"""真实费率下的表现：买入佣金 5bp + 卖出赎回费（<7天1.5% / ≥7天0.5%）。

映射：reb=5 交易日 ≈ 7 自然日（卡边界，按 1.5% 保守算）；reb=10 ≈ 14 自然日、reb=20 ≈ 28 自然日 → 按 0.5%。
输出：E:\\Code\\quant\\work\\cost_realistic.txt
"""
import os
import sys

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, Fixed, VolTarget, features, load_panel, simulate, slice_metrics  # noqa: E402

BUY = 0.0005
SELL_SHORT = 0.0155   # 1.5% 赎回费 + 5bp 佣金
SELL_LONG = 0.0055    # 0.5% + 5bp
OUT = r"E:\Code\quant\work\cost_realistic.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    n = len(close)
    log("=" * 78)
    log(f"[{pool}] {close.index[0].date()} ~ {close.index[-1].date()}  真实费率口径（买 {BUY:.2%} / 卖 短 {SELL_SHORT:.2%} 长 {SELL_LONG:.2%}）")
    log(f"{'配置':<22}{'年化':>9}{'回撤':>10}{'卡玛':>7}{'双边换手':>9}{'年化成本':>9}")
    cases = [
        ("S2 满仓 reb5 1.5%", Config(reb=5, buy_cost=BUY, sell_cost=SELL_SHORT, rule=Fixed(1.0))),
        ("S2 满仓 reb10 0.5%", Config(reb=10, buy_cost=BUY, sell_cost=SELL_LONG, rule=Fixed(1.0))),
        ("S2 满仓 reb20 0.5%", Config(reb=20, buy_cost=BUY, sell_cost=SELL_LONG, rule=Fixed(1.0))),
        ("VT15 reb5 1.5%", Config(reb=5, buy_cost=BUY, sell_cost=SELL_SHORT,
                                  rule=VolTarget(target=0.15, window=60, every=5))),
        ("VT15 reb10 0.5%", Config(reb=10, buy_cost=BUY, sell_cost=SELL_LONG,
                                   rule=VolTarget(target=0.15, window=60, every=10))),
        ("VT15 reb20 0.5%", Config(reb=20, buy_cost=BUY, sell_cost=SELL_LONG,
                                   rule=VolTarget(target=0.15, window=60, every=20))),
    ]
    for label, cfg in cases:
        res = simulate(close, f, cfg)
        m = slice_metrics(res, 60, n)
        log(f"{label:<22}{m['cagr']:>8.2%}{m['mdd']:>10.2%}{m['calmar']:>7.2f}"
            f"{m['turn_two_way']:>9.1f}x{m['cost_drag']:>9.2%}")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
