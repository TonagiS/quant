#!/usr/bin/env python3
"""快速启动牛市开关 A-FAST。见 runs/2026-11-14_bull_fast/PREREG.md；复用 bull_switch_backtest。"""
from __future__ import annotations
import sys, time
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402
import bull_switch_backtest as B  # noqa: E402
import fastcap_ma_sens as MS  # noqa: E402
import highvol_stocks_backtest as HV  # noqa: E402
import layered_v1_backtest as L  # noqa: E402
from single_asset_harness import run_pos  # noqa: E402

RUN = AB.ROOT / "runs" / "2026-11-14_bull_fast"
COST = AB.COST
WARMUP = 250
FLAGS = ["MKT", "OWN", "FAST-MKT", "FAST-OWN"]
ARMS = ["A", "A-FC", "A-BULL-MKT", "A-BULL-OWN", "A-FAST-MKT", "A-FAST-OWN"]
A_PEAKS = [("2007-10", "CSI", "2007-10-01", "2007-10-31"), ("2015-06", "CSI", "2015-06-01", "2015-06-30"),
           ("2021-02", "CSI", "2021-02-01", "2021-02-28")]
A_BOTTOMS = [("2005-06", "2005-06-01", "2005-06-30"), ("2008-11", "2008-11-01", "2008-11-30"),
             ("2014-07", "2014-07-01", "2014-07-31"), ("2019-01", "2019-01-01", "2019-01-31"),
             ("2024-09", "2024-09-01", "2024-09-30")]
US_PEAKS = [("2000-03", "IXIC", "2000-03-01", "2000-03-31"), ("2007-10", "GSPC", "2007-10-01", "2007-10-31"),
            ("2021-11", "IXIC", "2021-11-01", "2021-11-30")]
US_BOTTOMS = [("2002-10", "2002-10-01", "2002-10-31"), ("2009-03", "2009-03-01", "2009-03-31"),
              ("2020-03", "2020-03-01", "2020-03-31"), ("2022-10", "2022-10-01", "2022-10-31")]
POST_PEAK, FIRST_N, POST_BOTTOM = 120, 60, 250


def fast_flag_from_close(c, th):
    ma60 = c.rolling(60, min_periods=60).mean()
    ma200 = c.rolling(200, min_periods=200).mean()
    on = ((c > ma60) & (c / c.shift(20) - 1 > th) & (c > 0.95 * ma200) & ma200.notna()).to_numpy()
    below = (c < ma60).fillna(False).to_numpy()
    dd = (c / c.rolling(60, min_periods=1).max() - 1).to_numpy()
    out = np.zeros(len(c), dtype=bool)
    st, ron, rbelow = False, 0, 0
    for i in range(len(c)):
        ron = ron + 1 if on[i] else 0
        rbelow = rbelow + 1 if below[i] else 0
        if st:
            if dd[i] <= -0.10 or rbelow >= 3:
                st = False
        elif ron >= 2:
            st = True
        out[i] = st
    return pd.Series(out, index=c.index)


_BF = {}


def bench_flags(b):
    if b not in _BF:
        c = B.bench_close(b)
        _BF[b] = {"MKT": B.mkt_flag_from_close(c), "FAST-MKT": fast_flag_from_close(c, 0.08)}
    return _BF[b]


def align(f, idx):
    return f.reindex(idx.union(f.index)).ffill().reindex(idx).fillna(False).astype(bool)


def extreme(c, a, b, fn):
    w = c[(c.index >= pd.Timestamp(a)) & (c.index <= pd.Timestamp(b))]
    if not len(w):
        return None
    return w.idxmax() if fn == "max" else w.idxmin()


def days_on_after(flag, t):
    f = flag[flag.index >= t]
    if len(f) == 0 or not f.iloc[0]:
        return 0
    off = np.flatnonzero(~f.to_numpy())
    return int(off[0]) if len(off) else int(len(f))


def lag_on_after(flag, t):
    f = flag[flag.index >= t]
    on = np.flatnonzero(f.to_numpy())
    return int(on[0]) if len(on) else np.nan


_PEAKDATES = {}


def peak_dates(ash):
    """基准顶日期（用于 post-peak 归类，全面板统一）。"""
    k = "A" if ash else "US"
    if k not in _PEAKDATES:
        if ash:
            c = B.bench_close("CSI300TR")
            _PEAKDATES[k] = [extreme(c, a, b, "max") for _, _, a, b in A_PEAKS]
        else:
            ser = {"GSPC": B.bench_close("GSPC"),
                   "IXIC": pd.read_csv(HV.DX / "_IXIC.csv", parse_dates=["date"]).set_index("date").close}
            _PEAKDATES[k] = [extreme(ser[bk], a, b, "max") for _, bk, a, b in US_PEAKS]
    return _PEAKDATES[k]


