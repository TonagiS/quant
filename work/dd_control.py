# -*- coding: utf-8 -*-
"""回撤控制方案对比：在长史（2007 起指数池）和现池（52 ETF）上一起测。

方案：
  A 基线满仓
  B VT15（现状）
  C VT15 + 池等权跌破 200 日线则腰斩敞口
  D 下行波动目标（只按下行波动缩放，目标 10%）
  E VT15 + 尾部急停（20 日波动 >30% 时敞口再砍半）
  F 80% 策略(VT15) + 20% 国债ETF 固定配置（改变风险预算，不参与排名）

产物：E:\\Code\\quant\\work\\dd_control.txt
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, Fixed, Rule, VolTarget, features, load_panel, perf, simulate  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\work\dd_control.txt"
TD = 252
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


class VT_MATrend(Rule):
    """VT15；池等权跌破 200 日均线时敞口再乘 below。"""

    def __init__(self, target=0.15, window=60, every=10, below=0.5, ma=200):
        self.target, self.window, self.every = target, window, every
        self.below, self.ma = below, ma
        self.name = "VT15+MA200半仓"
        self.cache = 1.0

    def reset(self):
        self.cache = 1.0

    def scale(self, j, ctx):
        bench = ctx["bench"][: j + 1]
        if len(bench) < self.ma + 1:
            trend = 1.0
        else:
            ma = float(np.mean(bench[-self.ma:]))
            trend = 1.0 if bench[-1] >= ma else self.below
        src = ctx.get("base_rets")
        lo = max(int(ctx.get("start", 0)), j - self.window + 1)
        r = (src if src is not None else ctx["rets"])[lo: j + 1]
        if len(r) < 40:
            return trend
        sd = float(np.nanstd(r, ddof=1)) * np.sqrt(TD)
        if not np.isfinite(sd) or sd <= 1e-8:
            return trend
        return float(np.clip(self.target / sd, 0.0, 1.0)) * trend


class SemiVT(Rule):
    """只按下行波动缩放（下行波动目标）。"""

    def __init__(self, target=0.10, window=60, every=10):
        self.target, self.window, self.every = target, window, every
        self.name = f"下行波动目标{target:.0%}"

    def reset(self):
        pass

    def scale(self, j, ctx):
        src = ctx.get("base_rets")
        lo = max(int(ctx.get("start", 0)), j - self.window + 1)
        r = np.asarray((src if src is not None else ctx["rets"])[lo: j + 1], float)
        r = r[np.isfinite(r)]
        if len(r) < 40:
            return 1.0
        down = r[r < 0]
        if len(down) < 10:
            return 1.0
        sd = float(np.std(down, ddof=1)) * np.sqrt(TD)
        if sd <= 1e-8:
            return 1.0
        return float(np.clip(self.target / sd, 0.0, 1.0))


class VT_TailCut(Rule):
    """VT15；20 日已实现波动 > tail 时敞口再砍半。"""

    def __init__(self, target=0.15, window=60, every=10, tail=0.30):
        self.target, self.window, self.every, self.tail = target, window, every, tail
        self.name = "VT15+尾部急停"

    def reset(self):
        pass

    def scale(self, j, ctx):
        src = ctx.get("base_rets")
        lo = max(int(ctx.get("start", 0)), j - self.window + 1)
        r = np.asarray((src if src is not None else ctx["rets"])[lo: j + 1], float)
        r = r[np.isfinite(r)]
        if len(r) < 40:
            return 1.0
        sd = float(np.std(r, ddof=1)) * np.sqrt(TD)
        if sd <= 1e-8:
            return 1.0
        expo = float(np.clip(self.target / sd, 0.0, 1.0))
        if len(r) >= 20:
            sd20 = float(np.std(r[-20:], ddof=1)) * np.sqrt(TD)
            if sd20 > self.tail:
                expo *= 0.5
        return expo


def long_panel():
    closes = {}
    for p in glob.glob(r"E:\Code\quant\data\cache2007\idx_*.csv"):
        code = os.path.basename(p).split("_")[1]
        df = pd.read_csv(p, parse_dates=["date"])
        if "close" in df.columns and len(df) >= 300:
            closes[code] = df.drop_duplicates("date").set_index("date")["close"].astype(float)
    return pd.DataFrame(closes).sort_index()


def etf_panel():
    return load_panel("ext")


def run(close, label, fee_mode):
    f = features(close)
    lo = f["start"]
    idx = close.index
    log(f"\n[{label}] 标的 {close.shape[1]}，{idx[0].date()} ~ {idx[-1].date()}")

    def show(tag, rule, cost_kw):
        cfg = Config(reb=10, k=5, rule=rule, **cost_kw)
        res = simulate(close, f, cfg)
        m = perf(res["ret"][lo:])
        segs = []
        for a, b in ((2007, 2018), (2019, 2026)):
            mask = np.asarray((idx >= pd.Timestamp(f"{a}-01-01")) & (idx <= pd.Timestamp(f"{b}-12-31")))
            rr = res["ret"][mask]
            mm = perf(rr)
            segs.append(f"{a}-{b} {mm['cagr']:>6.1%}/{mm['calmar']:.2f}")
        log(f"  {tag:<26}全样本 {m['cagr']:>7.2%} / {m['mdd']:>7.1%} / 卡玛 {m['calmar']:>5.2f}   "
            + "  ".join(segs))

    if fee_mode == "index":
        ck = dict(buy_cost=BUY, sell_cost=BUY)
    else:
        ck = dict(buy_cost=BUY, fee_tier=True)

    show("A 满仓", Fixed(1.0), ck)
    show("B VT15", VolTarget(target=0.15, window=60, every=10), ck)
    show("C VT15+MA200半仓", VT_MATrend(), ck)
    show("D 下行波动目标10%", SemiVT(), ck)
    show("E VT15+尾部急停", VT_TailCut(), ck)

    # F 80/20：策略(VT15) + 国债ETF
    if fee_mode == "etf":
        bond_path = r"E:\Code\quant\data\cache2019_exp\sh511010.csv"
        if os.path.exists(bond_path):
            bdf = pd.read_csv(bond_path, parse_dates=["date"]).set_index("date")["close"].astype(float)
            res = simulate(close, f, Config(reb=10, k=5, rule=VolTarget(target=0.15, window=60, every=10),
                                            buy_cost=BUY, fee_tier=True))
            strat = pd.Series(res["ret"], index=idx)[lo:]
            bond = bdf.pct_change().reindex(strat.index).fillna(0.0)
            for w in (0.2, 0.3):
                blend = (1 - w) * strat + w * bond
                m = perf(blend.to_numpy())
                mask = (strat.index >= pd.Timestamp("2019-01-01"))
                mm = perf(blend[mask].to_numpy())
                log(f"  {'F %d%%国债 + %d%%策略' % (w*100, (1-w)*100):<26}全样本 "
                    f"{m['cagr']:>7.2%} / {m['mdd']:>7.1%} / 卡玛 {m['calmar']:>5.2f}   "
                    f"2019-2026 {mm['cagr']:>6.1%}/{mm['calmar']:.2f}")


log("=== 回撤控制方案对比 ===")
log("（长史用指数池，成本只算佣金；现池用 52 ETF，场外分档费率）")
run(long_panel(), "长史指数池", "index")
run(etf_panel(), "现池 52 ETF", "etf")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
