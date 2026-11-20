#!/usr/bin/env python3
"""补拉不复权日线（腾讯 newfqkline fq=''），供总收益重建。只拉 panel 里 info=qfq_naive 的代码。
输出 data/cache_ohlc_raw_extra/{code}.csv（gitignored）。"""
import sys, time
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_history_pull as P
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache_ohlc_raw_extra"
OUT.mkdir(parents=True, exist_ok=True)
codes = pd.read_csv(sys.argv[1]).iloc[:, 0].tolist()
ok = fail = 0
for i, c in enumerate(codes):
    p = OUT / f"{c}.csv"
    if p.exists() and p.stat().st_size > 100:
        ok += 1; continue
    try:
        d = P.tx_pull(c, fq="")
        if len(d):
            d.to_csv(p, index=False, float_format="%.6g"); ok += 1
        else:
            fail += 1; print("empty", c, flush=True)
    except Exception as e:  # noqa
        fail += 1; print("fail", c, repr(e)[:120], flush=True)
    if i % 50 == 0: print(i, ok, fail, flush=True)
    time.sleep(0.15)
print("done", ok, fail)
