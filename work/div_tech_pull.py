#!/usr/bin/env python3
"""红利-科技跷跷板：补拉指数（中证红利价格、中证全债、中证TMT、科创50、创业板全收益）。"""
from __future__ import annotations
import sys, time
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_history_pull as P  # noqa: E402

OUT = P.ROOT / "data" / "cache_div_tech"
OUT.mkdir(parents=True, exist_ok=True)
LIST = [("000922", "cs"), ("H11001", "cs"), ("000998", "cs"), ("000688", "cs"), ("H00998", "cs"),
        ("sz399606", "tx"), ("sz399006", "tx"), ("H00688", "cs")]
meta = []
for code, src in LIST:
    try:
        df = P.cs_pull(code) if src == "cs" else P.tx_pull(code, fq="")
        if df.empty: raise RuntimeError("empty")
        df.to_csv(OUT / f"{code}.csv", index=False, float_format="%.8g")
        meta.append(dict(code=code, src=src, status="ok", rows=len(df), start=str(df.date.iloc[0].date()), end=str(df.date.iloc[-1].date())))
    except Exception as e:  # noqa: BLE001
        meta.append(dict(code=code, src=src, status="fail", err=repr(e)[:150]))
    print(meta[-1], flush=True); time.sleep(0.4)
pd.DataFrame(meta).to_csv(OUT / "_meta.csv", index=False)
