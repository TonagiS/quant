#!/usr/bin/env python3
"""分析师盈利预测修正信号（点时、免费数据重建）。

1) 研报级修正：同一机构对同一股票，本篇与它此前 ≤PREV 天内最近一篇的同一会计年度 EPS 比较
   （FY1 = 发布年、FY2 = 发布年+1，两年都有取均值）；rel = (新-旧)/max(|旧|,0.05)，截 ±1；
   up: rel>+0.5%，down: rel<-0.5%。
2) 股票级（每周五，只用发布日 < 该日的研报）：窗口 W 天内  diff=(U-D)/N，mrev=mean(rel)。
3) ETF 级：季报前十大持仓（季末+LAG 天后才可见）按占净值比例加权；有信号权重 <30% 记 NaN。
缓存 data/cache_analyst/sig_*.pkl（gitignored）。
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CA = ROOT / "data" / "cache_analyst"
PREV = 180
HOLD_LAG = 30


def load_reports():
    pk = CA / "reports.pkl"
    if pk.exists():
        return pd.read_pickle(pk)
    metas, epss = [], []
    for f in sorted((CA / "meta").glob("*.csv.gz")):
        metas.append(pd.read_csv(f, dtype=str, usecols=lambda c: c in (
            "infoCode", "publishDate", "stockCode", "orgCode", "orgSName", "indvInduName", "emRatingValue",
            "lastEmRatingValue", "ratingChange")))
        e = CA / "eps" / f.name
        if e.exists():
            epss.append(pd.read_csv(e, dtype={"infoCode": str}))
    M = pd.concat(metas).drop_duplicates("infoCode")
    M["date"] = pd.to_datetime(M.publishDate.str[:10])
    M["stockCode"] = M.stockCode.str.zfill(6)
    Ep = pd.concat(epss)
    out = dict(meta=M, eps=Ep)
    pd.to_pickle(out, pk)
    return out


def report_revisions(R):
    M, Ep = R["meta"], R["eps"]
    Ep = Ep.dropna(subset=["fy", "eps"]).copy()
    Ep["fy"] = Ep.fy.astype(int)
    X = Ep.merge(M[["infoCode", "date", "stockCode", "orgCode"]], on="infoCode")
    X = X[(X.fy == X.date.dt.year) | (X.fy == X.date.dt.year + 1)]
    X = X.sort_values(["stockCode", "orgCode", "fy", "date"]).drop_duplicates(["stockCode", "orgCode", "fy", "date"], keep="last")
    g = X.groupby(["stockCode", "orgCode", "fy"])
    X["prev_eps"] = g.eps.shift(1)
    X["prev_date"] = g.date.shift(1)
    X = X[X.prev_eps.notna() & ((X.date - X.prev_date).dt.days <= PREV) & ((X.date - X.prev_date).dt.days >= 1)]
    X["rel"] = ((X.eps - X.prev_eps) / np.maximum(X.prev_eps.abs(), 0.05)).clip(-1, 1)
    r = X.groupby(["infoCode", "stockCode", "orgCode", "date"]).rel.mean().reset_index()
    r["up"] = (r.rel > 0.005).astype(int)
    r["dn"] = (r.rel < -0.005).astype(int)
    return r


def stock_signal(rv, W=90, freq="W-FRI", start="2017-01-06", end="2026-11-20"):
    grid = pd.date_range(start, end, freq=freq)
    rv = rv.sort_values("date")
    codes = sorted(rv.stockCode.unique())
    ci = {c: k for k, c in enumerate(codes)}
    d = rv.date.values.astype("datetime64[D]")
    sc = rv.stockCode.map(ci).values
    up, dn, rel = rv.up.values, rv.dn.values, rv.rel.values
    DIFF = np.full((len(grid), len(codes)), np.nan)
    MREV = np.full((len(grid), len(codes)), np.nan)
    NN = np.zeros((len(grid), len(codes)))
    for i, t in enumerate(grid.values.astype("datetime64[D]")):
        a, b = np.searchsorted(d, t - np.timedelta64(W, "D"), "left"), np.searchsorted(d, t, "left")  # 发布日 < t
        if b <= a:
            continue
        s = sc[a:b]
        n = np.bincount(s, minlength=len(codes)).astype(float)
        u = np.bincount(s, up[a:b], minlength=len(codes))
        dd = np.bincount(s, dn[a:b], minlength=len(codes))
        rr = np.bincount(s, rel[a:b], minlength=len(codes))
        ok = n > 0
        DIFF[i, ok] = (u[ok] - dd[ok]) / n[ok]
        MREV[i, ok] = rr[ok] / n[ok]
        NN[i] = n
    mk = lambda A: pd.DataFrame(A, index=grid, columns=codes)
    return {"diff": mk(DIFF), "mrev": mk(MREV), "n": mk(NN)}


def load_holdings(codes):
    rows = []
    for c in codes:
        f = CA / "hold" / f"{c}.csv.gz"
        if not f.exists():
            continue
        h = pd.read_csv(f, dtype={"股票代码": str})
        if not len(h):
            continue
        q = h["季度"].str.extract(r"(\d{4})年(\d)季度")
        h["qend"] = pd.PeriodIndex.from_fields(year=q[0].astype(int), quarter=q[1].astype(int), freq="Q").end_time.normalize()
        h["w"] = pd.to_numeric(h["占净值比例"], errors="coerce")
        h["etf"] = c
        h = h[h["股票代码"].str.fullmatch(r"\d{6}")]
        h = h.sort_values("w", ascending=False).groupby(["etf", "qend"]).head(10)
        rows.append(h[["etf", "qend", "股票代码", "w"]].rename(columns={"股票代码": "stock"}))
    H = pd.concat(rows)
    H["avail"] = H.qend + pd.Timedelta(days=HOLD_LAG)
    return H


def etf_signal(S, H, key="diff", min_cov=0.3):
    """S: 股票级 dict；H: 持仓表。返回 周频 × ETF 的加权信号。"""
    A = S[key]
    grid = A.index
    out = {}
    cols = {c: k for k, c in enumerate(A.columns)}
    Av = A.to_numpy()
    for etf, h in H.groupby("etf"):
        qs = sorted(h.avail.unique())
        res = np.full(len(grid), np.nan)
        for qi, av in enumerate(qs):
            nxt = qs[qi + 1] if qi + 1 < len(qs) else pd.Timestamp("2100-01-01")
            m = (grid >= av) & (grid < nxt)
            if not m.any():
                continue
            hh = h[h.avail == av]
            idx = [cols.get(s, -1) for s in hh.stock]
            w = hh.w.to_numpy(float)
            tot = np.nansum(w)
            if tot <= 0:
                continue
            ok = np.array([k >= 0 for k in idx])
            if not ok.any():
                continue
            sub = Av[np.ix_(np.flatnonzero(m), np.array(idx)[ok])]
            ww = w[ok]
            have = ~np.isnan(sub)
            num = np.nansum(sub * ww, axis=1)
            den = (have * ww).sum(axis=1)
            v = np.where(den / tot >= min_cov, num / np.where(den > 0, den, 1), np.nan)
            res[np.flatnonzero(m)] = v
        out[etf] = res
    return pd.DataFrame(out, index=grid)


if __name__ == "__main__":
    R = load_reports()
    rv = report_revisions(R)
    print(len(R["meta"]), R["eps"].infoCode.nunique(), len(rv), rv.date.min(), rv.date.max())
    print(rv[["rel", "up", "dn"]].describe())
