# -*- coding: utf-8 -*-
"""归因：扩池掉收益，是"池子变差"还是"策略不适配"？

拆四层：
  1) 全池等权持有（f["bench"]）—— 纯池子效应
  2) S2 原样（动量+60日线+逆波动）
  3) S2 等权权重（动量+60日线，去掉逆波动）
  4) 去掉 60 日线过滤（动量+逆波动 / 动量+等权）
口径：场外分档（真实持有天数）、reb=10、k=5。
"""
import os
import sys

import pandas as pd

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, Fixed, features, load_panel, perf, simulate  # noqa: E402

EXP = r"E:\Code\quant\data\cache2019_exp"
BUY = 0.0005
OUT = r"E:\Code\quant\work\pool_attrib.txt"
lines = []

EXTRA = ["sh513520", "sh513030", "sh511010", "sh511260", "sz159985", "sz159981",
         "sh513120", "sz159792", "sh515210", "sz159825", "sz159766", "sz159996",
         "sh516110", "sh516960", "sh516950", "sz159870", "sh512890"]


def log(s=""):
    print(s)
    lines.append(str(s))


def panel(extra):
    base = load_panel("ext")
    add = {}
    for c in extra:
        p = os.path.join(EXP, f"{c}.csv")
        if os.path.exists(p):
            df = pd.read_csv(p, parse_dates=["date"])
            add[c] = df.set_index("date")["close"].astype(float)
    if not add:
        return base
    return pd.concat([base, pd.DataFrame(add)], axis=1).sort_index()


def bench_perf(close, f):
    lo = f["start"]
    r = f["bench"].pct_change().iloc[lo:].dropna().to_numpy()
    return perf(r)


def run(close, label):
    f = features(close)
    lo = f["start"]
    f_notrend = dict(f)
    f_notrend["cand"] = f["valid"] & f["rm"].notna()     # 去掉 60 日线过滤

    log(f"\n[{label}] 标的 {close.shape[1]} 只")
    b = bench_perf(close, f)
    log(f"  ① 全池等权持有        年化 {b['cagr']:>7.2%}  回撤 {b['mdd']:>8.2%}  卡玛 {b['calmar']:.2f}")

    cases = [
        ("② S2 原样(逆波动)", f, "invvol"),
        ("③ S2 等权", f, "equal"),
        ("④ 无60日线(逆波动)", f_notrend, "invvol"),
        ("⑤ 无60日线(等权)", f_notrend, "equal"),
    ]
    for name, fx, wm in cases:
        cfg = Config(reb=10, k=5, buy_cost=BUY, fee_tier=True, weight_mode=wm, rule=Fixed(1.0))
        res = simulate(close, fx, cfg)
        m = perf(res["ret"][lo:])
        turn = float(res["turn"][lo:].sum() / (max(len(close) - lo, 1) / 252))
        log(f"  {name:<18}年化 {m['cagr']:>7.2%}  回撤 {m['mdd']:>8.2%}  卡玛 {m['calmar']:.2f}  换手 {turn:>5.1f}x")


log("=== 扩池归因（场外分档、reb=10、k=5）===")
run(load_panel("ext"), "现有 52 只")
run(panel(EXTRA), "扩池 69 只")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