def one(spec):
    panel, code = spec["panel"], spec["code"]
    try:
        df = B.load(spec)
    except Exception as e:  # noqa: BLE001
        return [], dict(spec, status=f"load_fail {e!r}"[:200]), []
    if df is None or len(df) < (260 if panel == "cs951" else WARMUP + 500):
        return [], dict(spec, status="too_short"), []
    df = df.reset_index(drop=True)
    F = L.slim_factors(df)
    pos, _ = MS.arms_positions(F)
    A, FC = pos["A"], pos["A-FC"]
    idx = A.index
    b = B.bench_of(panel, code)
    bf = bench_flags(b)
    c = F["close"]
    fl = {"MKT": align(bf["MKT"], idx), "OWN": B.own_flag_from_close(c),
          "FAST-MKT": align(bf["FAST-MKT"], idx), "FAST-OWN": fast_flag_from_close(c, 0.12)}
    P = {"A": A, "A-FC": FC}
    for k in FLAGS:
        arm = "A-BULL-" + k if k in ("MKT", "OWN") else "A-" + k
        P[arm] = pd.Series(np.where(fl[k], FC, A), index=idx)
    ret = F["ret"]
    nets = {a: run_pos(p, ret, cost=COST) for a, p in P.items()}
    dates = pd.Series(idx)
    if panel == "cs951":
        ev = (dates >= pd.Timestamp("2019-01-01")).to_numpy(); windows = B.CS_WINDOWS
    else:
        ev = np.arange(len(idx)) >= WARMUP; windows = B.LONG_WINDOWS
    base = {k: spec.get(k, "") for k in ("panel", "code", "name", "group")}
    base.update(bench=b)
    lag = {k: v.shift(1).fillna(False).astype(bool) for k, v in fl.items()}  # t 日收益所用 flag
    rows = []

    def emit(kind, w, m, minn=AB.MIN_STATS):
        if m.sum() < minn:
            return
        mm = pd.Series(m, index=idx)
        sh = {f"on_{k}": float(lag[k][mm].mean()) for k in FLAGS}
        bh = None
        for arm, (net, ps) in nets.items():
            s = AB.wstats(net, ret, ps, m)
            rows.append(dict(base, kind=kind, window=w, arm=arm, **s, **sh)); bh = s
        rows.append(dict(base, kind=kind, window=w, arm="BH", n=bh["n"], cagr=bh["bh_cagr"], mdd=bh["bh_mdd"],
                         calmar=bh["bh_calmar"], avg_pos=1.0, turn=0.0, static_cagr=bh["bh_cagr"], static_mdd=bh["bh_mdd"],
                         static_calmar=bh["bh_calmar"], d_calmar_static=0.0, bh_cagr=bh["bh_cagr"], bh_mdd=bh["bh_mdd"],
                         bh_calmar=bh["bh_calmar"], **sh))

    wmask = {}
    for w, a, bb in windows:
        m = ev.copy()
        if a:
            m &= ((dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(bb))).to_numpy()
        wmask[w] = m
        emit("window", w, m)
    if panel.startswith("hv_"):
        vprev = (ret.rolling(250, min_periods=250).std() * np.sqrt(252)).shift(1)
        for bname, lo, hi in HV.BUCKETS:
            m = ev & ((vprev >= lo) & (vprev < hi)).fillna(False).to_numpy()
            emit("pit", bname, m, HV.MIN_BUCKET)

    # ---- 诊断 ----
    ash = panel in ("cs951", "etf_long", "index", "hv_ashare")
    lA = np.log1p(nets["A"][0].fillna(0.0))
    full = wmask["full"]
    yrs = full.sum() / 252
    pk = [p for p in peak_dates(ash) if p is not None]
    pos_i = np.arange(len(idx))
    postpeak = np.zeros(len(idx), dtype=bool)
    for p in pk:
        j = idx.searchsorted(p)
        postpeak[j:j + POST_PEAK + 1] = True
    diag = dict(base, status="ok", rows=len(df), eval_years=yrs)
    peaks = A_PEAKS if ash else US_PEAKS
    bots = A_BOTTOMS if ash else US_BOTTOMS
    valid_from = idx[WARMUP] if len(idx) > WARMUP else idx[-1]
    for k in FLAGS:
        arm = "A-BULL-" + k if k in ("MKT", "OWN") else "A-" + k
        f = fl[k]; g = lag[k].to_numpy()
        d = (np.log1p(nets[arm][0].fillna(0.0)) - lA).to_numpy()
        # 开启序号
        run = np.zeros(len(g), dtype=int); r = 0
        for i, v in enumerate(g):
            r = r + 1 if v else 0; run[i] = r
        cat_pp = full & postpeak
        cat_f60 = full & ~postpeak & (run >= 1) & (run <= FIRST_N)
        cat_late = full & ~postpeak & (run > FIRST_N)
        cat_off = full & ~postpeak & (run == 0)
        for nm_, cm in (("postpeak", cat_pp), ("first60", cat_f60), ("later_on", cat_late), ("off", cat_off)):
            diag[f"{k}|contrib_{nm_}"] = float(d[cm].sum() / yrs) if yrs > 0 else np.nan
        diag[f"{k}|contrib_total"] = float(d[full].sum() / yrs) if yrs > 0 else np.nan
        diag[f"{k}|pp_on_share"] = float(g[cat_pp].mean()) if cat_pp.any() else np.nan
        fe = f.to_numpy()[full]
        diag[f"{k}|share_on"] = float(fe.mean())
        diag[f"{k}|toggles_per_yr"] = float(((fe[1:]) & (~fe[:-1])).sum() / yrs) if yrs > 0 else np.nan
        for n_, _, a, bb in peaks:
            t = extreme(c, a, bb, "max")
            if t is not None and t >= valid_from:
                diag[f"{k}|peak_{n_}_days_on"] = days_on_after(f, t)
                j = idx.get_loc(t)
                diag[f"{k}|peak_{n_}_sumd120"] = float(d[j + 1:j + 1 + POST_PEAK].sum())
        for n_, a, bb in bots:
            t = extreme(c, a, bb, "min")
            if t is not None and t >= valid_from:
                diag[f"{k}|bottom_{n_}_lag"] = lag_on_after(f, t)
                j = idx.get_loc(t)
                diag[f"{k}|bottom_{n_}_sumd250"] = float(d[j + 1:j + 1 + POST_BOTTOM].sum())
        # 回撤分解
        for w in ("y2008", "y2015"):
            m = wmask.get(w)
            if m is None or m.sum() < AB.MIN_STATS:
                continue
            net = nets[arm][0].fillna(0.0)[m]
            eq = (1 + net).cumprod()
            T = (eq / eq.cummax() - 1).idxmin()
            Pk = eq[:T].idxmax()
            sel = m & np.asarray(idx > Pk) & np.asarray(idx <= T)
            ex = d[sel]
            on_ = g[sel]
            diag[f"{k}|{w}_mdd_excess"] = float(ex.sum())
            diag[f"{k}|{w}_mdd_excess_on"] = float(ex[on_].sum())
            diag[f"{k}|{w}_mdd_excess_off"] = float(ex[~on_].sum())
    return rows, diag, []


