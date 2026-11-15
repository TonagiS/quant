#!/usr/bin/env python3
"""降息周期指标：分 regime 描述 + 开关回测。见 runs/2026-11-15_rate_cycle/PREREG.md"""
from __future__ import annotations
import sys, time
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402
import bull_switch_backtest as B  # noqa: E402
import highvol_stocks_backtest as HV  # noqa: E402
import layered_v1_backtest as L  # noqa: E402
from single_asset_harness import run_pos, stats  # noqa: E402

ROOT = AB.ROOT
RUN = ROOT / "runs" / "2026-11-15_rate_cycle"
EV_CSV = ROOT / "data" / "macro" / "pboc_rate_events.csv"
COST = AB.COST
WARMUP = 250
START = pd.Timestamp("2006-01-04")
END = "2026-11-13"
PRI_INST = ["DEP1Y", "LOAN1Y", "LPR1Y", "OMO7D", "MLF1Y"]
RRR_INST = ["RRR_LARGE", "RRR_SMALL"]
HORIZON = 365
WINDOWS = [("full", None, None), ("y2008", "2008-01-01", "2008-12-31"), ("y2015", "2015-01-01", "2015-12-31"),
           ("y2016", "2016-01-01", "2016-12-31"), ("y2018", "2018-01-01", "2018-12-31"),
           ("y2022", "2022-01-01", "2022-12-31"), ("2023-2026", "2023-01-01", END)]
REGIMES = ["EASE", "NEUTRAL", "TIGHT"]


def event_dates(kind):
    ev = pd.read_csv(EV_CSV, parse_dates=["date"])
    inst = PRI_INST if kind == "PRI" else RRR_INST
    ev = ev[ev.instrument.isin(inst)]
    g = ev.groupby("date").direction.agg(lambda s: set(s))
    assert all(len(x) == 1 for x in g), "同日方向冲突"
    return pd.Series([next(iter(x)) for x in g], index=g.index).sort_index()


_EV = {}


def label(dates, kind):
    """label_t 只用公告日 < t 的事件（严格早于），见 PREREG。"""
    if kind not in _EV:
        _EV[kind] = event_dates(kind)
    ev = _EV[kind]
    d = pd.DatetimeIndex(dates)
    j = np.searchsorted(ev.index.values, d.values, side="left") - 1
    out = np.full(len(d), "NEUTRAL", dtype=object)
    ok = j >= 0
    last_d = np.where(ok, ev.index.values[np.clip(j, 0, None)], np.datetime64("NaT", "ns"))
    last_dir = np.where(ok, ev.values[np.clip(j, 0, None)], "")
    age = (d.values - last_d) / np.timedelta64(1, "D")
    out[ok & (last_dir == "cut") & (age <= HORIZON)] = "EASE"
    out[ok & (last_dir == "hike") & (age <= HORIZON)] = "TIGHT"
    return pd.Series(out, index=d)


def arms(F, lab_pri, lab_rrr):
    pos, _ = AB.arms_positions(F)
    A, Av = pos["A"], pos["A-vol"]
    P = {"A": A}
    for tag, lab in (("PRI", lab_pri), ("RRR", lab_rrr)):
        e = (lab == "EASE").to_numpy()
        P[f"SWa-{tag}"] = pd.Series(np.where(e, A, 0.0), index=A.index)
        P[f"SWb-{tag}"] = pd.Series(np.where(e, A, 0.5 * A), index=A.index)
        P[f"SWc-{tag}"] = pd.Series(np.where(e, Av, A), index=A.index)
    return P


def seg_count(m):
    m = np.asarray(m, bool)
    return int((m[1:] & ~m[:-1]).sum() + (1 if len(m) and m[0] else 0))


