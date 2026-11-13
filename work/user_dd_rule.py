# -*- coding: utf-8 -*-
"""用户自述的减仓策略回测。

规则（按用户的描述，缺处按最合理假设补全，见 ASSUME）：
  1. 趋势判定：周线收盘 > 60 周均线 → 处于上涨趋势（日频按周线 ffill）
  2. 上涨趋势中，价格跌破 20 日线 → 仓位降到 50%
  3. 之后按价格相对 20 日线的偏离度线性给仓位：在 MA20 上 10% → 100%，正好 MA20 → 50%，MA20 下 10% → 0%
  4. 跌破 60 日线 → 空仓
  5. 非上涨趋势（周线在 60 周线下方）→ 空仓

ASSUME：规则作用在"池等权指数"上（代表大盘），因为原描述没说是单只还是组合。
产物：E:\\Code\\quant\\work\\user_dd_rule.txt
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, Fixed, Rule, VolTarget, features, load_panel, perf, simulate  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\work\user_dd_rule.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


def exposure_series(close, band=0.10, trend_weekly=60, ma_short=20, ma_long=60):
    """按用户规则生成逐日敞口序列。"""
    bench = close.mean(axis=1)  # 等权价格指数（近似大盘）
    bench = bench / bench.iloc[0]
    ma_s = bench.rolling(ma_short, min_periods=ma_short).mean()
    ma_l = bench.rolling(ma_long, min_periods=ma_long).mean()

    # 周线趋势：周收盘 > 60 周均线
    wk = bench.resample("W-FRI").last().dropna()
    wk_ma = wk.rolling(trend_weekly, min_periods=trend_weekly).mean()
    trend = (wk > wk_ma).astype(float)
    trend_d = trend.reindex(bench.index, method="ffill").fillna(0.0)

    expo = np.zeros(len(bench))
    for i in range(len(bench)):
        x = float(bench.iloc[i])
        if not np.isfinite(ma_l.iloc[i]) or not np.isfinite(trend_d.iloc[i]):
            expo[i] = 0.0
            continue
        if trend_d.iloc[i] <= 0:          # 非上涨趋势 → 空仓
            expo[i] = 0.0
            continue
        if x < float(ma_l.iloc[i]):       # 击穿 60 日线 → 空仓
            expo[i] = 0.0
            continue
        if x >= float(ma_s.iloc[i]):      # 20 日线上方 → 满仓
            expo[i] = 1.0
            continue
        dev = (x / float(ma_s.iloc[i]) - 1.0) / band   # 0 ~ -1
        expo[i] = float(np.clip(0.5 + 0.5 * dev, 0.0, 1.0))
    return pd.Series(expo, index=bench.index)


class PrecomputedRule(Rule):
    def __init__(self, expo: pd.Series, name="用户减仓规则"):
        self.expo = expo.to_numpy()
        self.name = name

    def reset(self):
        pass

    def scale(self, j, ctx):
        return float(self.expo[min(j, len(self.expo) - 1)])


def long_panel():
    closes = {}
    for p in glob.glob(r"E:\Code\quant\data\cache2007\idx_*.csv"):
        code = os.path.basename(p).split("_")[1]
        df = pd.read_csv(p, parse_dates=["date"])
        if "close" in df.columns and len(df) >= 300:
            closes[code] = df.drop_duplicates("date").set_index("date")["close"].astype(float)
    return pd.DataFrame(closes).sort_index()


def run(close, label, sell):
    f = features(close)
    lo = f["start"]
    idx = close.index
    log(f"\n[{label}] {idx[lo].date()} ~ {idx[-1].date()}  （卖出手续 {sell:.2%}）")
    rows = []
    for tag, rule in (("满仓", Fixed(1.0)),
                      ("VT15", VolTarget(target=0.15, window=60, every=10)),
                      ("用户规则 ±10%", PrecomputedRule(exposure_series(close, 0.10))),
                      ("用户规则 ±5%", PrecomputedRule(exposure_series(close, 0.05))),
                      ("用户规则 ±15%", PrecomputedRule(exposure_series(close, 0.15)))):
        res = simulate(close, f, Config(reb=10, k=5, buy_cost=BUY, sell_cost=sell, rule=rule))
        m = perf(res["ret"][lo:])
        avg = float(np.mean(res["scale"][lo:]))
        log(f"  {tag:<12}年化 {m['cagr']:>7.2%}  回撤 {m['mdd']:>7.2%}  卡玛 {m['calmar']:.2f}  平均仓位 {avg:.0%}")
        rows.append((tag, m, avg))
    return rows


log("=== 用户减仓策略回测（作用在池等权指数上）===")
run(load_panel("ext"), "52 ETF 池", 0.005)
run(long_panel(), "长史指数池", 0.0005)

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
