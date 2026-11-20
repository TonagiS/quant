#!/usr/bin/env python3
"""components 公共部分：核心资产收益、核心目标权重（IV/EW/ERC + C3 乘数）、卫星变体、风控叠加。规则见 runs/2026-11-20_components/FREEZE.md。"""
from __future__ import annotations

import shutil
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recombine_core as RC  # noqa: E402
import tr_ens_data as TD  # noqa: E402
import long_history_backtest as LH  # noqa: E402
from fullmkt_single_asset import sector_tag  # noqa: E402

PB, E, TP = RC.PB, RC.E, RC.TP
ROOT = RC.ROOT
RUN = ROOT / "runs" / "2026-11-20_components"
CC = ROOT / "data" / "cache_components"
END = RC.END
START_W, SUB_W, CRASH = RC.START_W, RC.SUB_W, RC.CRASH

BASE8 = [c for c, _, _, _ in PB.DIV]
EQ8 = [c for c, _, _, e in PB.DIV if e]
BOND = "sh511010"
EXTRA = [("sh511380", ("cs", CC / "000832.csv", "close"), False),
         ("sh511360", ("cs", CC / "H11014.csv", "close"), False),
         ("sh513520", ("us", ROOT / "data/cache_cross_market/N225.csv", "adjclose"), True),
         ("sh513030", ("us", CC / "GDAXI.csv", "close"), True),
         ("sz162411", ("us", CC / "XOP.csv", "close"), True)]
ALL13 = BASE8 + [c for c, _, _ in EXTRA]
EQ13 = EQ8 + [c for c, _, e in EXTRA if e]

_S = {}


def panel():
    if "P" not in _S:
        _S["P"] = TD.build()
    return _S["P"]


def tr_162411():
    q, r = CC / "qfq", CC / "raw"
    q.mkdir(exist_ok=True); r.mkdir(exist_ok=True)
    shutil.copy(CC / "sz162411.csv", q / "sz162411.csv")
    shutil.copy(CC / "sz162411_raw.csv", r / "sz162411.csv")
    o1, o2, o3 = LH.QFQ_NEW, LH.QFQ_OLD, LH.RAW
    LH.QFQ_NEW, LH.QFQ_OLD, LH.RAW = q, q, r
    try:
        df, info = LH.tr_series("sz162411")
    finally:
        LH.QFQ_NEW, LH.QFQ_OLD, LH.RAW = o1, o2, o3
    s = df.set_index("date")["close"] if "date" in df.columns else df["close"]
    return s, info


