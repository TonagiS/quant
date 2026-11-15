#!/usr/bin/env python3
"""缩量回踩 + 放量突破 全A股回测。规则冻结见 runs/2026-11-15_vol_breakout/PREREG.md（不改参数）。
用法: python3 work/vol_breakout_backtest.py [all_a|pre2006]
输出到 runs/2026-11-15_vol_breakout/<panel>/"""
from __future__ import annotations
import sys, time, json
from multiprocessing import Pool
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "2026-11-15_vol_breakout"
DA = ROOT / "data" / "cache_vb" / "ashare"
DB = ROOT / "data" / "cache_vb" / "bench"
COST = 0.0005
SIG_START, SIG_END = pd.Timestamp("2006-01-04"), pd.Timestamp("2026-11-12")
END = pd.Timestamp("2026-11-13")
MIN_BARS = 60
SEED, NDRAW = 20261115, 20
STOP = 0.92
MAXHOLD = 15


def log(m): print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def limit_pct(code, dates):
    L = np.full(len(dates), 0.10)
    if code.startswith("sh68"): L[:] = 0.20
    elif code.startswith("sz30"): L[dates >= np.datetime64("2020-08-24")] = 0.20
    return L


def load(code):
    df = pd.read_csv(DA / f"{code}.csv", parse_dates=["date"])
    df = df[(df.volume > 0) & df.c.notna() & df.o.notna() & (df.o > 0)].reset_index(drop=True)
    if len(df) < MIN_BARS + 25: return None
    d = df.date.values
    f = df.f.values.astype(float)
    o, h, l, c = (df[k].values.astype(float) for k in ("o", "h", "l", "c"))
    O, H, Lo, C = o * f, h * f, l * f, c * f
    V = df.volume.values / f
    s = pd.Series(C)
    ma5, ma10, ma20 = (s.rolling(n).mean().values for n in (5, 10, 20))
    av20 = pd.Series(V).rolling(20).mean().shift(1).values  # d-20..d-1
    hh19 = s.rolling(19).max().shift(1).values  # max C_{t-19..t-1}
    ret = s.pct_change().values
    trend = (C > ma20) & (ma5 > ma10) & (ma10 > ma20)
    shrink = (V < 0.7 * av20) & (C > ma20)
    shrink10 = pd.Series(shrink.astype(float)).rolling(10, min_periods=1).max().values > 0  # t-9..t
    brk = (C > hh19) & (V >= 1.5 * av20) & (ret >= 0.02)
    idx = np.arange(len(C))
    sig = trend & shrink10 & brk & (idx >= MIN_BARS)
    # 涨跌停（原始价，参考前收除权调整）
    Lp = limit_pct(code, d)
    ref = np.r_[np.nan, c[:-1] * f[:-1] / f[1:]]
    up = np.round(ref * (1 + Lp) + 1e-9, 2)
    dn = np.round(ref * (1 - Lp) + 1e-9, 2)
    limup_open = o >= up - 0.005
    oneprice_dn = (h <= dn + 0.005) & (o <= dn + 0.005)
    vr = V / av20
    return dict(code=code, d=d, O=O, H=H, L=Lo, C=C, ma20=ma20, sig=sig, trend=trend & (idx >= MIN_BARS),
                limup_open=limup_open, oneprice_dn=oneprice_dn, vr=vr)


def sim(a, t, intraday=False):
    """t = 信号日索引。返回 dict 或 None（不可入场）。"""
    n = len(a["C"]); e = t + 1
    if e >= n or a["limup_open"][e]: return None
    O, H, L, C, ma20, dn = a["O"], a["H"], a["L"], a["C"], a["ma20"], a["oneprice_dn"]
    ep = O[e]; stop = STOP * ep
    s = e
    while s < n:
        if intraday and L[s] <= stop and not dn[s]:
            px = stop if s == e else min(O[s], stop)
            return dict(e=e, x=s, ep=ep, xp=px, reason="stop_intra", hold=s - e + 1, open=False)
        hold = s - e + 1
        r = None
        if (not intraday) and C[s] <= stop: r = "stop"
        elif C[s] < ma20[s]: r = "ma20"
        elif hold >= MAXHOLD: r = "time"
        if r is not None:
            x = s + 1
            while x < n and dn[x]: x += 1
            if x >= n: break
            return dict(e=e, x=x, ep=ep, xp=O[x], reason=r, hold=x - e, open=False)
        s += 1
    return dict(e=e, x=n - 1, ep=ep, xp=C[n - 1], reason="open", hold=n - e, open=True, mtm_close=True)


