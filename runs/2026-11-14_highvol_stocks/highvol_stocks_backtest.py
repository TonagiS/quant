#!/usr/bin/env python3
"""超高波动标的长历史检验。见 runs/2026-11-14_highvol_stocks/PREREG.md
臂复用 fastcap_ma_sens.arms_positions（A/A-vol/A-SV/A-FC/A-FC30/A-FC20）+ candidates_v2.policy_F；统计复用 ablation_v3.wstats。"""
from __future__ import annotations
import json, sys, time
from multiprocessing import Pool
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402
import candidates_v2_backtest as CV  # noqa: E402
import fastcap_ma_sens as MS  # noqa: E402
import layered_v1_backtest as L  # noqa: E402
from single_asset_harness import run_pos  # noqa: E402

ROOT = AB.ROOT
RUN = ROOT / "runs" / "2026-11-14_highvol_stocks"
DA = ROOT / "data" / "cache_highvol" / "ashare"
DX = ROOT / "data" / "cache_highvol" / "xmkt"
END = "2026-11-13"
COST = AB.COST
WARMUP, MIN_EVAL, MIN_BUCKET = 250, 500, 250
ARMS = ["A", "A-vol", "A-SV", "A-FC", "A-FC30", "A-FC20", "F"]
WINDOWS = [("full", None, None), ("2000-2002", "2000-01-01", "2002-12-31"), ("2007-2009", "2007-01-01", "2009-12-31"),
           ("y2008", "2008-01-01", "2008-12-31"), ("y2015", "2015-01-01", "2015-12-31"),
           ("y2018", "2018-01-01", "2018-12-31"), ("y2022", "2022-01-01", "2022-12-31"),
           ("2023-2026", "2023-01-01", END)]
BUCKETS = [("pit>=40", 0.40, 9.0), ("pit35-40", 0.35, 0.40), ("pit<35", 0.0, 0.35)]


def load(path):
    d = pd.read_csv(path, parse_dates=["date"]).sort_values("date").drop_duplicates("date")
    d = d[(d.close > 0) & d.close.notna() & (d.date <= pd.Timestamp(END))]
    out = pd.DataFrame({"date": d.date, "close": d.close.astype(float),
                        "amount": d["amount"] if "amount" in d else np.nan,
                        "volume": d["volume"] if "volume" in d else np.nan})
    return out.reset_index(drop=True)


def one(args):
    panel, code, group, path = args
    try:
        df = load(path)
    except Exception as e:  # noqa: BLE001
        return [], dict(panel=panel, code=code, group=group, status=f"load_fail {e!r}"[:200])
    if len(df) < WARMUP + MIN_EVAL:
        return [], dict(panel=panel, code=code, group=group, status="too_short", rows=len(df))
    F = L.slim_factors(df)
    pos, _ = MS.arms_positions(F)
    pos = {a: pos[a] for a in ARMS if a in pos}
    pos["F"] = CV.policy_F(F)
    ret = F["ret"]
    nets = {a: run_pos(p, ret, cost=COST) for a, p in pos.items()}
    dates = pd.Series(ret.index)
    ev = np.arange(len(ret)) >= WARMUP
    vol250 = ret.rolling(250, min_periods=250).std() * np.sqrt(252)
    vprev = vol250.shift(1)
    med_vol = float(vol250[ev].median())
    big = int((ret.abs() > 0.5).sum())
    base = dict(panel=panel, code=code, group=group, first=str(dates.iloc[0].date()), last=str(dates.iloc[-1].date()),
                eval_start=str(dates.iloc[WARMUP].date()), med_vol250=med_vol)
    rows = []

    def emit(kind, w, m, extra=None):
        if m.sum() < (MIN_BUCKET if kind == "pit" else AB.MIN_STATS):
            return
        bh = None
        for arm, (net, ps) in nets.items():
            s = AB.wstats(net, ret, ps, m)
            rows.append(dict(base, kind=kind, window=w, arm=arm, **s, **(extra or {})))
            bh = s
        rows.append(dict(base, kind=kind, window=w, arm="BH", n=bh["n"], cagr=bh["bh_cagr"], mdd=bh["bh_mdd"],
                         calmar=bh["bh_calmar"], avg_pos=1.0, turn=0.0, static_cagr=bh["bh_cagr"],
                         static_mdd=bh["bh_mdd"], static_calmar=bh["bh_calmar"], d_calmar_static=0.0,
                         bh_cagr=bh["bh_cagr"], bh_mdd=bh["bh_mdd"], bh_calmar=bh["bh_calmar"], **(extra or {})))

    for w, a, b in WINDOWS:
        m = ev.copy()
        if a:
            m &= ((dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b))).to_numpy()
        ws = vprev[m]
        emit("window", w, m, dict(vol250_at_start=float(ws.iloc[0]) if len(ws) else np.nan))
    for bname, lo, hi in BUCKETS:
        m = ev & ((vprev >= lo) & (vprev < hi)).fillna(False).to_numpy()
        emit("pit", bname, m)
    share = {b: float(((vprev[ev] >= lo) & (vprev[ev] < hi)).mean()) for b, lo, hi in BUCKETS}
    return rows, dict(base, status="ok", rows=len(df), n_eval=int(ev.sum()), big_moves_gt50pct=big,
                      **{f"share_{k}": v for k, v in share.items()})


def tasks():
    t = []
    am = pd.read_csv(RUN / "ashare_pull_meta.csv")
    for c in am[am.status.isin(["ok", "cached"])].code:
        p = DA / f"{c}.csv"
        if p.exists():
            t.append(("ashare", c, "A股个股", p))
    xm = pd.read_csv(RUN / "xmkt_pull_meta.csv")
    for _, r in xm[xm.status == "ok"].iterrows():
        t.append(("xmkt", r.code, r.group, DX / f"{r.code.replace('^', '_').replace('.', '_')}.csv"))
    return t


def main():
    t = tasks()
    print(time.strftime("%H:%M:%S"), "tasks", len(t), flush=True)
    rows, meta = [], []
    with Pool(6) as pool:
        for i, (r, m) in enumerate(pool.imap_unordered(one, t, chunksize=4)):
            rows += r; meta.append(m)
            if i % 200 == 0:
                print(time.strftime("%H:%M:%S"), i, flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    pd.DataFrame(meta).to_csv(RUN / "coverage.csv", index=False, encoding="utf-8-sig")
    print(time.strftime("%H:%M:%S"), "done", len(res), flush=True)


if __name__ == "__main__":
    main()