def core_returns():
    """13 只资产日收益（cal 对齐）：BASE8 用 PB.div_returns；EXTRA 同样方法拼接。"""
    if "R13" in _S:
        return _S["R13"]
    D = RC.data()
    cal = D["cal"]
    P = panel()
    out = {c: D["Rd"][c] for c in BASE8}
    first = {}
    for code, (kind, path, col), eq in EXTRA:
        if code == "sz162411":
            c, info = tr_162411()
            first["sz162411_info"] = info
        else:
            c = P["close_tr"][code].dropna()
        c = c[c.index <= END]
        r_etf = c.pct_change().iloc[1:].iloc[20:]
        d = pd.read_csv(path, parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
        px = d[col].astype(float)
        px = px[px > 0]
        pr = px.reindex(cal.union(px.index)).ffill().reindex(cal)
        pr = pr[(pr.index >= px.index.min()) & (pr.index <= px.index.max())]
        r = pd.Series(np.nan, index=cal)
        r.loc[pr.index] = pr.pct_change()
        r.loc[r_etf.index.intersection(cal)] = r_etf.reindex(r_etf.index.intersection(cal))
        if len(r_etf):
            r[(r.index >= r_etf.index[0]) & ~r.index.isin(r_etf.index)] = 0.0
        out[code] = r
        first[code] = str(r_etf.index[0].date()) if len(r_etf) else None
    R = pd.DataFrame(out)[ALL13]
    _S["R13"], _S["first"] = R, first
    return R


def erp_mult():
    """C3：m_t = 0.5 + 扩张百分位(ERP)，有效观测 <250 时 p=0.5。返回 cal 对齐的 Series。"""
    if "m" in _S:
        return _S["m"], _S["erp"]
    cal = RC.data()["cal"]
    pe = pd.read_csv(CC / "000300.csv", parse_dates=["date"]).set_index("date").pe
    y = pd.read_csv(CC / "cgb10y.csv", parse_dates=["date"]).set_index("date").y10
    df = pd.concat({"pe": pe, "y": y}, axis=1).sort_index()
    df["y"] = df["y"].ffill()
    df = df.dropna()
    df = df[df.pe > 0]
    erp = 100.0 / df.pe - df.y
    vals = erp.to_numpy()
    p = np.full(len(vals), 0.5)
    for i in range(len(vals)):
        if i + 1 >= 250:
            p[i] = (vals[: i + 1] <= vals[i]).mean()
    ps = pd.Series(p, index=erp.index)
    m = (0.5 + ps).reindex(cal.union(ps.index)).ffill().reindex(cal).fillna(1.0)
    _S["m"], _S["erp"] = m, erp
    return m, erp


def erc_weights_mult(cov):
    """engine 的乘法迭代（冻结时写的实现）。协方差含负值时不收敛，只作参考。"""
    n = cov.shape[0]
    w = np.full(n, 1.0 / n)
    for _ in range(300):
        rc = w * (cov @ w)
        if not np.isfinite(rc).all() or rc.mean() <= 0:
            break
        w = w * np.sqrt(rc.mean() / np.maximum(rc, 1e-15))
        w = np.clip(w, 1e-8, None)
        w = w / w.sum()
    return w


ERC_ALGO = "ccd"


def erc_weights(cov, tol=1e-10, it=2000):
    """等风险贡献：Spinu 凸形式 min ½w'Σw − Σ(1/n)log w 的循环坐标下降（Griveau-Billion 等 2013），再归一化。"""
    if ERC_ALGO == "mult":
        return erc_weights_mult(cov)
    n = cov.shape[0]
    b = np.full(n, 1.0 / n)
    w = 1.0 / np.sqrt(np.diag(cov))
    w = w / w.sum()
    for _ in range(it):
        w_old = w.copy()
        for i in range(n):
            a = cov[i, i]
            c = float(cov[i] @ w - a * w[i])
            w[i] = (-c + np.sqrt(c * c + 4 * a * b[i])) / (2 * a)
        if np.max(np.abs(w - w_old)) < tol:
            break
    return w / w.sum()


def core_targets(R, s0, mode="IV", assets=BASE8, eq=EQ8, mult=None, k_static=None):
    """月末目标权重。mode: IV / EW / ERC；mult: C3 乘数 Series；k_static: 常数乘数。"""
    R = R[assets]
    nobs = R.notna().cumsum()
    vol60 = R.rolling(60, min_periods=60).std()
    me = PB.month_end_pos(R.index, s0)
    tg, mused = {}, {}
    for s in me:
        if mode == "ERC":
            avail = [c for c in assets if nobs.iat[s, assets.index(c)] >= 120]
        else:
            avail = [c for c in assets if nobs.iat[s, assets.index(c)] >= 60 and np.isfinite(vol60.iat[s, assets.index(c)])]
        if not avail:
            continue
        if mode == "EW":
            w = pd.Series(1.0 / len(avail), index=avail)
        elif mode == "IV":
            iv = 1.0 / vol60.iloc[s][avail].clip(lower=1e-6)
            w = iv / iv.sum()
        else:
            X = R.iloc[s - 119: s + 1][avail].fillna(0.0).to_numpy()
            cov = np.cov(X.T) + np.eye(len(avail)) * 1e-10
            w = pd.Series(erc_weights(cov), index=avail)
        k = None
        if mult is not None:
            k = float(mult.iloc[s])
        elif k_static is not None:
            k = float(k_static)
        if k is not None and BOND in w.index:
            e = [c for c in w.index if c in eq]
            budget = float(w[e].sum())
            room = budget + float(w[BOND])
            k_eff = min(k, room / budget) if budget > 0 else 1.0
            w[e] = w[e] * k_eff
            w[BOND] = room - budget * k_eff
            mused[s] = k_eff
        tg[s] = w
    return tg, pd.Series(mused, dtype=float)


def core_run(lo, cost, **kw):
    R = core_returns()
    tg, mused = core_targets(R, lo, **kw)
    net, turn = PB.sim_targets(R[kw.get("assets", BASE8)], tg, lo, cost)
    return dict(ret=net, turn=turn, expo=np.ones(len(net)), lo=lo, mused=mused)


# ------------------------------------------------------------------ 卫星
def sector_map():
    if "sec" in _S:
        return _S["sec"]
    P = panel()
    u = pd.read_csv(ROOT / "data/etf_universe.csv").set_index("code")["name"].to_dict()
    sec = {c: sector_tag(P["cat"].get(c, ""), u.get(c, ""), "") for c in RC.data()["ever"]}
    _S["sec"] = sec
    return sec


def sat_feats(sdate, skip=0, quality=False, sector=False):
    D = RC.data()
    close, member = D["closeT"], D["M"]
    ret = close.pct_change(fill_method=None)
    vol20 = ret.rolling(20, min_periods=15).std()
    H = (20, 60, 120)
    mom = {n: close.shift(skip) / close.shift(n + skip) - 1.0 for n in H}
    ma60 = close.rolling(60, min_periods=60).mean()
    bars = close.notna().cumsum()
    valid = (bars >= 150) & close.notna() & mom[120].notna() & vol20.notna() & member
    rk = {n: mom[n].where(valid).rank(axis=1, pct=True) for n in H}
    rm = (rk[20] + rk[60] + rk[120]) / 3.0
    cand = valid & rm.notna() & (close > ma60)
    if sector or quality:
        rmv, cv = rm.to_numpy(), cand.to_numpy().copy()
        if sector:
            sec = sector_map()
            sv = np.array([sec[c] for c in close.columns])
            for i in range(len(cv)):
                idx = np.flatnonzero(cv[i] & ~np.isnan(rmv[i]))
                if len(idx) <= 2:
                    continue
                cnt = {}
                for j in idx[np.argsort(-rmv[i, idx])]:
                    cnt[sv[j]] = cnt.get(sv[j], 0) + 1
                    if cnt[sv[j]] > 2:
                        cv[i, j] = False
        if quality:
            r = ret.to_numpy()
            pos = pd.DataFrame(r > 0, index=close.index).rolling(120, min_periods=120).mean().to_numpy()
            neg = pd.DataFrame(r < 0, index=close.index).rolling(120, min_periods=120).mean().to_numpy()
            idd = np.sign(mom[120].to_numpy()) * (neg - pos)
            smooth = (1 - idd) / 2
            new = np.full(rmv.shape, np.nan)
            for i in range(len(cv)):
                idx = np.flatnonzero(cv[i] & ~np.isnan(rmv[i]))
                top = idx[np.argsort(-rmv[i, idx])][:10]
                cv[i] = False
                cv[i, top] = True
                sm = np.nan_to_num(smooth[i, top], nan=0.5)
                new[i, top] = rmv[i, top] * sm
            rm = pd.DataFrame(new, index=close.index, columns=close.columns)
        cand = pd.DataFrame(cv, index=close.index, columns=close.columns)
    enough = (valid.sum(axis=1) >= 8) & rm.notna().any(axis=1)
    enough &= close.index >= pd.Timestamp(sdate)
    start = int(np.argmax(enough.to_numpy()))
    return {"ret": ret, "vol20": vol20, "mom": mom, "ma60": ma60, "valid": valid, "rm": rm,
            "cand": cand, "start": start, "bench": (1.0 + ret.mean(axis=1).fillna(0.0)).cumprod()}


def sat_run(sdate, cost, skip=0, quality=False, sector=False, buffer=False, vt=True, keep=False):
    D = RC.data()
    f = sat_feats(sdate, skip, quality, sector)
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost, buffer_n=8 if buffer else None)
    r0 = E.simulate(D["closeT"], f, cfg, keep_weights=keep and not vt)
    if vt:
        f2 = dict(f); f2["base_ret"] = r0["ret"]
        rr = E.simulate(D["closeT"], f2, replace(cfg, rule=E.VolTarget(0.15, 60, 40, every=10)), keep_weights=keep)
    else:
        rr = r0
    return dict(ret=np.asarray(rr["ret"]), turn=np.asarray(rr["turn"]), expo=np.asarray(rr["expo"]), lo=f["start"],
                w_hist=rr.get("w_hist") if keep else None)