def rec(a, t, r, kind):
    ret = r["xp"] * (1 - COST) / (r["ep"] * (1 + COST)) - 1
    out = dict(code=a["code"], kind=kind, sig_date=a["d"][t], entry_date=a["d"][r["e"]], exit_date=a["d"][r["x"]],
               entry_px=r["ep"], exit_px=r["xp"], ret=ret, hold=r["hold"], reason=r["reason"], vr=a["vr"][t])
    return out


def path(a, r):
    """入场日..出场前一日的收盘（出场在开盘则不含出场日收盘；盘中止损当日不计收盘）。"""
    e, x = r["e"], r["x"]
    last = x if r.get("mtm_close") else x - 1
    return a["d"][e:last + 1], a["C"][e:last + 1]


def pass1(code):
    a = load(code)
    if a is None: return None
    d = a["d"]
    inwin = (d >= np.datetime64(SIG_START)) & (d <= np.datetime64(SIG_END))
    cands, paths = [], {}
    chain = {"main": -1, "intra": -1}
    trades = []
    for t in np.where(a["sig"] & inwin)[0]:
        for kind, intra in (("main", False), ("intra", True)):
            r = sim(a, t, intra)
            if r is None:
                if kind == "main": trades.append(dict(code=code, kind="skip_limitup", sig_date=d[t]))
                continue
            rr = rec(a, t, r, kind)
            # 候选（组合用，全部信号）
            rr["cand_id"] = f"{code}_{t}_{kind}"
            pd_, pc_ = path(a, r)
            paths[rr["cand_id"]] = (pd_, pc_.astype(np.float64))
            cands.append(rr)
            # 逐笔链（持仓期间忽略新信号；卖出成交日收盘起可再信号）
            if t >= chain[kind]:
                rr2 = dict(rr); rr2["in_chain"] = True
                trades.append(rr2)
                chain[kind] = r["x"]
    # 随机基线合格日
    nxt_ok = np.r_[~a["limup_open"][1:], False]
    idx = np.arange(len(d))
    elig = inwin & (idx >= MIN_BARS) & nxt_ok
    yr = pd.DatetimeIndex(d).year.values
    eu = np.where(elig)[0]; et = np.where(elig & a["trend"])[0]
    first = d[0]
    # EW 用：复权收盘 & 合格（>=60 根）
    ew = (d, a["C"].astype(np.float32), idx >= MIN_BARS)
    return dict(code=code, cands=cands, paths=paths, trades=trades, eu=(eu.astype(np.int32), yr[eu].astype(np.int16)),
                et=(et.astype(np.int32), yr[et].astype(np.int16)), first=first, ew=ew)


def pass2(args):
    code, items = args  # items: list of (t, draw, base)
    a = load(code); out = []
    for t, draw, base in items:
        r = sim(a, int(t), False)
        if r is None: continue
        rr = rec(a, int(t), r, base); rr["draw"] = int(draw); out.append(rr)
    return out


# ---------------- 指标 ----------------
def metrics(nav: pd.Series):
    nav = nav.dropna()
    if len(nav) < 20: return {}
    nav = nav / nav.iloc[0]
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = nav.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    r = nav.pct_change().dropna()
    mdd = (nav / nav.cummax() - 1).min()
    return dict(cagr=cagr, vol=r.std() * np.sqrt(252), mdd=mdd, calmar=cagr / abs(mdd) if mdd < 0 else np.nan,
                total=nav.iloc[-1] - 1)


