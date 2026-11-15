#!/usr/bin/env python3
"""汇总：逐笔统计 / 随机基线 / 组合判定。见 runs/2026-11-15_vol_breakout/PREREG.md"""
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-15_vol_breakout"


def tstats(df):
    r = df.ret
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else np.nan
    return dict(n=len(r), win=(r > 0).mean(), mean=r.mean(), median=r.median(), hold=df.hold.mean(), pf=pf)


def main(panel):
    out = RUN / panel
    tr = pd.read_csv(out / "trades_chain.csv.gz", parse_dates=["sig_date"])
    skip = (tr.kind == "skip_limitup").sum()
    ch = tr[tr.kind.isin(["main", "intra"])]
    rows = []
    for k, g in ch.groupby("kind"):
        rows.append(dict(group="all", kind=k, **tstats(g)))
        for y, gy in g.groupby(g.sig_date.dt.year): rows.append(dict(group=str(y), kind=k, **tstats(gy)))
        for rs, gr in g.groupby("reason"): rows.append(dict(group="reason_" + rs, kind=k, **tstats(gr)))
    ts = pd.DataFrame(rows); ts.to_csv(out / "trade_stats.csv", index=False)
    rd = pd.read_csv(out / "random_trades.csv.gz", parse_dates=["sig_date"])
    rr = pd.DataFrame([dict(base=b, draw=d, **tstats(g)) for (b, d), g in rd.groupby(["kind", "draw"])])
    rr.to_csv(out / "random_draw_stats.csv", index=False)
    real = ts[(ts.group == "all") & (ts.kind == "main")].iloc[0]
    cmp_rows = []
    for b, g in rr.groupby("base"):
        cmp_rows.append(dict(base=b, real_mean=real["mean"], rnd_mean_avg=g["mean"].mean(), rnd_mean_min=g["mean"].min(),
                             rnd_mean_max=g["mean"].max(), real_rank=int((g["mean"] < real["mean"]).sum()),
                             real_median=real["median"], rnd_median_avg=g["median"].mean(),
                             real_win=real["win"], rnd_win_avg=g["win"].mean(), real_pf=real["pf"], rnd_pf_avg=g["pf"].mean(),
                             rnd_hold_avg=g["hold"].mean()))
    cmpd = pd.DataFrame(cmp_rows); cmpd.to_csv(out / "random_compare.csv", index=False)
    # 按年 vs 随机（均值）
    ry = rd.groupby(["kind", rd.sig_date.dt.year]).ret.mean().unstack(0)
    realy = ch[ch.kind == "main"].groupby(ch.sig_date.dt.year).ret.agg(["size", "mean"])
    yy = realy.join(ry); yy.to_csv(out / "yearly_vs_random.csv")
    edge = all((c.real_mean > c.rnd_mean_max) and (c.real_median > c.rnd_median_avg) for c in cmpd.itertuples())
    pm = pd.read_csv(out / "portfolio_metrics.csv")
    f = pm[pm.window == "full"].set_index("arm")
    s, st, ew = f.loc["strategy"], f.loc["STATIC"], f.loc["EW-M"]
    c1 = bool(s.calmar > st.calmar and s.calmar > ew.calmar); c2 = bool(s.cagr > st.cagr)
    verdict = dict(panel=panel, trade_edge=bool(edge), port_pass=bool(c1 and c2), crit1_calmar=c1, crit2_cagr_vs_static=c2,
                   cagr_gt_ew=bool(s.cagr > ew.cagr), skip_limitup_signals=int(skip),
                   full=dict(strategy=s[["cagr", "vol", "mdd", "calmar"]].to_dict(), static=st[["cagr", "vol", "mdd", "calmar"]].to_dict(),
                             ew=ew[["cagr", "vol", "mdd", "calmar"]].to_dict()),
                   extra=json.load(open(out / "portfolio_extra.json")))
    json.dump(verdict, open(out / "verdict.json", "w"), indent=1, ensure_ascii=False, default=float)
    pd.set_option("display.width", 250)
    print(ts[ts.group.isin(["all"]) | ts.group.str.startswith("reason")].round(4).to_string())
    print(cmpd.round(4).T.to_string())
    print(yy.round(4).to_string())
    print(pm.round(3).to_string())
    print(pd.read_csv(out / "portfolio_yearly.csv").round(3).to_string())
    print(json.dumps(verdict, indent=1, ensure_ascii=False, default=float))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all_a")