def regime_rows(base, ret, netA, psA, labs, ev):
    out = []
    for tag, lab in labs.items():
        for r in REGIMES:
            m = ev & (lab == r).to_numpy()
            if m.sum() < AB.MIN_STATS:
                continue
            rr = ret[m].fillna(0.0); na = netA[m]
            b, a = stats(rr), stats(na)
            out.append(dict(base, defn=tag, regime=r, days=int(m.sum()), segments=seg_count(m),
                            bh_cagr=b["cagr"], bh_vol=float(rr.std() * np.sqrt(252)), bh_mdd=b["mdd"], bh_calmar=b["calmar"],
                            a_cagr=a["cagr"], a_vol=float(na.std() * np.sqrt(252)), a_mdd=a["mdd"], a_calmar=a["calmar"],
                            a_avg_pos=float(psA[m].mean())))
    return out


def one(spec):
    panel = spec["panel"]
    try:
        df = B.load(spec) if panel != "bond" else HV.load(spec["path"])
    except Exception as e:  # noqa: BLE001
        return [], [], dict(spec, status=f"load_fail {e!r}"[:200])
    if df is None or len(df) < WARMUP + 500:
        return [], [], dict(spec, status="too_short")
    df = df.reset_index(drop=True)
    F = L.slim_factors(df)
    idx = F["close"].index
    labs = {"PRI": label(idx, "PRI"), "RRR": label(idx, "RRR")}
    P = arms(F, labs["PRI"], labs["RRR"])
    ret = F["ret"]
    nets = {a: run_pos(p, ret, cost=COST) for a, p in P.items()}
    dates = pd.Series(idx)
    ev = (np.arange(len(idx)) >= WARMUP) & (dates >= START).to_numpy()
    base = {k: spec.get(k, "") for k in ("panel", "code", "name", "group")}
    rows = []
    for w, a, bb in WINDOWS:
        m = ev.copy()
        if a:
            m &= ((dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(bb))).to_numpy()
        if m.sum() < AB.MIN_STATS:
            continue
        bh = None
        for arm, (net, ps) in nets.items():
            s = AB.wstats(net, ret, ps, m)
            rows.append(dict(base, window=w, arm=arm, **s)); bh = s
        rows.append(dict(base, window=w, arm="BH", n=bh["n"], cagr=bh["bh_cagr"], mdd=bh["bh_mdd"], calmar=bh["bh_calmar"],
                         avg_pos=1.0, turn=0.0, static_cagr=bh["bh_cagr"], static_mdd=bh["bh_mdd"], static_calmar=bh["bh_calmar"],
                         d_calmar_static=0.0, bh_cagr=bh["bh_cagr"], bh_mdd=bh["bh_mdd"], bh_calmar=bh["bh_calmar"]))
    netA, psA = nets["A"]
    reg = regime_rows(base, ret, netA, psA, labs, ev)
    return rows, reg, dict(base, status="ok", first=str(idx[0].date()), eval_from=str(idx[ev][0].date()) if ev.any() else "")


def tasks():
    t = [s for s in B.tasks() if s["panel"] in ("etf_long", "index", "hv_ashare")]
    t.append(dict(panel="bond", code="H11001", name="中证全债", group="债券", path=str(ROOT / "data" / "macro" / "H11001.csv")))
    return t


def main():
    t = tasks()
    print(time.strftime("%H:%M:%S"), "tasks", len(t), flush=True)
    rows, regs, meta = [], [], []
    with Pool(6) as pool:
        for i, (r, g, m) in enumerate(pool.imap_unordered(one, t, chunksize=4)):
            rows += r; regs += g; meta.append(m)
            if i % 300 == 0:
                print(time.strftime("%H:%M:%S"), i, flush=True)
    pd.DataFrame(rows).to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    pd.DataFrame(regs).to_csv(RUN / "regime_stats_long.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    pd.DataFrame(meta).to_csv(RUN / "coverage.csv", index=False, encoding="utf-8-sig")
    print(time.strftime("%H:%M:%S"), "done", len(rows), len(regs), flush=True)


if __name__ == "__main__":
    main()
