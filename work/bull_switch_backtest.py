#!/usr/bin/env python3
"""牛市开关 A-BULL（牛市 A-FC，否则 A）。见 runs/2026-11-14_bull_switch/PREREG.md"""
from __future__ import annotations
import json, sys, time
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402
import fastcap_ma_sens as MS  # noqa: E402
import highvol_stocks_backtest as HV  # noqa: E402
import layered_v1_backtest as L  # noqa: E402
import long_history_backtest as LH  # noqa: E402
from single_asset_harness import run_pos  # noqa: E402

ROOT = AB.ROOT
RUN = ROOT / "runs" / "2026-11-14_bull_switch"
BENCH_DIR = ROOT / "data" / "cache_highvol" / "bench"
END = "2026-11-13"
COST = AB.COST
WARMUP = 250
CONFIRM = 5
ARMS = ["A", "A-FC", "A-BULL-MKT", "A-BULL-OWN"]
LONG_WINDOWS = [("full", None, None), ("2000-2002", "2000-01-01", "2002-12-31"), ("2007-2009", "2007-01-01", "2009-12-31"),
                ("y2008", "2008-01-01", "2008-12-31"), ("y2015", "2015-01-01", "2015-12-31"),
                ("y2016", "2016-01-01", "2016-12-31"), ("y2018", "2018-01-01", "2018-12-31"),
                ("y2022", "2022-01-01", "2022-12-31"), ("2023-2026", "2023-01-01", END)]
CS_WINDOWS = [("full", "2019-01-01", END), ("2019-2022", "2019-01-01", "2022-12-31"),
              ("y2022", "2022-01-01", "2022-12-31"), ("2023-2026", "2023-01-01", END)]


def streak_flag(cond):
    c = cond.fillna(False).astype(bool).to_numpy()
    out = np.zeros(len(c), dtype=bool); run = 0
    for i, v in enumerate(c):
        run = run + 1 if v else 0
        out[i] = run >= CONFIRM
    return pd.Series(out, index=cond.index)


def mkt_flag_from_close(c):
    ma = c.rolling(200, min_periods=200).mean()
    cond = (c > ma) & (ma.pct_change(60) > 0) & (c / c.shift(250) - 1 > 0.20) & (c / c.rolling(60, min_periods=1).max() - 1 > -0.10)
    return streak_flag(cond & ma.notna())


def own_flag_from_close(c):
    ma = c.rolling(200, min_periods=200).mean()
    cond = (c > ma) & (ma.pct_change(60) > 0.05) & (c / c.shift(120) - 1 > 0.25) & (c / c.rolling(60, min_periods=1).max() - 1 > -0.10)
    return streak_flag(cond & ma.notna())


def bench_close(b):
    if b == "CSI300TR":
        d = LH.idx_series("H00300")
        return d.set_index("date").close
    d = pd.read_csv(BENCH_DIR / f"{b}.csv", parse_dates=["date"]).dropna(subset=["close"]).drop_duplicates("date").sort_values("date")
    return d.set_index("date").close.astype(float)


_BF = {}


def bench_flag(b):
    if b not in _BF:
        _BF[b] = mkt_flag_from_close(bench_close(b))
    return _BF[b]


def bench_of(panel, code):
    if panel in ("cs951", "etf_long", "index", "hv_ashare"):
        return "CSI300TR"
    c = str(code)
    if c.endswith(".HK") or c.endswith("_HK") or c == "HSI":
        return "HSI"
    if c == "N225":
        return "N225"
    return "GSPC"


def load(spec):
    panel, code, path = spec["panel"], spec["code"], spec.get("path")
    if panel in ("etf_long",):
        df, _ = LH.tr_series(code)
    elif panel == "cs951":
        df, _ = AB.tr_series_any(code)
    elif panel == "index":
        df = LH.idx_series(code)
    elif panel in ("xmkt", "xmkt_tr"):
        df, _ = AB.load_xm(code)
    else:
        df = HV.load(path)
    return df


