#!/usr/bin/env python3
"""A-FC 门槛换 G60(all-3) + 快信号 M 换 MA15/MA10（FC-G60-M15/M10）。见 runs/2026-11-14_fc_fastgate/PREREG.md；复用 bull_fast / fastcap_v5。"""
from __future__ import annotations
import sys, time
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402
import bull_fast_backtest as BF  # noqa: E402
import bull_switch_backtest as B  # noqa: E402
import fastcap_ma_sens as MS  # noqa: E402
import fastcap_v5_backtest as V  # noqa: E402
import highvol_stocks_backtest as HV  # noqa: E402
import layered_v1_backtest as L  # noqa: E402
from single_asset_harness import run_pos  # noqa: E402

RUN = AB.ROOT / "runs" / "2026-11-14_fc_fastgate"
COST = AB.COST
WARMUP = 250
NEW = ["FC-G60-M15", "FC-G60-M10"]
ARMS = ["A", "A-FC", "A-FAST-MKT"] + NEW


def gate(c, kind):
    """G60：三项全部成立（PREREG 冻结）；M 均线长度由臂名决定。"""
    ma20, ma60 = c.rolling(20, min_periods=20).mean(), c.rolling(60, min_periods=60).mean()
    g = (c > ma60) & (ma20 > ma60) & (ma60.pct_change(20) > 0) & ma60.shift(20).notna()
    return g, (15 if kind.endswith("M15") else 10)


def gated_positions(F, g1, volcap, taper, kind):
    c = F["close"]
    gt, mlen = gate(c, kind)
    R = (c / c.rolling(60, min_periods=1).max() - 1) <= V.R_TH
    vol20 = F["ret"].rolling(20, min_periods=20).std() * np.sqrt(L.TD)
    Vs = ((vol20 / F["vol60"]) > V.V_TH).fillna(False)
    ma = c.rolling(mlen, min_periods=mlen).mean()
    M = (c < ma).fillna(False) & ma.notna()
    clear = (gt.fillna(False) & ~R & ~Vs & ~M).to_numpy()
    off = pd.Series(V.cap_state_clear(clear), index=c.index)
    cap = pd.Series(np.where(off.to_numpy(), 1.0, volcap.to_numpy()), index=c.index)
    return (g1 * cap * taper).clip(0, 1), off


def all_positions(F, fast_flag):
    g1, volcap, T = L.g1_volcap_T(F)
    taper = T.map(L.TAPER_BASE).astype(float)
    pos, comps = MS.arms_positions(F)
    A, FC = pos["A"], pos["A-FC"]
    P = {"A": A, "A-FC": FC, "A-FAST-MKT": pd.Series(np.where(fast_flag, FC, A), index=A.index)}
    off = {"A-FC": comps["off_A-FC"].astype(bool), "A-FAST-MKT": comps["off_A-FC"].astype(bool) & fast_flag}
    for k in NEW:
        P[k], off[k] = gated_positions(F, g1, volcap, taper, k)
    return P, off