# ------------------------------------------------------------------ 风控
def apply_mult(d, m, cost):
    """m: 第 t 日收盘决定的乘数（ndarray），t+1 收盘成交，t+2 起生效。"""
    ret, expo = np.asarray(d["ret"], float), np.asarray(d["expo"], float)
    T = len(ret)
    me = np.ones(T)
    me[2:] = m[:-2]
    dm = np.zeros(T)
    dm[1:] = np.abs(m[1:] - m[:-1])            # 第 t 日决定的变化，在 t+1 收盘成交
    tc = np.zeros(T)
    tc[1:] = cost * dm[:-1] * expo[1:]
    out = dict(d)
    out["ret"] = me * ret - tc
    out["expo"] = me * expo
    out["turn"] = np.asarray(d["turn"], float) + np.r_[0, dm[:-1]] * expo
    out["mult"] = me
    return out


def r1_signal(ret, lo):
    T = len(ret)
    m = np.ones(T)
    nav = np.cumprod(1 + np.where(np.arange(T) >= lo, ret, 0.0))
    peak = np.maximum.accumulate(nav)
    hi60 = pd.Series(nav).rolling(60, min_periods=1).max().to_numpy()
    state = 1.0
    for t in range(lo, T):
        dd = nav[t] / peak[t] - 1
        if state == 1.0 and dd < -0.15:
            state = 0.5
        elif state == 0.5 and (nav[t] >= hi60[t] - 1e-12 or dd > -0.05):
            state = 1.0
        m[t] = state
    return m


def r2_signal(w_hist, lo):
    D = RC.data()
    R = D["RT"].to_numpy()
    T = len(R)
    m = np.ones(T)
    avg = np.full(T, np.nan)
    for t in range(max(lo, 60), T):
        cols = [c for c, x in w_hist[t].items() if x > 1e-9]
        if len(cols) < 2:
            continue
        X = R[t - 59: t + 1][:, cols]
        ok = np.isfinite(X).all(axis=1)
        if ok.sum() < 40:
            continue
        C = np.corrcoef(X[ok].T)
        iu = np.triu_indices(len(cols), 1)
        a = float(np.nanmean(C[iu]))
        avg[t] = a
        if a > 0.8:
            m[t] = 0.5
    return m, avg