def bench_timing():
    csi = B.bench_close("CSI300TR"); gs = B.bench_close("GSPC")
    ix = pd.read_csv(HV.DX / "_IXIC.csv", parse_dates=["date"]).set_index("date").close
    fl = {"CSI": {"BULL-MKT": B.mkt_flag_from_close(csi), "FAST-MKT": fast_flag_from_close(csi, 0.08)},
          "GSPC": {"BULL-MKT": B.mkt_flag_from_close(gs), "FAST-MKT": fast_flag_from_close(gs, 0.08)}}
    ser = {"CSI": csi, "GSPC": gs, "IXIC": ix}
    rows = []
    for n, bk, a, b in A_PEAKS:
        t = extreme(csi, a, b, "max")
        for d, f in fl["CSI"].items():
            rows.append(dict(market="A股", event=f"顶 {n}", date=str(t.date()), defn=d, metric="顶后仍开(天)", value=days_on_after(f, t)))
    for n, a, b in A_BOTTOMS:
        t = extreme(csi, a, b, "min")
        for d, f in fl["CSI"].items():
            rows.append(dict(market="A股", event=f"底 {n}", date=str(t.date()), defn=d, metric="底后开启(天)", value=lag_on_after(f, t)))
    for n, bk, a, b in US_PEAKS:
        t = extreme(ser[bk], a, b, "max")
        for d, f in fl["GSPC"].items():
            rows.append(dict(market="美股", event=f"顶 {n}({bk})", date=str(t.date()), defn=d, metric="顶后仍开(天)", value=days_on_after(f, t)))
    for n, a, b in US_BOTTOMS:
        t = extreme(gs, a, b, "min")
        for d, f in fl["GSPC"].items():
            rows.append(dict(market="美股", event=f"底 {n}", date=str(t.date()), defn=d, metric="底后开启(天)", value=lag_on_after(f, t)))
    stat = []
    for bk, fs in fl.items():
        for d, f in fs.items():
            ff = f[f.index >= ("2005-01-01" if bk == "CSI" else "1990-01-01")]
            yrs = len(ff) / 252
            stat.append(dict(bench=bk, defn=d, since=str(ff.index[0].date()), share_on=float(ff.mean()),
                             toggles_per_yr=float((ff.to_numpy()[1:] & ~ff.to_numpy()[:-1]).sum() / yrs)))
    return pd.DataFrame(rows), pd.DataFrame(stat)


def main():
    t = B.tasks()
    print(time.strftime("%H:%M:%S"), "tasks", len(t), flush=True)
    rows, diags = [], []
    with Pool(6) as pool:
        for i, (r, dg, _) in enumerate(pool.imap_unordered(one, t, chunksize=4)):
            rows += r; diags.append(dg)
            if i % 500 == 0:
                print(time.strftime("%H:%M:%S"), i, flush=True)
    pd.DataFrame(rows).to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    pd.DataFrame(diags).to_csv(RUN / "diagnostics_by_name.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    bt, st = bench_timing()
    bt.to_csv(RUN / "bench_flag_timing.csv", index=False, encoding="utf-8-sig")
    st.to_csv(RUN / "bench_flag_stats.csv", index=False, encoding="utf-8-sig")
    print(time.strftime("%H:%M:%S"), "done", len(rows), flush=True)


if __name__ == "__main__":
    main()