WINDOWS = {"full": ("2006-01-04", "2026-11-13"), "y2008": ("2008-01-01", "2008-12-31"),
           "crash2015": ("2015-06-12", "2016-01-28"), "y2015": ("2015-01-01", "2015-12-31"),
           "y2018": ("2018-01-01", "2018-12-31"), "2023-2026": ("2023-01-03", "2026-11-13")}


def window_nav(nav, w):
    s, e = WINDOWS[w]
    # 从窗口前一日收盘起算
    prev = nav[nav.index < pd.Timestamp(s)]
    seg = nav[(nav.index >= pd.Timestamp(s)) & (nav.index <= pd.Timestamp(e))]
    if len(prev): seg = pd.concat([prev.iloc[[-1]], seg])
    return seg


def portfolio(cands: pd.DataFrame, paths: dict, cal: pd.DatetimeIndex):
    cands = cands.sort_values(["sig_date", "vr", "code"], ascending=[True, False, True])
    by_entry = {k: g for k, g in cands.groupby("entry_date")}
    cash, nav_prev = 1.0, 1.0
    pos = {}  # code -> dict(shares, cand_id, exit_date, exit_px, path series, last_px)
    navs, expo, traded, costs, npos = [], [], 0.0, 0.0, []
    tlog = []
    for day in cal:
        # 1) 开盘卖出
        for code in [k for k, p in pos.items() if p["exit_date"] <= day and not p["intraday"] and not p["open"]]:
            p = pos.pop(code); v = p["shares"] * p["exit_px"]
            cash += v * (1 - COST); traded += v; costs += v * COST
            tlog.append(dict(code=code, cand_id=p["cand_id"], entry_date=p["entry_date"], exit_date=day,
                             ret=p["exit_px"] * (1 - COST) / (p["entry_px"] * (1 + COST)) - 1))
        # 2) 开盘买入
        if day in by_entry:
            for _, c in by_entry[day].iterrows():
                if len(pos) >= 10: break
                if c.code in pos: continue
                alloc = min(0.10 * nav_prev, cash)
                if alloc <= 1e-9: break
                gross = alloc / (1 + COST)
                sh = gross / c.entry_px
                cash -= alloc; traded += gross; costs += gross * COST
                pdts, pcs = paths[c.cand_id]
                pos[c.code] = dict(shares=sh, cand_id=c.cand_id, entry_date=day, entry_px=c.entry_px,
                                   exit_date=pd.Timestamp(c.exit_date), exit_px=c.exit_px,
                                   intraday=(c.reason == "stop_intra"), open=(c.reason == "open"),
                                   px=pd.Series(pcs, index=pd.DatetimeIndex(pdts)), last=c.entry_px)
        # 3) 盘中止损（敏感性臂）
        for code in [k for k, p in pos.items() if p["intraday"] and p["exit_date"] <= day]:
            p = pos.pop(code); v = p["shares"] * p["exit_px"]
            cash += v * (1 - COST); traded += v; costs += v * COST
            tlog.append(dict(code=code, cand_id=p["cand_id"], entry_date=p["entry_date"], exit_date=day,
                             ret=p["exit_px"] * (1 - COST) / (p["entry_px"] * (1 + COST)) - 1))
        # 4) 收盘计价
        mv = 0.0
        for p in pos.values():
            if day in p["px"].index: p["last"] = float(p["px"][day])
            mv += p["shares"] * p["last"]
        nav = cash + mv
        navs.append(nav); expo.append(mv / nav if nav > 0 else 0); npos.append(len(pos)); nav_prev = nav
    nav = pd.Series(navs, index=cal)
    return nav, pd.Series(expo, index=cal), traded, costs, pd.DataFrame(tlog), pd.Series(npos, index=cal)


