#!/usr/bin/env python3
"""PIT 规则池 / 分散臂的数据面板（2026-11-20）。

- ETF 总收益：复用 long_history 的减法前复权修正（tr_series，需不复权价；fullhist_raw 或 raw_2017）。
  拿不到不复权价的，退回 qfq pct_change（标记 qfq_naive）。
- 成交额：qfq 缓存里的 amount 字段（腾讯源成交额，人民币）。
- 类别：runs/2026-11-14_fullmkt_single_asset/universe_all.csv。
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_history_backtest as LH  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "data" / "cache_ohlc_full"
FH = ROOT / "data" / "cache_ohlc_fullhist"
RAW1 = ROOT / "data" / "cache_ohlc_fullhist_raw"
RAW2 = ROOT / "data" / "cache_ohlc_raw_2017"
UNI = ROOT / "runs" / "2026-11-14_fullmkt_single_asset" / "universe_all.csv"
END = "2026-11-13"
PKL = ROOT / "data" / "cache_pit" / "panel.pkl"


def universe():
    u = pd.read_csv(UNI)
    return dict(zip(u.tx_code, u.category)), dict(zip(u.tx_code, u.name))


def qfq_path(code):
    p = FH / f"{code}.csv"
    return p if p.exists() else FULL / f"{code}.csv"


def tr_any(code):
    if (RAW1 / f"{code}.csv").exists():
        LH.RAW = RAW1
        return LH.tr_series(code)
    if (RAW2 / f"{code}.csv").exists():
        LH.RAW = RAW2
        try:
            return LH.tr_series(code)
        finally:
            LH.RAW = RAW1
    return LH.tr_series(code, naive=True)


def build(codes=None, force=False):
    """返回 dict(close_tr, close_naive, amount, info)：列=代码，行=交易日。"""
    if PKL.exists() and not force and codes is None:
        return pd.read_pickle(PKL)
    cat, _ = universe()
    if codes is None:
        codes = sorted({os.path.basename(p)[:-4] for p in glob.glob(str(FULL / "*.csv"))}
                       | {os.path.basename(p)[:-4] for p in glob.glob(str(FH / "*.csv"))})
    tr, nv, am, info = {}, {}, {}, {}
    for c in codes:
        q = pd.read_csv(qfq_path(c), parse_dates=["date"]).drop_duplicates("date").sort_values("date")
        q = q[q.date <= pd.Timestamp(END)].set_index("date")
        am[c] = q["amount"].astype(float)
        qq = q.close[q.close > 0]
        nv[c] = qq
        df, inf = tr_any(c)
        info[c] = inf
        if df is not None and len(df):
            tr[c] = df.set_index("date")["close"]
    out = {"close_tr": pd.DataFrame(tr).sort_index(), "close_naive": pd.DataFrame(nv).sort_index(),
           "amount": pd.DataFrame(am).sort_index(), "info": pd.Series(info), "cat": cat}
    if codes is not None and len(codes) > 500:
        pd.to_pickle(out, PKL)
    return out


if __name__ == "__main__":
    P = build(force=True)
    inf = P["info"]
    print(P["close_tr"].shape, (inf.str.startswith("qfq_naive")).sum(), "naive")
