# -*- coding: utf-8 -*-
"""执行口径敏感性：full（全额对齐）vs topup（只补低配），多调仓周期 / 双池 / 费率口径。

输出：
  1) 与历史研究口径的对照（7 自然日≈5 交易日、5bp 平费率）
  2) 7 / 14 / 21 自然日 × ext/base 池的 CAGR/MDD/卡玛差
  3) 14 日、ext 池的月度收益 bootstrap（差值 90% 区间）
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
FLAT = dict(buy_cost=0.0005, sell_comm=0.0005, redeem_short=0.0)


def one(close, f, lo, mode, vt=None, expo_from=None, reb=14, **kw):
    r = B.run(close, f, mode, expo_from=expo_from, vt=vt, reb_days=reb, **kw)
    return r, B.metrics(r, lo)


def show(tag, a, b):
    print("%-26s full %6.2f%% / %7.2f%% / %.2f   topup %6.2f%% / %7.2f%% / %.2f   dCAGR %+5.2fpp  dCalmar %+5.2f  expo %.1f%% -> %.1f%%" % (
        tag, a["cagr"] * 100, a["mdd"] * 100, a["calmar"],
        b["cagr"] * 100, b["mdd"] * 100, b["calmar"],
        (b["cagr"] - a["cagr"]) * 100, b["calmar"] - a["calmar"],
        a["expo"] * 100, b["expo"] * 100))


def main():
    for pool in ("ext", "base"):
        close = E.load_panel(pool)
        f = E.features(close)
        lo = int(close.index.searchsorted(pd.Timestamp(EVAL)))
        print("\n===== pool=%s（%d 只，评估 %s ~ %s）=====" % (
            pool, close.shape[1], close.index[lo].date(), close.index[-1].date()))
        for reb in (7, 14, 21):
            s2f, mf = one(close, f, lo, "full", reb=reb)
            s2t, mt = one(close, f, lo, "topup", reb=reb)
            show("S2 满仓  %2d日" % reb, mf, mt)
            vf, mf2 = one(close, f, lo, "full", vt=0.15, expo_from=s2f["ret"], reb=reb)
            vt_, mt2 = one(close, f, lo, "topup", vt=0.15, expo_from=s2t["ret"], reb=reb)
            show("VT15      %2d日" % reb, mf2, mt2)

    # 1) 历史口径对照：7 自然日 + 5bp 平费率（研究基线 VT15 16.50%/-16.51%/1.00）
    close = E.load_panel("ext")
    f = E.features(close)
    lo = int(close.index.searchsorted(pd.Timestamp(EVAL)))
    s2f, _ = one(close, f, lo, "full", reb=7, **FLAT)
    _, m_flat = one(close, f, lo, "full", vt=0.15, expo_from=s2f["ret"], reb=7, **FLAT)
    print("\n[口径对照] ext 池 7 自然日 + 5bp 平费率：VT15 full = %.2f%% / %.2f%% / %.2f"
          "（研究基线 16.50%% / -16.51%% / 1.00，reb=5 交易日、权重不漂移）" % (
              m_flat["cagr"] * 100, m_flat["mdd"] * 100, m_flat["calmar"]))

    # 3) bootstrap：14 日 ext，VT15 两档的月度收益差
    s2f, _ = one(close, f, lo, "full", vt=None, reb=14)
    s2t, _ = one(close, f, lo, "topup", vt=None, reb=14)
    vf, _ = one(close, f, lo, "full", vt=0.15, expo_from=s2f["ret"], reb=14)
    vt_, _ = one(close, f, lo, "topup", vt=0.15, expo_from=s2t["ret"], reb=14)
    dates = close.index[lo:]
    for name, a, b in (("S2", s2f, s2t), ("VT15", vf, vt_)):
        ma = E.monthly(a["ret"][lo:], dates, 0)
        mb = E.monthly(b["ret"][lo:], dates, 0)
        ci = E.stationary_bootstrap_ci(ma, mb)
        print("[bootstrap] %s topup−full：ΔCAGR 90%% 区间 %+.2fpp ~ %+.2fpp（中位 %+.2fpp）" % (
            name, ci["cagr_lo"] * 100, ci["cagr_hi"] * 100, ci["cagr_med"] * 100))


if __name__ == "__main__":
    main()