def ew_monthly(ews, cal):
    """每月第一个交易日收盘按合格股票等权建仓，月内漂移。"""
    cols = {}
    elig = {}
    for code, (d, c, ok) in ews.items():
        s = pd.Series(c, index=pd.DatetimeIndex(d))
        cols[code] = s
        elig[code] = pd.Series(ok, index=pd.DatetimeIndex(d))
    P = pd.DataFrame(cols).reindex(cal).astype(np.float32)
    E = pd.DataFrame(elig).reindex(cal)
    traded_today = P.notna()
    E = E.fillna(False).astype(bool) & traded_today
    Pf = P.ffill()
    months = cal.to_period("M")
    firsts = [cal[months == m][0] for m in months.unique()]
    nav = pd.Series(np.nan, index=cal); val = 1.0
    nav.iloc[0] = 1.0
    for i, f0 in enumerate(firsts):
        f1 = firsts[i + 1] if i + 1 < len(firsts) else cal[-1]
        mem = E.loc[f0]; mem = mem[mem].index
        seg = Pf.loc[f0:f1, mem]
        rel = (seg / seg.iloc[0]).mean(axis=1)
        nav.loc[f0:f1] = val * rel.values
        val = float(val * rel.iloc[-1])
    return nav, E.sum(axis=1)


