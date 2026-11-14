#!/usr/bin/env python3
"""超高波动长历史数据拉取。见 runs/2026-11-14_highvol_stocks/PREREG.md
A 股：腾讯不复权日线 × 新浪后复权因子（乘法）= 总收益价；海外：Yahoo adjclose。"""
from __future__ import annotations
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd, requests

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "2026-11-14_highvol_stocks"
DA = ROOT / "data" / "cache_highvol" / "ashare"
DX = ROOT / "data" / "cache_highvol" / "xmkt"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://finance.sina.com.cn"}
END = "2026-11-13"
XMKT = {
    "指数/ETF": ["^SOX", "^IXIC", "^NDX", "SMH", "ARKK", "KWEB", "XBI", "3033.HK"],
    "美股半导体": ["INTC", "AMD", "MU", "AMAT", "LRCX", "KLAC", "NVDA", "TXN", "ADI", "MCHP", "QCOM", "TSM", "ASML",
              "AVGO", "MRVL", "ON", "TER", "NXPI"],
    "美股科技/互联网": ["AAPL", "MSFT", "ORCL", "CSCO", "AMZN", "EBAY", "ADBE", "INTU", "NFLX", "TSLA", "GOOGL", "META",
                 "BKNG", "JNPR", "AKAM", "CIEN", "VIAV", "SIRI", "NOK", "ERIC"],
    "中概/港股": ["BIDU", "SOHU", "NTES", "JD", "BABA", "0700.HK", "0981.HK", "1211.HK", "0992.HK", "2382.HK", "0175.HK",
              "9988.HK", "1810.HK", "3690.HK"],
}


def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def get(u, js=True, tries=4):
    last = None
    for i in range(tries):
        try:
            r = requests.get(u, headers=H, timeout=40); r.raise_for_status()
            return r.json() if js else r.text
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(1.5 * (i + 1))
    raise last


def sina_list():
    out, page = [], 1
    while True:
        j = get(f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=100&sort=symbol&asc=1&node=hs_a")
        if not j: break
        out += [dict(symbol=r["symbol"], name=r["name"], mktcap=r.get("mktcap")) for r in j]
        page += 1; time.sleep(0.2)
    return pd.DataFrame(out)


def tx(code, s, e, n):
    j = get(f"https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?param={code},day,{s},{e},{n},")
    return ((j.get("data") or {}).get(code) or {}).get("day") or []


def tx_raw_full(code):
    rows, end = [], END
    for _ in range(20):
        k = tx(code, "", end, 2000)
        k = [r for r in k if r[0] <= end]
        if not k: break
        rows = k + rows
        if len(k) < 2000: break
        end = (pd.Timestamp(k[0][0]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = pd.DataFrame([(r[0], r[2], r[5], r[8] if len(r) > 8 else None) for r in rows],
                      columns=["date", "raw_close", "volume", "amount"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ("raw_close", "volume", "amount"): df[c] = pd.to_numeric(df[c], errors="coerce")
    df["amount"] = df["amount"] * 1e4
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def sina_hfq(code):
    t = get(f"https://finance.sina.com.cn/realstock/company/{code}/hfq.js", js=False)
    js = json.loads(t[t.index("{"):t.rindex("}") + 1])
    f = pd.DataFrame(js["data"]); f["d"] = pd.to_datetime(f["d"]); f["f"] = f["f"].astype(float)
    return f.sort_values("d")


def pull_one_a(code):
    out = DA / f"{code}.csv"
    if out.exists() and out.stat().st_size > 1000:
        return dict(code=code, status="cached")
    try:
        if not tx(code, "1990-01-01", "2005-12-31", 1):
            return dict(code=code, status="listed_after_2005")
        df = tx_raw_full(code)
        f = sina_hfq(code)
        df = pd.merge_asof(df, f.rename(columns={"d": "date"}), on="date", direction="backward")
        df["close"] = df["raw_close"] * df["f"]
        df.to_csv(out, index=False, float_format="%.8g")
        return dict(code=code, status="ok", rows=len(df), first=str(df.date.iloc[0].date()),
                    last=str(df.date.iloc[-1].date()), n_factor=len(f))
    except Exception as e:  # noqa: BLE001
        return dict(code=code, status="fail", err=repr(e)[:200])


def pull_ashare():
    lst = sina_list()
    lst.to_csv(RUN / "ashare_list_sina.csv", index=False, encoding="utf-8-sig")
    cand = lst[lst.symbol.str.match(r"^(sh60|sz00)")]
    log(f"hs_a {len(lst)}，sh60/sz00 候选 {len(cand)}")
    meta = []
    with ThreadPoolExecutor(6) as ex:
        for i, r in enumerate(ex.map(pull_one_a, cand.symbol)):
            meta.append(r)
            if i % 100 == 0: log(f"{i} {r}")
    m = pd.DataFrame(meta).merge(cand.rename(columns={"symbol": "code"}), on="code", how="left")
    m.to_csv(RUN / "ashare_pull_meta.csv", index=False, encoding="utf-8-sig")
    log(m.status.value_counts().to_dict())


def yahoo(sym):
    j = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1=0&period2=9999999999&interval=1d&events=div,split")
    r = j["chart"]["result"][0]
    ts = pd.to_datetime(r["timestamp"], unit="s") + pd.Timedelta(seconds=int(r["meta"].get("gmtoffset", 0)))
    q = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
    df = pd.DataFrame({"date": ts.normalize(), "close_px": q["close"], "close": adj, "volume": q["volume"]})
    return df.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)


def pull_xmkt():
    meta = []
    for grp, syms in XMKT.items():
        for s in syms:
            fn = DX / f"{s.replace('^', '_').replace('.', '_')}.csv"
            try:
                df = yahoo(s); df = df[df.date <= pd.Timestamp(END)]
                df.to_csv(fn, index=False, float_format="%.8g")
                meta.append(dict(code=s, group=grp, status="ok", source="yahoo", rows=len(df),
                                 first=str(df.date.iloc[0].date()), last=str(df.date.iloc[-1].date())))
            except Exception as e:  # noqa: BLE001
                meta.append(dict(code=s, group=grp, status="fail", source="yahoo", err=repr(e)[:200]))
            log(meta[-1]); time.sleep(0.4)
    pd.DataFrame(meta).to_csv(RUN / "xmkt_pull_meta.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    w = sys.argv[1] if len(sys.argv) > 1 else "all"
    if w in ("xmkt", "all"): pull_xmkt()
    if w in ("ashare", "all"): pull_ashare()
