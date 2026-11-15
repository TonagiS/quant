#!/usr/bin/env python3
"""汇总：regime 描述、开关臂 vs A、采用判定。见 runs/2026-11-15_rate_cycle/PREREG.md"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-15_rate_cycle"
PANELS = ["index", "etf_long", "hv_ashare"]
CRASH = ["y2008", "y2015", "y2018"]
NEW = ["SWa-PRI", "SWb-PRI", "SWc-PRI", "SWa-RRR", "SWb-RRR", "SWc-RRR"]
DESC = ["沪深300全收益", "中证500全收益", "创业板指", "中证银行", "中证红利全收益", "中证全债", "黄金ETF华安(代理)"]


def main():
    r = pd.read_csv(RUN / "results_long.csv")
    g = pd.read_csv(RUN / "regime_stats_long.csv")
    # 1) 指数 regime 表
    gi = g[g.name.isin(DESC)].copy()
    gi["name"] = pd.Categorical(gi.name, DESC)
    gi = gi.sort_values(["defn", "name", "regime"])
    gi.to_csv(RUN / "regime_stats_index.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    # 2) 面板 regime 中位
    cols = ["bh_cagr", "bh_vol", "bh_mdd", "bh_calmar", "a_cagr", "a_vol", "a_mdd", "a_calmar", "a_avg_pos", "days", "segments"]
    gp = g[g.panel.isin(PANELS)].groupby(["defn", "panel", "regime"])[cols].median()
    gp["n_names"] = g[g.panel.isin(PANELS)].groupby(["defn", "panel", "regime"]).size()
    gp.reset_index().to_csv(RUN / "regime_stats_panel.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    # 3) 窗口 x 臂 中位
    rp = r[r.panel.isin(PANELS)]
    s = rp.groupby(["panel", "window", "arm"]).agg(n=("code", "size"), cagr=("cagr", "median"), mdd=("mdd", "median"),
                                                     calmar=("calmar", "median"), avg_pos=("avg_pos", "median"),
                                                     turn=("turn", "median"), d_static=("d_calmar_static", "median"),
                                                     static_pos=("d_calmar_static", lambda x: float((x > 0).mean())))
    s.reset_index().to_csv(RUN / "summary.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    # 配对：臂 - A
    pa = rp[rp.arm == "A"].set_index(["panel", "code", "window"])
    pw = []
    for arm in NEW:
        x = rp[rp.arm == arm].set_index(["panel", "code", "window"])
        d = (x[["cagr", "mdd", "calmar"]] - pa.loc[x.index, ["cagr", "mdd", "calmar"]]).reset_index()
        for (p, w), q in d.groupby(["panel", "window"]):
            pw.append(dict(arm=arm, panel=p, window=w, n=len(q), d_cagr_med=q.cagr.median(), d_mdd_med=q.mdd.median(),
                           d_calmar_med=q.calmar.median(), d_calmar_pos=float((q.calmar > 0).mean())))
    pd.DataFrame(pw).to_csv(RUN / "pairwise_vs_A.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    # 4) 采用判定
    med = s.reset_index().set_index(["panel", "window", "arm"])
    verdict = {}
    for arm in NEW:
        c1, c2, c3 = [], [], []
        for p in PANELS:
            for w in CRASH:
                if (p, w, arm) not in med.index or med.loc[(p, w, arm), "n"] < 5:
                    continue
                a, b = med.loc[(p, w, "A")], med.loc[(p, w, arm)]
                c1.append(dict(panel=p, window=w, n=int(b.n), mdd_A=a.mdd, mdd=b.mdd, calmar_A=a.calmar, calmar=b.calmar,
                               ok=bool(b.mdd >= a.mdd - 0.03 and b.calmar >= a.calmar)))
            a, b = med.loc[(p, "full", "A")], med.loc[(p, "full", arm)]
            c2.append(dict(panel=p, calmar_A=a.calmar, calmar=b.calmar, ok=bool(b.calmar >= a.calmar)))
            c3.append(dict(panel=p, d_static=b.d_static, pos=b.static_pos, ok=bool(b.d_static > 0 and b.static_pos > 0.5)))
        verdict[arm] = dict(c1_ok=all(x["ok"] for x in c1), c2_ok=all(x["ok"] for x in c2), c3_ok=all(x["ok"] for x in c3),
                            adopt=all(x["ok"] for x in c1 + c2 + c3), in_judgement=arm.endswith("PRI"), c1=c1, c2=c2, c3=c3)
    json.dump(verdict, open(RUN / "verdict.json", "w"), ensure_ascii=False, indent=1, default=float)
    for k, v in verdict.items():
        print(k, "c1", v["c1_ok"], "c2", v["c2_ok"], "c3", v["c3_ok"], "ADOPT", v["adopt"])


if __name__ == "__main__":
    main()
