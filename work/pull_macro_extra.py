"""补拉可得的"宏观/情绪"数据（期权历史拿不到，放弃；见 REPORT）。

产出（data/cache_macro/）：
- futures_basis.csv : IF/IC/IH 主力连续收盘 + 对应现货（沪深300/中证500/上证50），年化基差
- margin_sse.csv    : 沪市融资融券余额/买入额（2010+，深市接口需逐日，放弃）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MACRO = ROOT / "data" / "cache_macro"
MACRO.mkdir(parents=True, exist_ok=True)
OHLC = ROOT / "data" / "cache_ohlc"

PAIRS = {"IF0": "sh000300", "IC0": "sh000905", "IH0": "sh000016"}


def log(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def main():
    out = {}
    for fut, spot in PAIRS.items():
        try:
            f = ak.futures_main_sina(symbol=fut)[["日期", "收盘价", "成交量", "持仓量"]]
            f["日期"] = pd.to_datetime(f["日期"])
            f = f.rename(columns={"收盘价": "fut", "成交量": "fut_vol", "持仓量": "fut_oi"})
            s = pd.read_csv(OHLC / f"{spot}.csv", usecols=["date", "close"], parse_dates=["date"])
            s = s.rename(columns={"close": "spot"})
            m = f.merge(s, left_on="日期", right_on="date", how="inner").drop(columns=["date"])
            m["basis"] = m["fut"] / m["spot"] - 1
            m["basis_ann"] = m["basis"] * 252 / 30     # 近似：按月合约 30 天年化
            m["pair"] = fut
            out[fut] = m
            log(f"{fut}: {len(m)} 行  {m['日期'].min().date()} ~ {m['日期'].max().date()}  "
                f"基差中位 {m['basis'].median() * 100:+.2f}%")
            time.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            log(f"{fut} FAIL {type(e).__name__} {str(e)[:80]}")
    if out:
        df = pd.concat(out.values()).sort_values(["pair", "日期"])
        df.to_csv(MACRO / "futures_basis.csv", index=False, encoding="utf-8-sig")
        log(f"futures_basis.csv 写好 {len(df)} 行")

    try:
        mg = ak.stock_margin_sse(start_date="20100101", end_date="20261231")
        mg["信用交易日期"] = pd.to_datetime(mg["信用交易日期"])
        mg = mg.rename(columns={"信用交易日期": "date"}).sort_values("date")
        mg.to_csv(MACRO / "margin_sse.csv", index=False, encoding="utf-8-sig")
        log(f"margin_sse.csv {len(mg)} 行 {mg['date'].min().date()} ~ {mg['date'].max().date()}")
    except Exception as e:  # noqa: BLE001
        log(f"margin FAIL {type(e).__name__} {str(e)[:80]}")


if __name__ == "__main__":
    main()