def one(spec):
    panel, code = spec["panel"], spec["code"]
    try:
        df = load(spec)
    except Exception as e:  # noqa: BLE001
        return [], dict(spec, status=f"load_fail {e!r}"[:200])
    if df is None or len(df) < (260 if panel == "cs951" else WARMUP + 500):
        return [], dict(spec, status="too_short")
    df = df.reset_index(drop=True)
    F = L.slim_factors(df)
    pos, _ = MS.arms_positions(F)
    A, FC = pos["A"], pos["A-FC"]
    idx = A.index
    b = bench_of(panel, code)
    fm = bench_flag(b).reindex(idx.union(bench_flag(b).index)).ffill().reindex(idx).fillna(False).astype(bool)
    fo = own_flag_from_close(F["close"])
    P = {"A": A, "A-FC": FC,
         "A-BULL-MKT": pd.Series(np.where(fm, FC, A), index=idx),
         "A-BULL-OWN": pd.Series(np.where(fo, FC, A), index=idx)}
    ret = F["ret"]
    nets = {a: run_pos(p, ret, cost=COST) for a, p in P.items()}
    dates = pd.Series(idx)
    if panel == "cs951":
        ev = (dates >= pd.Timestamp("2019-01-01")).to_numpy(); windows = CS_WINDOWS
    else:
        ev = np.arange(len(idx)) >= WARMUP; windows = LONG_WINDOWS
    base = {k: spec.get(k, "") for k in ("panel", "code", "name", "group")}
    base.update(bench=b, first=str(dates.iloc[0].date()), last=str(dates.iloc[-1].date()))
    rows = []

    def emit(kind, w, m, minn=AB.MIN_STATS):
        if m.sum() < minn:
            return
        mm = pd.Series(m, index=idx)
        fl = dict(flag_mkt_share=float(fm.shift(1)[mm].mean()), flag_own_share=float(fo.shift(1)[mm].mean()))
        bh = None
        for arm, (net, ps) in nets.items():
            s = AB.wstats(net, ret, ps, m)
            rows.append(dict(base, kind=kind, window=w, arm=arm, **s, **fl)); bh = s
        rows.append(dict(base, kind=kind, window=w, arm="BH", n=bh["n"], cagr=bh["bh_cagr"], mdd=bh["bh_mdd"],
                         calmar=bh["bh_calmar"], avg_pos=1.0, turn=0.0, static_cagr=bh["bh_cagr"], static_mdd=bh["bh_mdd"],
                         static_calmar=bh["bh_calmar"], d_calmar_static=0.0, bh_cagr=bh["bh_cagr"], bh_mdd=bh["bh_mdd"],
                         bh_calmar=bh["bh_calmar"], **fl))

    for w, a, bb in windows:
        m = ev.copy()
        if a:
            m &= ((dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(bb))).to_numpy()
        emit("window", w, m)
    if panel.startswith("hv_"):
        vprev = (ret.rolling(250, min_periods=250).std() * np.sqrt(252)).shift(1)
        for bname, lo, hi in HV.BUCKETS:
            m = ev & ((vprev >= lo) & (vprev < hi)).fillna(False).to_numpy()
            emit("pit", bname, m, HV.MIN_BUCKET)
    # 个体 BULL-OWN 时点
    tim = timing(F["close"], fo, panel)
    return rows, dict(base, status="ok", rows=len(df), timing=tim)


A_PEAKS = [("2007-10", "2007-09-01", "2007-11-30"), ("2015-06", "2015-05-15", "2015-07-15"), ("2021-02", "2021-01-15", "2021-03-15")]
A_BOTTOMS = [("2005", "2005-01-01", "2005-12-31"), ("2008", "2008-09-01", "2008-12-31"), ("2013-14", "2013-06-01", "2014-06-30"),
             ("2018-19", "2018-10-01", "2019-01-31"), ("2024", "2024-01-01", "2024-09-30")]
US_PEAKS = [("2000-03", "IXIC", "2000-02-15", "2000-04-15"), ("2007-10", "GSPC", "2007-09-01", "2007-11-30"),
            ("2021-11", "IXIC", "2021-10-15", "2021-12-31")]
US_BOTTOMS = [("2002-03", "2002-07-01", "2003-03-31"), ("2008-09", "2008-11-01", "2009-03-31"),
              ("2020", "2020-02-01", "2020-04-30"), ("2022", "2022-09-01", "2022-12-31")]


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


def peak_bottom(c, a, b, fn):
    w = c[(c.index >= pd.Timestamp(a)) & (c.index <= pd.Timestamp(b))]
    return (w.idxmax() if fn == "max" else w.idxmin()) if len(w) else None


def timing(c, flag, panel):
    """以该标的自身价格的顶/底（同一冻结日期窗内）测 flag 时点；A 股面板用 A 股窗口，其他用美股窗口。"""
    out = {}
    ash = panel in ("cs951", "etf_long", "index", "hv_ashare")
    peaks = A_PEAKS if ash else [(n, a, b) for n, _, a, b in US_PEAKS]
    bots = A_BOTTOMS if ash else US_BOTTOMS
    valid = flag.index[WARMUP] if len(flag) > WARMUP else None
    for n, a, b in peaks:
        t = peak_bottom(c, a, b, "max")
        if t is not None and valid is not None and t >= valid:
            out[f"peak_{n}"] = days_on_after(flag, t)
    for n, a, b in bots:
        t = peak_bottom(c, a, b, "min")
        if t is not None and valid is not None and t >= valid:
            out[f"bottom_{n}"] = lag_on_after(flag, t)
    return out


