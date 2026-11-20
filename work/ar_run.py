#!/usr/bin/env python3
"""analyst_revision 主跑：覆盖率、IC 诊断、三族臂网格（允许调参）、成本敏感性。输出 runs/2026-11-21_analyst_revision/。"""
from __future__ import annotations
import itertools, json, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ar_bt as B  # noqa: E402
import ar_signal as S  # noqa: E402

RC = B.RC
RUN = B.RUN
RUN.mkdir(parents=True, exist_ok=True)
COSTS = (5, 10, 20)
WS, KEYS = (60, 90, 180), ("diff", "mrev")


def specs():
    out = [("S2+VT15", dict(kind="base")), ("S4", dict(kind="base", sector=True))]
    for W, k, ma, reb in itertools.product(WS, KEYS, (True, False), (10, 21)):
        out.append((f"REV|W{W}|{k}|{'MA60' if ma else 'noMA'}|R{reb}", dict(kind="rev", W=W, key=k, ma=ma, reb=reb)))
    for base, W, k, thr in itertools.product(("S2", "S4"), WS, KEYS, (0.0, -0.25)):
        out.append((f"FILT@{base}|W{W}|{k}|thr{thr}", dict(kind="filter", W=W, key=k, thr=thr, sector=base == "S4")))
    for base, W, k, lam in itertools.product(("S2", "S4"), WS, KEYS, (1.0, 3.0)):
        out.append((f"BLEND@{base}|W{W}|{k}|lam{lam:g}", dict(kind="blend", W=W, key=k, lam=lam, sector=base == "S4")))
    return out


def diagnostics():
    D = RC.data()
    cal, M, close = D["cal"], D["M"], D["closeT"]
    rows, ic = [], []
    fwd = close.shift(-22) / close.shift(-1) - 1  # 信号日次日起 21 日
    me = [d for d in cal.to_series().groupby(cal.to_period("M")).max() if d >= pd.Timestamp("2017-04-01")]
    for W, k in itertools.product(WS, KEYS):
        sig = B.etf_sig(W, k)
        for d in me:
            m = M.loc[d]
            s = sig.loc[d][m]
            rows.append(dict(W=W, key=k, date=d, pool=int(m.sum()), have=int(s.notna().sum())))
            x = pd.concat([s, fwd.loc[d][m]], axis=1).dropna()
            if len(x) >= 8:
                ic.append(dict(W=W, key=k, date=d, ic=x.iloc[:, 0].rank().corr(x.iloc[:, 1].rank()), n=len(x)))
    cov = pd.DataFrame(rows)
    cov["frac"] = cov.have / cov.pool
    icd = pd.DataFrame(ic)
    return cov, icd


def main():
    R = S.load_reports()
    rv = S.report_revisions(R)
    M, Ep = R["meta"], R["eps"]
    ok = Ep.dropna(subset=["fy"]).infoCode.unique()
    M = M.assign(y=M.date.dt.year, parsed=M.infoCode.isin(ok))
    data_stats = M.groupby("y").agg(reports=("infoCode", "size"), stocks=("stockCode", "nunique"), parsed=("parsed", "mean"))
    data_stats["revisions"] = rv.groupby(rv.date.dt.year).size()
    data_stats["rev_stocks"] = rv.groupby(rv.date.dt.year).stockCode.nunique()
    data_stats["up_share"] = rv.groupby(rv.date.dt.year).up.mean()
    data_stats["down_share"] = rv.groupby(rv.date.dt.year).dn.mean()
    data_stats.to_csv(RUN / "data_stats.csv")
    print(data_stats, flush=True)
    cov, icd = diagnostics()
    cov.to_csv(RUN / "coverage.csv", index=False)
    icd.to_csv(RUN / "ic_monthly.csv", index=False)
    covy = cov.assign(y=cov.date.dt.year).groupby(["W", "key", "y"]).frac.mean().unstack()
    ics = icd.groupby(["W", "key"]).ic.agg(["mean", "std", "count"])
    ics["t"] = ics["mean"] / ics["std"] * np.sqrt(ics["count"])
    ics.to_csv(RUN / "ic_summary.csv")
    print(covy.round(2), ics.round(3), sep="\n", flush=True)
    rows = []
    sp = specs()
    for bp in COSTS:
        for wn, sd in B.WINDOWS.items():
            for nm, kw in sp:
                if bp != 5 and not (nm in ("S2+VT15", "S4")):
                    continue
                d = B.arm(sd, bp / 1e4, **kw)
                rows.append(dict(cost_bp=bp, window=wn, arm=nm, start=str(RC.data()["cal"][d["lo"]].date()), **B.metrics(d)))
                if wn == "W2017H2":
                    for sg, (a, b) in B.SEGS.items():
                        rows.append(dict(cost_bp=bp, window=sg, arm=nm, **B.metrics(d, a, b)))
            print(bp, wn, flush=True)
    res = pd.DataFrame(rows)
    # 每族按 W2017H2@5bp 卡玛选最优，再补跑成本敏感性
    g = res[(res.cost_bp == 5) & (res.window == "W2017H2")]
    best = {}
    for fam in ("REV|", "FILT@S2", "FILT@S4", "BLEND@S2", "BLEND@S4"):
        sub = g[g.arm.str.startswith(fam)].sort_values("calmar", ascending=False)
        best[fam] = sub.arm.iloc[0]
    kwmap = dict(sp)
    extra = []
    for bp in (10, 20):
        for wn, sd in B.WINDOWS.items():
            for fam, nm in best.items():
                d = B.arm(sd, bp / 1e4, **kwmap[nm])
                extra.append(dict(cost_bp=bp, window=wn, arm=nm, start=str(RC.data()["cal"][d["lo"]].date()), **B.metrics(d)))
                if wn == "W2017H2":
                    for sg, (a, b) in B.SEGS.items():
                        extra.append(dict(cost_bp=bp, window=sg, arm=nm, **B.metrics(d, a, b)))
    res = pd.concat([res, pd.DataFrame(extra)], ignore_index=True)
    res.to_csv(RUN / "results.csv", index=False)
    # 选中臂在其他窗口的稳健性：同族所有参数在 W2019/W2021 的卡玛分布
    fam_rows = []
    for fam in best:
        for wn in B.WINDOWS:
            s = res[(res.cost_bp == 5) & (res.window == wn) & res.arm.str.startswith(fam)].calmar
            fam_rows.append(dict(family=fam, window=wn, n=len(s), median=s.median(), min=s.min(), max=s.max()))
    pd.DataFrame(fam_rows).to_csv(RUN / "family_dist.csv", index=False)
    (RUN / "best.json").write_text(json.dumps(best, ensure_ascii=False, indent=1))
    print(json.dumps(best, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
