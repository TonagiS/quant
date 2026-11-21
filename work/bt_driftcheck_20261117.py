# -*- coding: utf-8 -*-
"""口径对照：研究引擎（权重不漂移） vs 真实记账（持仓随价格漂移）。

同一信号、同一调仓网格（5 交易日）、同一 5bp 平费率，只差记账方式：
  engine.simulate : 每天把权重按目标恒等再平衡（相当于每日免费调回目标）
  harness full    : 到调仓日才动，期间仓位随行情漂移
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant\work")
sys.path.insert(0, r"E:\Code\quant\position")
sys.path.insert(0, r"E:\Code\quant\indicators\code")
import engine as E  # noqa: E402
import bt_topup_20261117 as B  # noqa: E402

EVAL = "2019-08-13"


def main():
    close = E.load_panel("ext")
    f = E.features(close)
    lo = int(close.index.searchsorted(pd.Timestamp(EVAL)))

    # 先用满仓版拿 S2 基础收益（VT15 的敞口就按它估波动，和实盘一致）
    base = E.run_with_base(close, f, E.Config(k=5, reb=5, cost=0.0005, delay=1,
                                             weight_mode="invvol", rule=E.Fixed(1.0)))

    for vt in (None, 0.15):
        cfg = E.Config(k=5, reb=5, cost=0.0005, delay=1, weight_mode="invvol",
                       rule=E.Fixed(1.0) if vt is None else E.VolTarget(vt, 60))
        res = E.run_with_base(close, f, cfg)
        m = E.perf(res["ret"][lo:])
        turn = float(np.sum(res["turn"][lo:]) / ((len(res["ret"]) - lo) / 252))
        print("engine   vt=%-5s CAGR %6.2f%% / MDD %7.2f%% / Calmar %.2f / 换手 %.1fx / 成本 %.2f%%" % (
            vt, m["cagr"] * 100, m["mdd"] * 100, m["calmar"], turn,
            float(np.sum(res["cost"][lo:]) / ((len(res["ret"]) - lo) / 252)) * 100))

        h = B.run(close, f, "full", vt=vt, expo_from=base["ret"],
                  reb_td=5, buy_cost=0.0005, sell_comm=0.0005, redeem_short=0.0)
        mh = B.metrics(h, lo)
        print("harness  vt=%-5s CAGR %6.2f%% / MDD %7.2f%% / Calmar %.2f / 换手 %.1fx / 成本 %.2f%%" % (
            vt, mh["cagr"] * 100, mh["mdd"] * 100, mh["calmar"], mh["turn"], mh["cost"] * 100))
        print()


if __name__ == "__main__":
    main()
