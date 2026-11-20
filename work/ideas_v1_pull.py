#!/usr/bin/env python3
"""ideas_v1 数据：上交所 ETF 份额（每周最后一个交易日，2012 起）、社融增量（akshare）。存 data/cache_ideas（不入库）。
用法：python3 work/ideas_v1_pull.py shares | /workspace/venv_ak/bin/python work/ideas_v1_pull.py credit"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache_ideas"
B = ("https://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&pageHelp.pageSize=3000"
     "&pageHelp.pageNo=1&pageHelp.beginPage=1&pageHelp.cacheSize=1&pageHelp.endPage=1&isPagination=true")
H = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sse.com.cn/"}


def fetch(d):
    for a in range(5):
        try:
            r = requests.get(B + "&STAT_DATE=" + d, headers=H, timeout=30)
            rows = r.json().get("result") or []
            return d, [(d, "sh" + str(x.get("SEC_CODE")).strip(), float(x.get("TOT_VOL") or 0)) for x in rows]
        except Exception:  # noqa: BLE001
            time.sleep(1.5 * (a + 1))
    return d, None


def shares():
    cal = pd.read_csv(ROOT / "data/cache_index_proxy/H00300.csv", parse_dates=["date"]).date
    cal = cal[(cal >= "2012-01-01") & (cal <= "2026-11-13")]
    wk = cal.groupby(cal.dt.to_period("W")).max()
    days = [d.strftime("%Y-%m-%d") for d in wk]
    rows, miss = [], []
    with ThreadPoolExecutor(6) as p:
        for k, (d, r) in enumerate(p.map(fetch, days)):
            if r is None or not r:
                miss.append(d)
            else:
                rows += r
            if k % 100 == 0:
                print(k, len(days), len(rows), flush=True)
    df = pd.DataFrame(rows, columns=["date", "code", "shares_wan"])
    df.to_csv(OUT / "sse_etf_shares_weekly.csv", index=False)
    print("rows", len(df), "weeks", df.date.nunique(), "miss", len(miss), miss[:10])


def credit():
    import akshare as ak
    d = ak.macro_china_social_financing()
    d.to_csv(OUT / "social_financing.csv", index=False)
    print(d.shape, d["月份"].min(), d["月份"].max())


if __name__ == "__main__":
    {"shares": shares, "credit": credit}[sys.argv[1]]()
