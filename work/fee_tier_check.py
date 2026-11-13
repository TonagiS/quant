# -*- coding: utf-8 -*-
"""回答两个问题：
1) 按"每只标的实际持有天数"分档收赎回费（fee_tier）vs 按调仓周期一刀切，差多少？
2) 14 天调仓时，有多少仓位其实是不动的？每只平均持有多久？
输出：E:\\Code\\quant\\work\\fee_tier_check.txt
"""
import os
import sys

import numpy as np

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, Fixed, VolTarget, features, load_panel, simulate, slice_metrics  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\work\fee_tier_check.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


def sell_cost(reb):
    cal = reb * 7 / 5
    if cal < 7:
        return BUY + 0.015
    if cal < 30:
        return BUY + 0.005
    return BUY + 0.0015


def holding_stats(w_hist):
    """从每日权重序列统计每只标的的持有段（交易日）与退出时的持有天数。"""
    runs = []
    cur = {}
    for d, w in enumerate(w_hist):
        live = {c for c, v in w.items() if v > 1e-9}
        for c in list(cur):
            if c not in live:
                runs.append((c, cur[c], d - cur[c]))
                del cur[c]
        for c in live:
            cur.setdefault(c, d)
    for c, s in cur.items():
        runs.append((c, s, len(w_hist) - s))
    if not runs:
        return {}
    days = np.array([r[2] for r in runs], float)
    cal = days * 7 / 5
    closed = np.array([r[2] for r in runs if r[1] + r[2] < len(w_hist) - 1], float) * 7 / 5
    return {
        "n_runs": len(runs),
        "avg_trading_days": float(days.mean()),
        "median_trading_days": float(np.median(days)),
        "short_share": float((closed < 7).mean()) if len(closed) else float("nan"),
        "mid_share": float(((closed >= 7) & (closed < 30)).mean()) if len(closed) else float("nan"),
        "long_share": float((closed >= 30).mean()) if len(closed) else float("nan"),
    }


for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    n = len(close)
    log("=" * 96)
    log(f"[{pool}] {close.index[0].date()} ~ {close.index[-1].date()}")
    log(f"{'配置':<16}{'一刀切':>18}{'按实际持有天':>20}{'差':>9}{'平均持有(交易日)':>16}"
        f"{'<7天':>7}{'7-30天':>8}{'>=30天':>8}")
    for reb in (5, 10, 14, 22, 30):
        for rname, mk in (("S2满仓", lambda r: Fixed(1.0)), ("VT15", lambda r: VolTarget(target=0.15, window=60, every=r))):
            flat = simulate(close, f, Config(reb=reb, buy_cost=BUY, sell_cost=sell_cost(reb), rule=mk(reb)))
            real = simulate(close, f, Config(reb=reb, buy_cost=BUY, sell_cost=BUY, fee_tier=True, rule=mk(reb)),
                            keep_weights=True)
            mf = slice_metrics(flat, 60, n)
            mr = slice_metrics(real, 60, n)
            st = holding_stats(real["w_hist"][60:])
            log(f"{rname + ' reb' + str(reb):<16}"
                f"{mf['cagr']:>8.2%}/{mf['calmar']:.2f}  "
                f"{mr['cagr']:>8.2%}/{mr['calmar']:.2f}  "
                f"{(mr['cagr'] - mf['cagr']):>+8.2%}  "
                f"{st.get('avg_trading_days', float('nan')):>14.1f}  "
                f"{st.get('short_share', float('nan')):>6.0%}"
                f"{st.get('mid_share', float('nan')):>8.0%}"
                f"{st.get('long_share', float('nan')):>8.0%}")

log("\n说明：'一刀切'=按调仓周期映射档位（保守，假设每笔都最小持有）；")
log("     '按实际持有天'=每只标的从建仓起算，卖出时看真实持有自然日落在哪一档（更真实）。")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
