# -*- coding: utf-8 -*-
"""长史回测：2007 年起的指数池（宽基 + 中证一级行业），看结论在 2019 年以前是否成立。

注意：index 不可直接交易（无 ETF），所以成本按"只佣金 5bp"（场内口径），
不代表实盘可复制；但能检验信号与 VT 的结构在 07、08、15 这些极端行情里是否还站得住。

产物：E:\\Code\\quant\\work\\long_history.txt
"""
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, Fixed, VolTarget, features, perf, simulate  # noqa: E402

CACHE = r"E:\Code\quant\data\cache2007"
BUY = 0.0005
OUT = r"E:\Code\quant\work\long_history.txt"
lines = []


def log(s=""):
    print(s)
    lines.append(str(s))


def panel():
    closes = {}
    for p in glob.glob(os.path.join(CACHE, "idx_*.csv")):
        code = os.path.basename(p).split("_")[1]
        df = pd.read_csv(p, parse_dates=["date"])
        if "close" not in df.columns or len(df) < 300:
            continue
        closes[code] = df.drop_duplicates("date").set_index("date")["close"].astype(float)
    return pd.DataFrame(closes).sort_index()


def feat_long(close):
    f = features(close)
    mom250 = close / close.shift(250) - 1.0
    rk120 = f["mom"][120].where(f["valid"]).rank(axis=1, pct=True)
    rk250 = mom250.where(f["valid"] & mom250.notna()).rank(axis=1, pct=True)
    rm = (rk120 + rk250) / 2.0
    g = dict(f)
    g["rm"] = rm
    g["cand"] = f["valid"] & rm.notna() & (close > f["ma60"])
    return g


def seg(close, res, a, b, label):
    idx = close.index
    lo = int(np.searchsorted(idx, pd.Timestamp(a)))
    hi = int(np.searchsorted(idx, pd.Timestamp(b)))
    m = perf(res["ret"][max(lo, 60):hi])
    turn = float(res["turn"][max(lo, 60):hi].sum() / (max(hi - max(lo, 60), 1) / 252))
    log(f"    {label:<16}年化 {m['cagr']:>7.2%}  回撤 {m['mdd']:>8.2%}  卡玛 {m['calmar']:>5.2f}  换手 {turn:>5.1f}x")


close = panel()
log(f"=== 长史指数池：{close.shape[1]} 只，{close.index[0].date()} ~ {close.index[-1].date()} ===")
log("（成本口径：只佣金 5bp；指数不可直接交易，仅供检验结构）")
fl = features(close)
fL = feat_long(close)

for tag, f in (("现用 20/60/120", fl), ("长周期 120/250", fL)):
    for vt in (None, 0.15):
        cfg = Config(reb=10, k=5, buy_cost=BUY, sell_cost=BUY,
                     rule=(VolTarget(target=vt, window=60, every=10) if vt else Fixed(1.0)))
        res = simulate(close, f, cfg)
        log(f"\n[{tag}]" + (f" + VT15" if vt else " 满仓"))
        seg(close, res, "2007-01-01", "2018-12-31", "2007-2018")
        seg(close, res, "2019-01-01", "2026-12-31", "2019-2026")
        seg(close, res, "2007-01-01", "2026-12-31", "全样本")

# 极端年份逐年
log("\n=== 逐年收益（现用+VT15 vs 满仓）===")
cfg_v = Config(reb=10, k=5, buy_cost=BUY, sell_cost=BUY,
               rule=VolTarget(target=0.15, window=60, every=10))
cfg_f = Config(reb=10, k=5, buy_cost=BUY, sell_cost=BUY, rule=Fixed(1.0))
rv = simulate(close, fl, cfg_v)
rf = simulate(close, fl, cfg_f)
s_v = pd.Series(rv["ret"], index=close.index)
s_f = pd.Series(rf["ret"], index=close.index)
for y, g in s_v.groupby(s_v.index.year):
    if y < 2008:
        continue
    log(f"  {y}: 满仓 {(1+s_f[s_f.index.year==y]).prod()-1:>+7.1%}   VT15 {(1+g).prod()-1:>+7.1%}")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
