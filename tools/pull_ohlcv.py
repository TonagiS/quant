"""全量拉 OHLCV（90 标的）→ data/cache_ohlc/ + 续写旧 cache + 10Y 国债。

数据源（按可达性定，2026-11-14 实测：东财 push2his 被 RST，akshare _em 全部不可用）
- ETF  ：腾讯 web.ifzq.gtimg.cn fqkline（qfq 后复权，800 根/页，含 volume；amount 用 量×收盘 近似，
         若旧 cache 有同日真实成交额则用真实的）
- 指数 ：新浪 stock_zh_index_daily（量有、额无）
- 蓝筹 ：新浪 stock_zh_a_daily(adjust="hfq")（含真实 amount）
- 续写旧 cache：用重叠段的中位比值把新序列缩放到旧口径，再只追加缺失日期，避免口径跳变
- 宏观 ：中债 10Y 国债收益率 → data/cache_macro/china_10y.csv
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "cache_ohlc"
MACRO = DATA / "cache_macro"
OUT.mkdir(parents=True, exist_ok=True)
MACRO.mkdir(parents=True, exist_ok=True)

END = "2026-12-31"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SLEEP = 0.25


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def get(url, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=15, headers=UA)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 + 1.5 * i)
    raise last


def fetch_etf_tx(code):
    """腾讯 qfq，倒序翻页。返回 date,open,high,low,close,volume。"""
    rows, end = [], END
    for _ in range(12):
        u = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
             f"param={code},day,2005-01-01,{end},800,qfq")
        d = get(u).json()["data"][code]
        k = d.get("qfqday") or d.get("day") or []
        if not k:
            break
        rows = k + rows
        if len(k) < 800:
            break
        end = (pd.Timestamp(k[0][0]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        time.sleep(SLEEP)
    df = pd.DataFrame([r[:6] for r in rows],
                      columns=["date", "open", "close", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ("open", "close", "high", "low", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    df["amount"] = df["volume"] * df["close"]
    return df, "tx_qfq"


def fetch_index(code):
    import akshare as ak

    raw = ak.stock_zh_index_daily(symbol=code)
    df = pd.DataFrame({"date": pd.to_datetime(raw["date"]), "open": raw["open"], "high": raw["high"],
                       "low": raw["low"], "close": raw["close"], "volume": raw["volume"]})
    df["amount"] = np.nan
    return df, "sina_index"


def fetch_stock(code):
    import akshare as ak

    raw = ak.stock_zh_a_daily(symbol=code, adjust="hfq")
    df = pd.DataFrame({"date": pd.to_datetime(raw["date"]), "open": raw["open"], "high": raw["high"],
                       "low": raw["low"], "close": raw["close"], "volume": raw["volume"],
                       "amount": raw["amount"]})
    return df, "sina_hfq"


def merge_old_amount(df, cache_path):
    """旧 cache 的真实成交额补进 amount（同日覆盖）。"""
    if not cache_path.exists():
        return df
    old = pd.read_csv(cache_path, parse_dates=["date"])
    if "amount" not in old.columns:
        return df
    m = old[["date", "amount"]].dropna()
    if m.empty:
        return df
    df = df.merge(m, on="date", how="left", suffixes=("", "_old"))
    df["amount"] = df["amount_old"].where(df["amount_old"].notna(), df["amount"])
    return df.drop(columns=["amount_old"])


def refresh_cache(df, cache_path):
    """只追加缺失日期；用重叠段中位比值对齐旧口径。返回新增行数、比值。"""
    old = pd.read_csv(cache_path, parse_dates=["date"])
    ov = old.merge(df[["date", "close"]], on="date", how="inner", suffixes=("_old", "_new"))
    ov = ov[ov["close_new"] > 0].tail(30)
    ratio = float((ov["close_old"] / ov["close_new"]).median()) if len(ov) >= 5 else 1.0
    last = old["date"].max()
    add = df[df["date"] > last].copy()
    if add.empty:
        return 0, ratio
    add["close"] = add["close"] * ratio
    cols = ["date", "close", "amount"] + (["volume"] if "volume" in old.columns else [])
    out = pd.concat([old, add[cols]], ignore_index=True).sort_values("date")
    out.to_csv(cache_path, index=False, float_format="%.6g")
    return len(add), ratio


def classify(fname):
    stem = fname.replace("_20070101", "")
    if stem.startswith("idx_"):
        return stem[4:], "index", "index"
    if stem.startswith("stk_"):
        return stem.split("_")[1], "stock", "stock"
    return stem, "etf", "etf"


def cache_path_of(code, kind):
    if kind == "etf":
        return DATA / "cache2019" / f"{code}.csv"
    sub = "idx" if kind == "index" else "stk"
    return DATA / "cache2007" / f"{sub}_{code}{'_hfq' if kind == 'stock' else ''}_20070101.csv"


def main():
    files = []
    for d in ("cache2019", "cache2007"):
        files += sorted((DATA / d).glob("*.csv"))
    log(f"标的 {len(files)}")

    meta, fails = [], []
    for i, p in enumerate(files, 1):
        code, kind, _ = classify(p.stem)
        try:
            if kind == "etf":
                df, src = fetch_etf_tx(code)
            elif kind == "index":
                df, src = fetch_index(code)
            else:
                df, src = fetch_stock(code)
            if len(df) < 200:
                raise RuntimeError(f"rows={len(df)}")
            cp = cache_path_of(code, kind)
            df = merge_old_amount(df, cp)
            df[["date", "open", "high", "low", "close", "volume", "amount"]].to_csv(
                OUT / f"{code}.csv", index=False, float_format="%.6g")
            added, ratio = refresh_cache(df, cp)
            meta.append(dict(code=code, kind=kind, source=src, rows=len(df),
                             start=str(df["date"].iloc[0].date()), end=str(df["date"].iloc[-1].date()),
                             cache_added=added, ratio=round(ratio, 4)))
            log(f"[{i:02d}/{len(files)}] {code:<8} {src:<10} {len(df):5d}行 "
                f"{df['date'].iloc[-1].date()} +{added} ratio {ratio:.3f}")
        except Exception as e:  # noqa: BLE001
            fails.append((code, repr(e)))
            log(f"[{i:02d}/{len(files)}] {code:<8} FAIL {e!r}")
        time.sleep(SLEEP)

    pd.DataFrame(meta).to_csv(OUT / "_meta.csv", index=False, encoding="utf-8-sig")
    log(f"完成：成功 {len(meta)}，失败 {len(fails)}")
    for c, e in fails:
        log(f"  FAIL {c}: {e}")

    try:
        import akshare as ak
        raw = get("https://yield.chinabond.com.cn/", 1)  # 探活，仅记录
        log(f"中债站点 {raw.status_code}")
    except Exception:
        pass
    try:
        import akshare as ak
        r = ak.bond_zh_us_rate(start_date="20050101")
        col = [c for c in r.columns if "中国国债收益率10年" in c][0]
        m = pd.DataFrame({"date": pd.to_datetime(r["日期"]), "cn10y": pd.to_numeric(r[col], errors="coerce")})
        m = m.dropna().drop_duplicates("date").sort_values("date")
        m.to_csv(MACRO / "china_10y.csv", index=False)
        log(f"10Y 国债 {m['date'].iloc[0].date()}~{m['date'].iloc[-1].date()} 末 {m['cn10y'].iloc[-1]:.3f}")
    except Exception as e:  # noqa: BLE001
        log(f"10Y 国债失败 {e!r}")


if __name__ == "__main__":
    main()
