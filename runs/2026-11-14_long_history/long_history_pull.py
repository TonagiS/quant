#!/usr/bin/env python3
"""长历史数据：截断 ETF 全量重拉 + 指数代理。见 runs/2026-11-14_long_history/PREREG.md"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
import pandas as pd, requests

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "2026-11-14_long_history"
OLD = ROOT / "data" / "cache_ohlc_full"
NEW = ROOT / "data" / "cache_ohlc_fullhist"
IDX = ROOT / "data" / "cache_index_proxy"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://www.csindex.com.cn/"}

INDEX = [  # code, source, name, ret_type, sector, lane
    ("H00300", "cs", "沪深300全收益", "全收益", "宽基", "active"),
    ("H00905", "cs", "中证500全收益", "全收益", "宽基", "active"),
    ("H00016", "cs", "上证50全收益", "全收益", "宽基", "active"),
    ("000852", "cs", "中证1000", "价格", "宽基", "active"),
    ("sz399006", "tx", "创业板指", "价格", "宽基", "active"),
    ("H00922", "cs", "中证红利全收益", "全收益", "红利/低波", "slow"),
    ("399986", "cs", "中证银行", "价格", "银行", "slow"),
    ("H30184", "cs", "中证半导体", "价格", "芯片/半导体", "active"),
    ("931865", "cs", "中证半导体产业", "价格", "芯片/半导体", "active"),
    ("000685", "cs", "科创芯片", "价格", "芯片/半导体", "active"),
    ("sz980017", "tx", "国证芯片", "价格", "芯片/半导体", "active"),
    ("931743", "cs", "半导体材料设备", "价格", "半导体设备", "active"),
    ("H30590", "cs", "中证机器人", "价格", "机器人", "active"),
    ("399971", "cs", "中证传媒", "价格", "传媒", "active"),
    ("930707", "cs", "中证畜牧", "价格", "养殖", "active"),
    ("931160", "cs", "中证通信设备(非CPO)", "价格", "通信设备", "active"),
    ("399967", "cs", "中证军工", "价格", "军工", "active"),
    ("930713", "cs", "CS人工智能", "价格", "人工智能", "active"),
    ("sh518880", "txq", "黄金ETF华安(代理)", "ETF前复权", "黄金", "active"),
]


def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def get(u, tries=4):
    last = None
    for i in range(tries):
        try:
            r = requests.get(u, headers=UA, timeout=40); r.raise_for_status(); return r.json()
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(1.5 * (i + 1))
    raise last


def tx_pull(code, fq="qfq", start="2000-01-01", end="2026-12-31"):
    rows = []
    for _ in range(20):
        j = get(f"https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?param={code},day,{start},{end},800,{fq}")
        b = (j.get("data") or {}).get(code) or {}
        k = b.get("qfqday") or b.get("day") or []
        if not k: break
        rows = k + rows
        if len(k) < 800: break
        end = (pd.Timestamp(k[0][0]) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        time.sleep(0.2)
    recs = []
    for r in rows:
        amt = None
        if len(r) >= 9 and r[8] not in ("", None, {}):
            try: amt = float(r[8]) * 1e4
            except (TypeError, ValueError): amt = None
        recs.append((r[0], r[1], r[3], r[4], r[2], r[5], amt))
    df = pd.DataFrame(recs, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    if df.empty: return df
    df["date"] = pd.to_datetime(df["date"])
    for c in df.columns[1:]: df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    df["amount"] = df["amount"].fillna(df["volume"] * df["close"] * 100)
    return df


def cs_pull(code):
    j = get(f"https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode={code}&startDate=19900101&endDate=20261113")
    d = pd.DataFrame(j.get("data") or [])
    if d.empty: return d
    d = d[d.tradeDate != "19900101"]
    df = pd.DataFrame({"date": pd.to_datetime(d.tradeDate), "close": pd.to_numeric(d.close, errors="coerce")})
    for c in ("open", "high", "low"): df[c] = pd.to_numeric(d[c], errors="coerce").to_numpy()
    df["volume"] = 0.0
    df["amount"] = pd.to_numeric(d.tradingValue, errors="coerce").to_numpy() * 1e8
    return df.dropna(subset=["close"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)


def pull_etfs():
    meta = []
    files = sorted(OLD.glob("*.csv"))
    for p in files:
        first = pd.read_csv(p, usecols=["date"], nrows=1).date.iloc[0]
        if first != "2017-01-03": continue
        code = p.stem
        out = NEW / f"{code}.csv"
        if out.exists() and out.stat().st_size > 100:
            df = pd.read_csv(out, parse_dates=["date"]); status = "cached"
        else:
            try:
                df = tx_pull(code); status = "ok" if len(df) else "empty"
                if len(df): df.to_csv(out, index=False, float_format="%.6g")
            except Exception as e:  # noqa: BLE001
                meta.append(dict(tx_code=code, status="fail", err=repr(e)[:200])); continue
        old = pd.read_csv(p, parse_dates=["date"])
        m = old.merge(df, on="date", suffixes=("_o", "_n"))
        ratio = (m.close_n / m.close_o)
        meta.append(dict(tx_code=code, status=status, rows=len(df), start=str(df.date.iloc[0].date()) if len(df) else "",
                         end=str(df.date.iloc[-1].date()) if len(df) else "", overlap=len(m),
                         overlap_ratio_med=float(ratio.median()) if len(m) else None,
                         overlap_ratio_maxdev=float((ratio / ratio.median() - 1).abs().max()) if len(m) else None,
                         nonpos_close=int((df.close <= 0).sum()) if len(df) else 0))
        log(f"ETF {code} {status} {meta[-1].get('start')} rows={meta[-1].get('rows')} maxdev={meta[-1].get('overlap_ratio_maxdev')}")
        time.sleep(0.15)
    pd.DataFrame(meta).to_csv(RUN / "pull_etf_meta.csv", index=False, encoding="utf-8-sig")


def pull_index():
    meta = []
    for code, src, name, rt, sec, lane in INDEX:
        try:
            if src == "cs": df = cs_pull(code)
            elif src == "tx": df = tx_pull(code, fq="")
            else: df = tx_pull(code, fq="qfq")
            if df.empty: raise RuntimeError("empty")
            df.to_csv(IDX / f"{code}.csv", index=False, float_format="%.8g")
            meta.append(dict(code=code, source=src, name=name, ret_type=rt, sector=sec, lane=lane, status="ok",
                             rows=len(df), start=str(df.date.iloc[0].date()), end=str(df.date.iloc[-1].date()), err=""))
        except Exception as e:  # noqa: BLE001
            meta.append(dict(code=code, source=src, name=name, ret_type=rt, sector=sec, lane=lane, status="fail",
                             rows=0, start="", end="", err=repr(e)[:200]))
        log(f"IDX {meta[-1]}")
        time.sleep(0.4)
    pd.DataFrame(meta).to_csv(RUN / "index_meta.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("index", "all"): pull_index()
    if what in ("etf", "all"): pull_etfs()
