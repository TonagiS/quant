#!/usr/bin/env python3
"""缩量回踩+放量突破：全A股（沪深主板/创业板/科创板，现存上市公司）日线 OHLCV 拉取。
腾讯不复权日线(open/close/high/low/volume/amount) × 新浪后复权因子（乘法）。
注意：新浪 hs_a 列表只含现存股票 → 存在幸存者偏差（退市股缺失）。见 runs/2026-11-15_vol_breakout/PREREG.md"""
from __future__ import annotations
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd, requests

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "2026-11-15_vol_breakout"
DA = ROOT / "data" / "cache_vb" / "ashare"
DB = ROOT / "data" / "cache_vb" / "bench"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://finance.sina.com.cn"}
END = "2026-11-13"


def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def get(u, js=True, tries=5):
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


def tx(code, e, n):
    j = get(f"https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?param={code},day,,{e},{n},")
    return ((j.get("data") or {}).get(code) or {}).get("day") or []


def tx_raw_full(code):
    rows, end = [], END
    for _ in range(20):
        k = [r for r in tx(code, end, 2000) if r[0] <= end]
        if not k: break
        rows = k + rows
        if len(k) < 2000: break
        end = (pd.Timestamp(k[0][0]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = pd.DataFrame([(r[0], r[1], r[2], r[3], r[4], r[5], r[8] if len(r) > 8 else None) for r in rows],
                      columns=["date", "o", "c", "h", "l", "volume", "amount"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ("o", "c", "h", "l", "volume", "amount"): df[c] = pd.to_numeric(df[c], errors="coerce")
    df["amount"] = df["amount"] * 1e4
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def sina_hfq(code):
    t = get(f"https://finance.sina.com.cn/realstock/company/{code}/hfq.js", js=False)
    js = json.loads(t[t.index("{"):t.rindex("}") + 1])
    f = pd.DataFrame(js["data"]); f["d"] = pd.to_datetime(f["d"]); f["f"] = f["f"].astype(float)
    return f.sort_values("d")


def pull_one(code):
    out = DA / f"{code}.csv"
    if out.exists() and out.stat().st_size > 500:
        return dict(code=code, status="cached")
    try:
        df = tx_raw_full(code)
        if df.empty: return dict(code=code, status="empty")
        try:
            f = sina_hfq(code)
            df = pd.merge_asof(df, f.rename(columns={"d": "date"}), on="date", direction="backward")
            df["f"] = df["f"].fillna(1.0); fs = "sina"
        except Exception:  # noqa: BLE001
            df["f"] = 1.0; fs = "none"
        df.to_csv(out, index=False, float_format="%.8g")
        return dict(code=code, status="ok", factor=fs, rows=len(df), first=str(df.date.iloc[0].date()),
                    last=str(df.date.iloc[-1].date()))
    except Exception as e:  # noqa: BLE001
        return dict(code=code, status="fail", err=repr(e)[:200])


def pull_bench():
    for code in ("sh000300", "sh000985"):
        df = tx_raw_full(code); df.to_csv(DB / f"{code}.csv", index=False, float_format="%.8g")
        log(f"bench {code} {len(df)} {df.date.iloc[0].date()}~{df.date.iloc[-1].date()}")


if __name__ == "__main__":
    pull_bench()
    lp = RUN / "ashare_list_sina.csv"
    lst = sina_list() if not lp.exists() else pd.read_csv(lp)
    lst.to_csv(lp, index=False, encoding="utf-8-sig")
    cand = lst[lst.symbol.str.match(r"^(sh60|sh68|sz00|sz30)")]
    log(f"hs_a {len(lst)}，沪深A候选 {len(cand)}")
    meta = []
    with ThreadPoolExecutor(int(sys.argv[1]) if len(sys.argv) > 1 else 8) as ex:
        for i, r in enumerate(ex.map(pull_one, cand.symbol)):
            meta.append(r)
            if i % 200 == 0: log(f"{i} {r}")
    m = pd.DataFrame(meta).merge(cand.rename(columns={"symbol": "code"}), on="code", how="left")
    m.to_csv(RUN / "pull_meta.csv", index=False, encoding="utf-8-sig")
    log(m.status.value_counts().to_dict())
