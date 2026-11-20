#!/usr/bin/env python3
"""分析师研报元数据（东方财富 reportapi，个股研报 qType=0），按月全市场分页拉取。
缓存 data/cache_analyst/meta/YYYY-MM.csv.gz（gitignored）。免费接口最早只有 2017-01。"""
import json, sys, time
from pathlib import Path
import pandas as pd, requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache_analyst" / "meta"
OUT.mkdir(parents=True, exist_ok=True)
U = "https://reportapi.eastmoney.com/report/list"
KEEP = ["infoCode", "publishDate", "stockCode", "stockName", "orgCode", "orgSName", "title", "emRatingName",
        "emRatingValue", "lastEmRatingName", "lastEmRatingValue", "ratingChange", "indvInduCode", "indvInduName",
        "indvAimPriceT", "indvAimPriceL", "attachPages", "attachSize", "researcher",
        "predictLastYearEps", "predictThisYearEps", "predictNextYearEps", "predictNextTwoYearEps"]


def get(p, tries=5):
    for k in range(tries):
        try:
            return requests.get(U, params=p, timeout=30).json()
        except Exception:
            time.sleep(2 * (k + 1))
    raise RuntimeError(p)


def month(b, e):
    base = {"industryCode": "*", "pageSize": "100", "industry": "*", "rating": "*", "ratingChange": "*",
            "beginTime": b, "endTime": e, "fields": "", "qType": "0", "orgCode": "", "rcode": ""}
    rows, pg, tp = [], 1, 1
    while pg <= tp:
        p = dict(base, pageNo=str(pg), p=str(pg), pageNum=str(pg), pageNumber=str(pg))
        r = get(p)
        tp = r.get("TotalPage") or 0
        rows += r.get("data") or []
        pg += 1
    df = pd.DataFrame(rows)
    return df[[c for c in KEEP if c in df.columns]] if len(df) else df, r.get("hits")


if __name__ == "__main__":
    months = pd.period_range("2017-01", "2026-11", freq="M")
    for m in months:
        f = OUT / f"{m}.csv.gz"
        if f.exists() and str(m) != "2026-11":
            continue
        b, e = m.start_time.strftime("%Y-%m-%d"), m.end_time.strftime("%Y-%m-%d")
        df, hits = month(b, e)
        df = df.drop_duplicates("infoCode") if len(df) else df
        df.to_csv(f, index=False)
        print(m, hits, len(df), flush=True)
