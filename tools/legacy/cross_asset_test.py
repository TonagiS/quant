# -*- coding: utf-8 -*-
"""跨资产适配实验：69 只混合池上，换一套"按类别/长周期"的信号能不能救回来。

V1 基线：现用 20/60/120 动量 + 60 日线 + 逆波动（已知 69 只 = 1.44%）
V2 长周期：120/250 日动量排名（降低跨资产切换）
V3 类别配额：A股 3 只 + 跨境/商品/债 2 只（强制分散到非同类资产）
V4 V2 + VT15
产物：E:\\Code\\quant\\work\\cross_asset_test.txt
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, Fixed, VolTarget, features, load_panel, perf, simulate  # noqa: E402

EXP = r"E:\Code\quant\data\cache2019_exp"
BUY = 0.0005
OUT = r"E:\Code\quant\work\cross_asset_test.txt"
lines = []

EXTRA = ["sh513520", "sh513030", "sh511010", "sh511260", "sz159985", "sz159981",
         "sh513120", "sz159792", "sh515210", "sz159825", "sz159766", "sz159996",
         "sh516110", "sh516960", "sh516950", "sz159870", "sh512890"]

# 非"A股行业 beta"的一类：债、商品、跨境
NON_EQ = {"sh511010", "sh511260", "sz159985", "sz159981", "sh513520", "sh513030",
          "sh513120", "sz159792", "sh513100", "sh513500", "sh513050", "sz159920", "sh518880"}


def log(s=""):
    print(s)
    lines.append(str(s))


def panel69():
    base = load_panel("ext")
    add = {}
    for c in EXTRA:
        p = os.path.join(EXP, f"{c}.csv")
        if os.path.exists(p):
            df = pd.read_csv(p, parse_dates=["date"])
            add[c] = df.set_index("date")["close"].astype(float)
    return pd.concat([base, pd.DataFrame(add)], axis=1).sort_index()


def run(close, f, label, wm="invvol", vt=None):
    lo = f["start"]
    reb = 10
    cfg = Config(reb=reb, k=5, buy_cost=BUY, fee_tier=True, weight_mode=wm,
                 rule=(VolTarget(target=vt, window=60, every=reb) if vt else Fixed(1.0)))
    res = simulate(close, f, cfg)
    m = perf(res["ret"][lo:])
    turn = float(res["turn"][lo:].sum() / (max(len(close) - lo, 1) / 252))
    log(f"  {label:<26}年化 {m['cagr']:>7.2%}  回撤 {m['mdd']:>8.2%}  卡玛 {m['calmar']:.2f}  换手 {turn:>5.1f}x")
    return m


def feat_long(close):
    """120/250 日动量排名（长周期，减少跨资产来回切）。"""
    f = features(close)
    mom250 = close / close.shift(250) - 1.0
    rk120 = f["mom"][120].where(f["valid"]).rank(axis=1, pct=True)
    rk250 = mom250.where(f["valid"] & mom250.notna()).rank(axis=1, pct=True)
    rm = (rk120 + rk250) / 2.0
    f2 = dict(f)
    f2["rm"] = rm
    f2["cand"] = f["valid"] & rm.notna() & (close > f["ma60"])
    return f2


def feat_quota(close):
    """类别配额：A股 3 只 + 非A股(债/商品/跨境) 2 只，通过分桶偏移拼成全局排名。"""
    f = features(close)
    eq = [c for c in close.columns if c not in NON_EQ]
    ne = [c for c in close.columns if c in NON_EQ]
    rk = f["rm"]
    score = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for cols, base in ((eq, 1.0), (ne, 0.5)):
        sub = rk[cols].rank(axis=1, ascending=False, pct=False)
        # 桶内第 1 名 0.9、第 2 名 0.8 … 桶底 0.5+，两桶错开保证跨类名额固定
        score[cols] = base + (1.0 - (sub - 1) / max(len(cols), 1)) * 0.4
    f2 = dict(f)
    f2["rm"] = score.where(rk.notna())
    f2["cand"] = f["valid"] & rk.notna() & (close > f["ma60"])
    return f2


log("=== 跨资产适配实验（场外分档、reb=10、k=5）===")
c69 = panel69()
log(f"\n[69 只混合池] 标的 {c69.shape[1]} 只")
run(c69, features(c69), "V1 基线（20/60/120）")
run(c69, feat_long(c69), "V2 长周期（120/250）")
run(c69, feat_long(c69), "V2 长周期 + VT15", vt=0.15)
run(c69, feat_quota(c69), "V3 类别配额（A3+非A2）")
run(c69, feat_quota(c69), "V3 类别配额 + VT15", vt=0.15)

log("\n[52 只原池对照]")
c52 = load_panel("ext")
run(c52, features(c52), "V1 基线（20/60/120）")
run(c52, feat_long(c52), "V2 长周期 + VT15", vt=0.15)

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
