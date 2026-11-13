"""把 ETF 池从 53 只扩到"2014 年前就存在"的全集。

步骤：
1) 新浪 ETF 列表（927 只，代码形如 sz159998）
2) 每只打一次新浪全历史（便宜，只为拿首个交易日）→ data/cache_etf_probe.json（可续跑）
3) 首日 <= 2015-06-30 的，用腾讯 qfq 拉全历史 → data/cache_ohlc_long/<code>.csv
   （已在 cache_ohlc/ 里的直接复制，保留真实成交额）
4) 输出清单 data/cache_ohlc_long/_meta.csv + etf_universe.csv（按年份统计覆盖数）
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import akshare as ak
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pull_ohlcv import fetch_etf_tx, log  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OLD = DATA / "cache_ohlc"
NEW = DATA / "cache_ohlc_long"
PROBE = DATA / "cache_etf_probe.json"
NEW.mkdir(parents=True, exist_ok=True)
CUTOFF = "2015-06-30"

probe = json.loads(PROBE.read_text(encoding="utf-8")) if PROBE.exists() else {}


def list_etfs():
    d = ak.fund_etf_category_sina(symbol="ETF基金")
    d = d[["代码", "名称"]].dropna()
    d["代码"] = d["代码"].astype(str).str.strip().str.lower()
    return d[d["代码"].str.match(r"^(sh|sz)\d{6}$")].reset_index(drop=True)


def probe_one(code):
    if code in probe:
        return probe[code]
    info = {"first": None, "n": 0}
    try:
        df = ak.fund_etf_hist_sina(symbol=code)
        if df is not None and len(df):
            info = {"first": str(pd.to_datetime(df["date"]).min().date()),
                    "n": int(len(df))}
    except Exception as e:  # noqa: BLE001
        info["err"] = type(e).__name__
    probe[code] = info
    return info


def main():
    lst = list_etfs()
    log(f"列表 {len(lst)} 只 ETF")
    t0 = time.time()
    for i, row in lst.iterrows():
        probe_one(row["代码"])
        if (i + 1) % 50 == 0:
            PROBE.write_text(json.dumps(probe, ensure_ascii=False), encoding="utf-8")
            log(f"探针 {i + 1}/{len(lst)}  用时 {time.time() - t0:.0f}s")
        time.sleep(0.12)
    PROBE.write_text(json.dumps(probe, ensure_ascii=False), encoding="utf-8")

    rows = []
    for _, r in lst.iterrows():
        p = probe.get(r["代码"], {})
        rows.append(dict(code=r["代码"], name=r["名称"], first=p.get("first"),
                         n=p.get("n", 0), err=p.get("err", "")))
    u = pd.DataFrame(rows)
    u["y"] = u["first"].str[:4]
    log("按首个交易日年份统计：")
    log(u.groupby("y").size().to_string())

    survivors = u[(u["first"].notna()) & (u["first"] <= CUTOFF)].sort_values("first")
    log(f"首日 <= {CUTOFF} 的 {len(survivors)} 只，开始拉腾讯 qfq")

    meta, ok, fail = [], 0, 0
    for i, r in survivors.iterrows():
        code = r["code"]
        dst = NEW / f"{code}.csv"
        src = OLD / f"{code}.csv"
        try:
            if dst.exists():
                ok += 1
            elif src.exists():
                shutil.copyfile(src, dst)
                ok += 1
            else:
                df, srcname = fetch_etf_tx(code)
                if len(df) < 250:
                    fail += 1
                    continue
                df.to_csv(dst, index=False, encoding="utf-8")
                ok += 1
                time.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            log(f"  FAIL {code} {type(e).__name__} {str(e)[:60]}")
            fail += 1
            continue
        d = pd.read_csv(dst, usecols=["date"])
        meta.append(dict(code=code, kind="etf", source="long", rows=len(d),
                         start=d["date"].iloc[0], end=d["date"].iloc[-1],
                         cache_added=time.strftime("%Y-%m-%d"), ratio=1.0))
        if (len(meta) % 20) == 0:
            log(f"  拉取 {len(meta)}/{len(survivors)}（ok {ok} fail {fail}）")

    m = pd.DataFrame(meta)
    if len(m):
        m.to_csv(NEW / "_meta.csv", index=False, encoding="utf-8-sig")
    u.to_csv(DATA / "etf_universe.csv", index=False, encoding="utf-8-sig")
    log(f"完成：写好 {len(m)} 只，失败 {fail}")
    if len(m):
        m["y"] = m["start"].str[:4]
        log(m.groupby("y").size().to_string())


if __name__ == "__main__":
    main()
