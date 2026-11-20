#!/usr/bin/env python3
"""components 数据：中证转债/中证短融指数、沪深300 PE(TTM)、10年国债收益率、DAX/XOP、华宝油气 162411。存 data/cache_components（不入库）。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_history_pull as LP  # noqa: E402
import highvol_pull as HP  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache_components"
RUN = ROOT / "runs" / "2026-11-20_components"


def cs_full(code):
    j = LP.get(f"https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode={code}&startDate=20020101&endDate=20261120")
    d = pd.DataFrame(j.get("data") or [])
    d = d[d.tradeDate != "20020101"] if len(d) > 1 else d
    return pd.DataFrame({"date": pd.to_datetime(d.tradeDate), "close": pd.to_numeric(d.close, errors="coerce"),
                         "pe": pd.to_numeric(d.get("peg"), errors="coerce")}).dropna(subset=["close"]).drop_duplicates("date").sort_values("date")


def cgb10():
    rows, page = [], 1
    while True:
        u = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_WEB_TREASURYYIELD&columns=SOLAR_DATE,EMM00166466"
             f"&sortColumns=SOLAR_DATE&sortTypes=-1&pageNumber={page}&pageSize=500")
        j = requests.get(u, headers={"User-Agent": "Mozilla/5.0"}, timeout=40).json()
        data = (j.get("result") or {}).get("data") or []
        rows += data
        if page >= (j.get("result") or {}).get("pages", 1):
            break
        page += 1
        time.sleep(0.3)
    d = pd.DataFrame(rows)
    return pd.DataFrame({"date": pd.to_datetime(d.SOLAR_DATE), "y10": pd.to_numeric(d.EMM00166466, errors="coerce")}).dropna().drop_duplicates("date").sort_values("date")


def main():
    meta = []
    for code in ("000832", "H11014", "000300"):
        df = cs_full(code)
        df.to_csv(OUT / f"{code}.csv", index=False)
        meta.append({"what": code, "source": "csindex", "rows": len(df), "start": str(df.date.iloc[0].date()), "end": str(df.date.iloc[-1].date()),
                     "pe_first": str(df.dropna(subset=["pe"]).date.iloc[0].date()) if df.pe.notna().any() else ""})
    y = cgb10()
    y.to_csv(OUT / "cgb10y.csv", index=False)
    meta.append({"what": "cgb10y(EMM00166466)", "source": "eastmoney", "rows": len(y), "start": str(y.date.iloc[0].date()), "end": str(y.date.iloc[-1].date())})
    for sym, nm in (("^GDAXI", "GDAXI"), ("XOP", "XOP")):
        df = HP.yahoo(sym)
        df.to_csv(OUT / f"{nm}.csv", index=False)
        meta.append({"what": nm, "source": "yahoo", "rows": len(df), "start": str(df.date.iloc[0].date()), "end": str(df.date.iloc[-1].date())})
    for fq in ("qfq", ""):
        df = LP.tx_pull("sz162411", fq=fq)
        df.to_csv(OUT / f"sz162411{'_raw' if fq == '' else ''}.csv", index=False)
        meta.append({"what": f"sz162411 fq={fq or 'raw'}", "source": "tencent", "rows": len(df), "start": str(df.date.iloc[0].date()), "end": str(df.date.iloc[-1].date())})
    m = pd.DataFrame(meta)
    m.to_csv(RUN / "data_meta.csv", index=False)
    print(m.to_string())


if __name__ == "__main__":
    main()
