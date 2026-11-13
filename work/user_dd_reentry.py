# -*- coding: utf-8 -*-
"""用户减仓规则 + 站回纠错（滞回/确认）：避免贴线反复穿越。

变体：
  base      原版：<MA20 线性降仓（MA20 处 50%，-10% 处 0%），≥MA20 满仓，<MA60 空仓
  buf{x}    站回需超过 MA20 +x% 才恢复满仓、跌到 MA20 -x% 才继续减仓，中间保持上一天仓位
  conf{n}   空仓/半仓后，须连续 n 日收盘站上 MA60 / MA20 才逐级恢复

产物：E:\\Code\\quant\\work\\user_dd_reentry.txt
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, Fixed, Rule, VolTarget, features, load_panel, perf, simulate  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\work\user_dd_reentry.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


def exposure(close, band=0.10, buf=0.0, conf=1, trend_weekly=60, ma_s=20, ma_l=60):
    bench = close.mean(axis=1)
    bench = bench / bench.iloc[0]
    ms = bench.rolling(ma_s, min_periods=ma_s).mean()
    ml = bench.rolling(ma_l, min_periods=ma_l).mean()
    wk = bench.resample("W-FRI").last().dropna()
    wk_ma = wk.rolling(trend_weekly, min_periods=trend_weekly).mean()
    trend = (wk > wk_ma).reindex(bench.index, method="ffill").fillna(0.0)

    out = np.zeros(len(bench))
    prev = 0.0
    up_s = up_l = 0
    for i in range(len(bench)):
        x, s, l = float(bench.iloc[i]), ms.iloc[i], ml.iloc[i]
        if not (np.isfinite(s) and np.isfinite(l)) or trend.iloc[i] <= 0:
            out[i] = 0.0
            prev, up_s, up_l = 0.0, 0, 0
            continue
        above_l = x >= float(l) * (1 + buf)
        above_s = x >= float(s) * (1 + buf)
        up_l = up_l + 1 if above_l else 0
        up_s = up_s + 1 if above_s else 0

        if x < float(l) * (1 - buf) and up_l == 0:
            target = 0.0
        elif prev <= 0.0:
            target = 0.5 if up_l >= conf else 0.0
        elif up_s >= conf:
            target = 1.0
        elif x <= float(s) * (1 - buf):
            dev = (x / float(s) - 1.0) / band
            target = float(np.clip(0.5 + 0.5 * dev, 0.0, 1.0))
        else:
            target = prev          # 缓冲带内保持
        out[i] = target
        prev = target
    return pd.Series(out, index=bench.index)


class Fix(Rule):
    def __init__(self, s, name=""):
        self.e = s.to_numpy()
        self.name = name

    def reset(self):
        pass

    def scale(self, j, ctx):
        return float(self.e[min(j, len(self.e) - 1)])


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
    log(f"\n[{label}] 卖出手续 {sell:.2%}")
    cases = [("原版（无纠错）", exposure(close, 0.10, 0.0, 1))]
    for buf in (0.01, 0.02, 0.03):
        cases.append((f"缓冲带 ±{buf:.0%}", exposure(close, 0.10, buf, 1)))
    for conf in (2, 3):
        cases.append((f"确认 {conf} 日", exposure(close, 0.10, 0.01, conf)))
        cases.append((f"缓冲1%+确认{conf}日", exposure(close, 0.10, 0.01, conf)))
    cases.append(("VT15 参照", None))
    for tag, s in cases:
        rule = VolTarget(target=0.15, window=60, every=10) if s is None else Fix(s, tag)
        res = simulate(close, f, Config(reb=10, k=5, buy_cost=BUY, sell_cost=sell, rule=rule))
        m = perf(res["ret"][lo:])
        avg = float(np.mean(res["scale"][lo:]))
        log(f"  {tag:<18}年化 {m['cagr']:>7.2%}  回撤 {m['mdd']:>7.2%}  卡玛 {m['calmar']:>5.2f}  平均仓位 {avg:.0%}")


log("=== 用户减仓规则 + 站回纠错 ===")
run(long_panel(), "长史指数池 2010-2026", 0.0005)
run(load_panel("ext"), "52 ETF 池 2019-2026", 0.005)

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
