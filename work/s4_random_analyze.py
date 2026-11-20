#!/usr/bin/env python3
"""S4 随机零假设：百分位、p 值、板块上限解释比例、判定。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-21_s4_random"
WIN = ["W2013", "W2017", "W2019"]
SEGS = WIN + ["Y2015", "Y2018", "S2021_24", "S2023_now"]
METS = ["cagr", "mdd", "calmar", "sharpe"]
PAIRS = [("S4", "N1"), ("S4", "N2"), ("S4", "N3"), ("INCUMBENT", "N3"), ("INCUMBENT", "N1"), ("INCUMBENT", "N2")]


def main():
    nm = pd.read_csv(RUN / "null_metrics.csv.gz")
    st = pd.read_csv(RUN / "strategies.csv")
    rows, summ = [], []
    for bp in (5, 20):
        for null in ("N1", "N2", "N3"):
            for seg in SEGS:
                z = nm[(nm.null == null) & (nm.cost_bp == bp) & (nm.window == seg)]
                for m in METS:
                    v = z[m].to_numpy()
                    summ.append({"cost_bp": bp, "null": null, "window": seg, "metric": m, "n": len(v),
                                 "median": float(np.nanmedian(v)), "p05": float(np.nanpercentile(v, 5)),
                                 "p95": float(np.nanpercentile(v, 95)), "mean": float(np.nanmean(v))})
        for arm, null in PAIRS:
            for seg in SEGS:
                z = nm[(nm.null == null) & (nm.cost_bp == bp) & (nm.window == seg)]
                s = st[(st.arm == arm) & (st.cost_bp == bp) & (st.window == seg)].iloc[0]
                r = {"cost_bp": bp, "arm": arm, "null": null, "window": seg}
                for m in METS:
                    v = z[m].to_numpy()
                    r[f"{m}_strat"] = float(s[m])
                    r[f"{m}_pctile"] = float(np.mean(v < s[m]) + 0.5 * np.mean(v == s[m]))   # MDD 为负数，越大越浅，同向
                k = int(np.sum(z.calmar.to_numpy() >= s.calmar))
                r["p_calmar"] = (k + 1) / (len(z) + 1)
                r["p_sharpe"] = (int(np.sum(z.sharpe.to_numpy() >= s.sharpe)) + 1) / (len(z) + 1)
                rows.append(r)
    ranks = pd.DataFrame(rows)
    sm = pd.DataFrame(summ)
    ranks.to_csv(RUN / "ranks.csv", index=False)
    sm.to_csv(RUN / "null_summary.csv", index=False)

    def med(null, seg, m, bp):
        return float(sm[(sm.null == null) & (sm.window == seg) & (sm.metric == m) & (sm.cost_bp == bp)]["median"].iloc[0])

    def sv(arm, seg, m, bp):
        return float(st[(st.arm == arm) & (st.window == seg) & (st.cost_bp == bp)][m].iloc[0])

    expl = []
    for bp in (5, 20):
        for seg in SEGS:
            for m in ("calmar", "cagr", "mdd"):
                d_strat = sv("S4", seg, m, bp) - sv("INCUMBENT", seg, m, bp)
                d_null = med("N1", seg, m, bp) - med("N3", seg, m, bp)
                expl.append({"cost_bp": bp, "window": seg, "metric": m, "S4_minus_inc": d_strat,
                             "N1med_minus_N3med": d_null, "share_explained": d_null / d_strat if abs(d_strat) > 1e-9 else np.nan,
                             "N2med_minus_N1med(filter_value_under_cap)": med("N2", seg, m, bp) - med("N1", seg, m, bp)})
    ex = pd.DataFrame(expl)
    ex.to_csv(RUN / "sector_cap_explained.csv", index=False)

    ver = {}
    for arm, null in (("S4", "N1"), ("INCUMBENT", "N3"), ("S4", "N2"), ("INCUMBENT", "N1")):
        for bp in (5, 20):
            p = {w: float(ranks[(ranks.arm == arm) & (ranks.null == null) & (ranks.window == w) & (ranks.cost_bp == bp)].p_calmar.iloc[0]) for w in WIN}
            if sum(v <= 0.05 for v in p.values()) >= 2 and p["W2013"] <= 0.05:
                v = "打过随机（显著）"
            elif max(p.values()) <= 0.20:
                v = "略好于随机、不显著"
            elif min(p.values()) > 0.20:
                v = "与随机无差别"
            else:
                v = "混合（各窗口不一致）"
            ver[f"{arm}_vs_{null}_{bp}bp"] = {"p_calmar": p, "verdict": v}
    (RUN / "verdict.json").write_text(json.dumps(ver, ensure_ascii=False, indent=2))
    with pd.option_context("display.width", 250, "display.max_rows", 400):
        cols = ["cost_bp", "arm", "null", "window", "calmar_strat", "calmar_pctile", "p_calmar", "cagr_pctile", "mdd_pctile", "sharpe_pctile"]
        print(ranks[cols].round(3).to_string())
        p = sm[sm.metric.isin(["calmar", "cagr", "mdd"])].pivot_table(index=["cost_bp", "window"], columns=["null", "metric"], values=["median"]).round(3)
        print(p.to_string())
        print(sm[(sm.metric == "calmar")][["cost_bp", "null", "window", "p05", "median", "p95"]].round(3).to_string())
        print(ex.round(3).to_string())
    print(json.dumps(ver, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