def bench_timing():
    rows = []
    csi = bench_close("CSI300TR"); gs = bench_close("GSPC"); ix = pd.read_csv(HV.DX / "_IXIC.csv", parse_dates=["date"]).set_index("date").close
    series = {"CSI300TR": csi, "GSPC": gs, "IXIC": ix}
    flags = {k: {"BULL-MKT": mkt_flag_from_close(v), "BULL-OWN": own_flag_from_close(v)} for k, v in series.items()}
    # 美股 BULL-MKT 实际使用 ^GSPC 的 flag；^IXIC 上的 MKT 定义仅作对照
    for n, a, b in A_PEAKS:
        t = peak_bottom(csi, a, b, "max")
        for d in ("BULL-MKT", "BULL-OWN"):
            rows.append(dict(market="A股", bench="CSI300TR", event=f"顶 {n}", date=str(t.date()), defn=d, days=days_on_after(flags["CSI300TR"][d], t)))
    for n, a, b in A_BOTTOMS:
        t = peak_bottom(csi, a, b, "min")
        for d in ("BULL-MKT", "BULL-OWN"):
            rows.append(dict(market="A股", bench="CSI300TR", event=f"底 {n}", date=str(t.date()), defn=d, days=lag_on_after(flags["CSI300TR"][d], t)))
    for n, bk, a, b in US_PEAKS:
        t = peak_bottom(series[bk], a, b, "max")
        for d in ("BULL-MKT", "BULL-OWN"):
            rows.append(dict(market="美股", bench=bk, event=f"顶 {n}", date=str(t.date()), defn=d, days=days_on_after(flags[bk][d], t)))
            if bk == "IXIC" and d == "BULL-MKT":
                rows.append(dict(market="美股", bench="GSPC(实际MKT基准)", event=f"顶 {n}", date=str(t.date()), defn=d, days=days_on_after(flags["GSPC"][d], t)))
    for n, a, b in US_BOTTOMS:
        t = peak_bottom(gs, a, b, "min")
        for d in ("BULL-MKT", "BULL-OWN"):
            rows.append(dict(market="美股", bench="GSPC", event=f"底 {n}", date=str(t.date()), defn=d, days=lag_on_after(flags["GSPC"][d], t)))
    share = []
    for k, fs in flags.items():
        for d, f in fs.items():
            for a, b in (("2005-01-01", END), ("2019-01-01", END), ("1990-01-01", END)):
                ff = f[(f.index >= a)]
                share.append(dict(bench=k, defn=d, since=a, share_on=float(ff.mean())))
    return pd.DataFrame(rows), pd.DataFrame(share)


def tasks():
    t = []
    lh = pd.read_csv(AB.LHR / "results_long.csv")
    for r in lh[(lh.panel == "etf") & (lh.period == "full") & (lh.arm == "BH")].itertuples():
        t.append(dict(panel="etf_long", code=r.code, name=r.name, group=r.sector))
    meta = pd.read_csv(AB.LHR / "index_meta.csv")
    for r in meta[meta.status == "ok"].itertuples():
        t.append(dict(panel="index", code=r.code, name=r.name, group=r.sector))
    cv = pd.read_csv(ROOT / "runs" / "2026-11-14_candidates_v2" / "results_long.csv")
    for r in cv[(cv.in_cross_section == True) & (cv.rule == "A")].itertuples():  # noqa: E712
        t.append(dict(panel="cs951", code=r.tx_code, name=r.name, group=r.sector))
    for panel, lst in (("xmkt", AB.XM_PRIMARY), ("xmkt_tr", AB.XM_TR)):
        for code, name, rt in lst:
            t.append(dict(panel=panel, code=code, name=name, group=rt))
    for spec in HV.tasks():
        pnl, code, grp, path = spec
        t.append(dict(panel="hv_ashare" if pnl == "ashare" else "hv_xmkt", code=code, name=code, group=grp, path=str(path)))
    return t


def main():
    t = tasks()
    print(time.strftime("%H:%M:%S"), "tasks", len(t), flush=True)
    rows, meta = [], []
    with Pool(6) as pool:
        for i, (r, m) in enumerate(pool.imap_unordered(one, t, chunksize=4)):
            rows += r; meta.append(m)
            if i % 500 == 0:
                print(time.strftime("%H:%M:%S"), i, flush=True)
    pd.DataFrame(rows).to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    cov = pd.DataFrame(meta)
    tim = pd.json_normalize(cov["timing"].apply(lambda x: x if isinstance(x, dict) else {}))
    cov = pd.concat([cov.drop(columns=["timing"]), tim], axis=1)
    cov.to_csv(RUN / "coverage_timing.csv", index=False, encoding="utf-8-sig")
    bt, sh = bench_timing()
    bt.to_csv(RUN / "bench_flag_timing.csv", index=False, encoding="utf-8-sig")
    sh.to_csv(RUN / "bench_flag_share.csv", index=False, encoding="utf-8-sig")
    print(time.strftime("%H:%M:%S"), "done", len(rows), flush=True)


if __name__ == "__main__":
    main()
