#!/usr/bin/env python3
"""逐月：研报 PDF 前 2 页 → EPS 预测解析，结果 data/cache_analyst/eps/{YYYY-MM}.csv.gz（gitignored）。"""
import sys, time
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ar_pdf as A

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cache_analyst" / "eps"
OUT.mkdir(parents=True, exist_ok=True)

if __name__ == "__main__":
    months = [str(m) for m in pd.period_range("2017-01", "2026-11", freq="M")]
    if len(sys.argv) > 1 and sys.argv[1] == "rev":
        months = months[::-1]
    for ym in months:
        f = OUT / f"{ym}.csv.gz"
        if f.exists():
            continue
        mf = ROOT / "data/cache_analyst/meta" / f"{ym}.csv.gz"
        while not mf.exists():
            time.sleep(30)
        if ym == "2026-11":
            time.sleep(5)
        t = time.time()
        df = A.run_month(ym, workers=24)
        df.to_csv(f, index=False)
        n = df.infoCode.nunique()
        ok = df[df.how.isin(["table", "text"])].infoCode.nunique()
        print(ym, n, ok, f"{ok / max(n, 1):.0%}", f"{time.time() - t:.0f}s", flush=True)