def one(spec):
    panel, code = spec["panel"], spec["code"]
    try:
        df = B.load(spec)
    except Exception as e:  # noqa: BLE001
        return [], dict(spec, status=f"load_fail {e!r}"[:200])
    if df is None or len(df) < (260 if panel == "cs951" else WARMUP + 500):
        return [], dict(spec, status="too_short")
    df = df.reset_index(drop=True)
    F = L.slim_factors(df)
    idx = F["close"].index
    b = B.bench_of(panel, code)
    ff = BF.align(BF.bench_flags(b)["FAST-MKT"], idx)
    P, OFF = all_positions(F, ff)
    ret = F["ret"]
    nets = {a: run_pos(p, ret, cost=COST) for a, p in P.items()}
    dates = pd.Series(idx)
    if panel == "cs951":
        ev = (dates >= pd.Timestamp("2019-01-01")).to_numpy(); windows = B.CS_WINDOWS
    else:
        ev = np.arange(len(idx)) >= WARMUP; windows = B.LONG_WINDOWS
    base = {k: spec.get(k, "") for k in ("panel", "code", "name", "group")}
    rows = []

    def emit(kind, w, m, minn=AB.MIN_STATS):
        if m.sum() < minn:
            return
        mm = pd.Series(m, index=idx)
        sh = {f"capoff_{k}": float(v.shift(1).fillna(False)[mm].mean()) for k, v in OFF.items()}
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
    # 诊断
    ash = panel in ("cs951", "etf_long", "index", "hv_ashare")
    peaks = BF.A_PEAKS if ash else BF.US_PEAKS
    bots = BF.A_BOTTOMS if ash else BF.US_BOTTOMS
    full = wmask["full"]; yrs = full.sum() / 252
    lA = np.log1p(nets["A"][0].fillna(0.0))
    valid_from = idx[WARMUP] if len(idx) > WARMUP else idx[-1]
    c = F["close"]
    diag = dict(base, status="ok", eval_years=yrs)
    for k, off in OFF.items():
        o = off.to_numpy()[full]
        diag[f"{k}|capoff_share"] = float(o.mean())
        diag[f"{k}|capoff_toggles_per_yr"] = float((o[1:] & ~o[:-1]).sum() / yrs) if yrs > 0 else np.nan
        d = (np.log1p(nets[k][0].fillna(0.0)) - lA).to_numpy()
        diag[f"{k}|contrib_total"] = float(d[full].sum() / yrs) if yrs > 0 else np.nan
        for n_, _, a, bb in peaks:
            t = BF.extreme(c, a, bb, "max")
            if t is not None and t >= valid_from:
                j = idx.get_loc(t)
                diag[f"{k}|peak_{n_}_days_off"] = BF.days_on_after(off, t)
                diag[f"{k}|peak_{n_}_sumd120"] = float(d[j + 1:j + 1 + BF.POST_PEAK].sum())
        for n_, a, bb in bots:
            t = BF.extreme(c, a, bb, "min")
            if t is not None and t >= valid_from:
                j = idx.get_loc(t)
                diag[f"{k}|bottom_{n_}_lag"] = BF.lag_on_after(off, t)
                diag[f"{k}|bottom_{n_}_sumd250"] = float(d[j + 1:j + 1 + BF.POST_BOTTOM].sum())
    return rows, diag


def bench_timing():
    out = []
    for bk, ser, peaks, bots in (("CSI300TR", B.bench_close("CSI300TR"), BF.A_PEAKS, BF.A_BOTTOMS),
                                 ("GSPC", B.bench_close("GSPC"), BF.US_PEAKS, BF.US_BOTTOMS)):
        df = pd.DataFrame({"date": ser.index, "close": ser.values, "amount": np.nan, "volume": np.nan})
        F = L.slim_factors(df)
        ff = BF.bench_flags(bk)["FAST-MKT"].reindex(F["close"].index).fillna(False).astype(bool)
        _, OFF = all_positions(F, ff)
        c = F["close"]
        for k, off in OFF.items():
            for n_, *_r in peaks:
                a, bb = _r[-2], _r[-1]
                t = BF.extreme(c, a, bb, "max")
                if bk == "GSPC" and _r[0] == "IXIC":
                    continue  # ^GSPC 上只测 ^GSPC 自身的顶（2007-10）
                out.append(dict(bench=bk, arm=k, event=f"顶 {n_}", date=str(t.date()), metric="顶后关帽持续(天)", value=BF.days_on_after(off, t)))
            for n_, a, bb in bots:
                t = BF.extreme(c, a, bb, "min")
                out.append(dict(bench=bk, arm=k, event=f"底 {n_}", date=str(t.date()), metric="底后首次关帽(天)", value=BF.lag_on_after(off, t)))
            o = off[off.index >= ("2005-01-01" if bk == "CSI300TR" else "1990-01-01")].to_numpy()
            out.append(dict(bench=bk, arm=k, event="全期", date="", metric="关帽占比", value=float(o.mean())))
            out.append(dict(bench=bk, arm=k, event="全期", date="", metric="关帽次数/年", value=float((o[1:] & ~o[:-1]).sum() / (len(o) / 252))))
    return pd.DataFrame(out)


def main():
    t = B.tasks()
    print(time.strftime("%H:%M:%S"), "tasks", len(t), flush=True)
    rows, diags = [], []
    with Pool(6) as pool:
        for i, (r, dg) in enumerate(pool.imap_unordered(one, t, chunksize=4)):
            rows += r; diags.append(dg)
    pd.DataFrame(rows).to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    pd.DataFrame(diags).to_csv(RUN / "diagnostics_by_name.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    bench_timing().to_csv(RUN / "bench_capoff_timing.csv", index=False, encoding="utf-8-sig")
    print(time.strftime("%H:%M:%S"), "done", len(rows), flush=True)


if __name__ == "__main__":
    main()
