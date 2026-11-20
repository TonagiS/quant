#!/usr/bin/env python3
"""总收益面板（2026-11-20 tr_ens_trendgate）。

在 pit_pool_data 基础上把不复权价来源扩到 data/cache_ohlc_raw_extra（本次补拉），
重建 close_tr；其余（close_naive、amount、cat）不变。缓存 data/cache_pit/panel_tr2.pkl（gitignored）。
总收益方法：long_history_backtest.tr_series（拆分分段，r_t = Δqfq_t / (k·raw_{t-1})）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_history_backtest as LH  # noqa: E402
import pit_pool_data as D  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW3 = ROOT / "data" / "cache_ohlc_raw_extra"
PKL2 = ROOT / "data" / "cache_pit" / "panel_tr2.pkl"


def tr_any(code):
    for raw in (D.RAW1, D.RAW2, RAW3):
        if (raw / f"{code}.csv").exists():
            old = LH.RAW
            LH.RAW = raw
            try:
                return LH.tr_series(code)
            finally:
                LH.RAW = old
    return LH.tr_series(code, naive=True)


def build(force=False):
    if PKL2.exists() and not force:
        return pd.read_pickle(PKL2)
    D.tr_any = tr_any
    D.PKL = PKL2
    out = D.build(force=True)
    return out


if __name__ == "__main__":
    P = build(force=True)
    inf = P["info"]
    print(P["close_tr"].shape, "naive:", int(inf.str.startswith("qfq_naive").sum()),
          "trunc:", int(inf.str.contains("trunc_after").sum()), "gapcut:", int(inf.str.contains("gap_cut").sum()))