def main(panel="all_a"):
    out = RUN / panel; out.mkdir(parents=True, exist_ok=True)
    meta = pd.read_csv(RUN / "pull_meta.csv")
    meta = meta[meta.status.isin(["ok", "cached"])]
    is_st = meta.name.astype(str).str.contains("ST|退")
    codes = meta.loc[~is_st, "code"].tolist()
    log(f"{panel}: 列表 {len(meta)}，剔除当前ST/退 {int(is_st.sum())}，候选 {len(codes)}")
    res = []
    with Pool(5) as pool:
        for i, r in enumerate(pool.imap_unordered(pass1, codes, chunksize=8)):
            if r is not None: res.append(r)
            if i % 500 == 0: log(f"pass1 {i}")
    if panel == "pre2006":
        res = [r for r in res if pd.Timestamp(r["first"]) < pd.Timestamp("2006-01-01")]
    log(f"有效股票 {len(res)}")
    cands = pd.DataFrame([c for r in res for c in r["cands"]])
    paths = {k: v for r in res for k, v in r["paths"].items()}
    trades = pd.DataFrame([t for r in res for t in r["trades"]])
    for c in ("sig_date", "entry_date", "exit_date"):
        for df in (cands, trades):
            if c in df: df[c] = pd.to_datetime(df[c])
    trades.to_csv(out / "trades_chain.csv.gz", index=False, compression="gzip")
    cands.to_csv(out / "candidates_all_signals.csv.gz", index=False, compression="gzip")
    univ = pd.DataFrame([dict(code=r["code"], first=r["first"]) for r in res])
    univ.to_csv(out / "universe.csv", index=False)

    # ---- 随机基线 ----
    main_tr = trades[(trades.kind == "main") & trades.get("in_chain", False).fillna(False).astype(bool)]
    ny = main_tr.groupby(main_tr.sig_date.dt.year).size()
    rng = np.random.default_rng(SEED)
    jobs = {}
    for base, key in (("R-uni", "eu"), ("R-trend", "et")):
        allc = np.concatenate([np.full(len(r[key][0]), j, dtype=np.int32) for j, r in enumerate(res)])
        allt = np.concatenate([r[key][0] for r in res]); ally = np.concatenate([r[key][1] for r in res])
        for y, n in ny.items():
            pool_idx = np.where(ally == y)[0]
            if len(pool_idx) == 0: continue
            for dr in range(NDRAW):
                pick = rng.choice(pool_idx, size=int(n), replace=len(pool_idx) < n)
                for p in pick:
                    jobs.setdefault(res[allc[p]]["code"], []).append((allt[p], dr, base))
    log(f"随机基线：{sum(len(v) for v in jobs.values())} 笔模拟")
    rand = []
    with Pool(5) as pool:
        for r in pool.imap_unordered(pass2, list(jobs.items()), chunksize=8): rand += r
    rand = pd.DataFrame(rand)
    rand.to_csv(out / "random_trades.csv.gz", index=False, compression="gzip")

    # ---- 组合 ----
    cal = pd.read_csv(DB / "sh000985.csv", parse_dates=["date"]).set_index("date")
    cal = cal[(cal.index >= pd.Timestamp("2006-01-04")) & (cal.index <= END)]
    calidx = cal.index
    ews = {r["code"]: r["ew"] for r in res}
    del res
    ew, ncons = ew_monthly(ews, calidx); del ews
    port = {}
    for kind in ("main", "intra"):
        cc = cands[cands.kind == kind].copy()
        nav, ex, traded, costs, tlog, npos = portfolio(cc, paths, calidx)
        port[kind] = (nav, ex, traded, costs, tlog, npos)
        tlog.to_csv(out / f"portfolio_trades_{kind}.csv", index=False)
    navdf = pd.DataFrame({"strategy": port["main"][0], "strategy_intra": port["intra"][0], "EW-M": ew,
                          "exposure": port["main"][1], "exposure_intra": port["intra"][1],
                          "npos": port["main"][5], "n_ew": ncons})
    k = float(port["main"][1].mean()); k_i = float(port["intra"][1].mean())
    r_ew = ew.pct_change().fillna(0)
    navdf["STATIC"] = (1 + k * r_ew).cumprod()
    navdf["STATIC_intra"] = (1 + k_i * r_ew).cumprod()
    for b, nm in (("sh000300", "CSI300"), ("sh000985", "CSIAll")):
        s = pd.read_csv(DB / f"{b}.csv", parse_dates=["date"]).set_index("date")["c"].reindex(calidx).ffill()
        navdf[nm] = s / s.iloc[0]
    navdf.to_csv(out / "nav_daily.csv")
    rows = []
    for w in WINDOWS:
        for col in ("strategy", "STATIC", "EW-M", "CSI300", "CSIAll", "strategy_intra", "STATIC_intra"):
            m = metrics(window_nav(navdf[col], w))
            seg = window_nav(navdf["exposure" if "intra" not in col else "exposure_intra"], w)
            rows.append(dict(window=w, arm=col, **m, avg_exposure=float(seg.iloc[1:].mean()) if col.startswith("strategy") else np.nan))
    pm = pd.DataFrame(rows); pm.to_csv(out / "portfolio_metrics.csv", index=False)
    yrs = (calidx[-1] - calidx[0]).days / 365.25
    avgnav = port["main"][0].mean()
    extra = dict(k_main=k, k_intra=k_i, turnover_annual=port["main"][2] / avgnav / yrs,
                 cost_drag_annual=port["main"][3] / avgnav / yrs,
                 turnover_annual_intra=port["intra"][2] / port["intra"][0].mean() / yrs,
                 cost_drag_annual_intra=port["intra"][3] / port["intra"][0].mean() / yrs,
                 n_port_trades=len(port["main"][4]), n_port_trades_intra=len(port["intra"][4]),
                 avg_npos=float(port["main"][5].mean()), frac_days_full=float((port["main"][5] >= 10).mean()))
    yearly = navdf[["strategy", "strategy_intra", "STATIC", "EW-M", "CSI300", "CSIAll"]].resample("YE").last()
    first_row = navdf[["strategy", "strategy_intra", "STATIC", "EW-M", "CSI300", "CSIAll"]].iloc[[0]] * 0 + 1
    yearly = pd.concat([first_row, yearly]).pct_change().iloc[1:]
    yearly.index = yearly.index.year
    yearly["avg_exposure"] = navdf["exposure"].groupby(navdf.index.year).mean()
    yearly.to_csv(out / "portfolio_yearly.csv")
    json.dump(extra, open(out / "portfolio_extra.json", "w"), indent=1, ensure_ascii=False)
    log("完成")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all_a")
