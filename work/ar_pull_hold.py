#!/usr/bin/env python3
"""PIT 池 189 只 ETF 的季报持仓（akshare fund_portfolio_hold_em，东财基金 F10），2016–2026。
缓存 data/cache_analyst/hold/{code}.csv.gz。季报披露滞后，使用时按 季末+1个月 才可见。"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import pandas as pd
import akshare as ak

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache_analyst" / "hold"
OUT.mkdir(parents=True, exist_ok=True)


def one(code):
    f = OUT / f"{code}.csv.gz"
    if f.exists():
        return code, -1
    parts = []
    for y in range(2016, 2027):
        for k in range(3):
            try:
                d = ak.fund_portfolio_hold_em(symbol=code[2:], date=str(y))
                break
            except Exception:
                d = None
                time.sleep(2)
        if d is not None and len(d):
            parts.append(d)
    df = pd.concat(parts) if parts else pd.DataFrame(columns=["股票代码", "股票名称", "占净值比例", "季度"])
    df.to_csv(f, index=False)
    return code, len(df)


if __name__ == "__main__":
    ever = pd.read_pickle(ROOT / "data/cache_recombine/base.pkl")["ever"]
    with ThreadPoolExecutor(4) as ex:
        for code, n in ex.map(one, ever):
            print(code, n, flush=True)
