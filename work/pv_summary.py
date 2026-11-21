#!/usr/bin/env python3
"""pool_variants 汇总：按 FREEZE 判定。python3 work/pv_summary.py"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-21_pool_variants"
d = pd.read_csv(RUN / "results_long.csv.gz")
sz = pd.read_csv(RUN / "pool_sizes.csv").set_index("variant")
rows = []
for (strat, bp), g in d.groupby(["strat", "cost_bp"]):
    base = g[g.variant == "BASE"]
    bst = base[base.kind == "start"].set_index("key").calmar
    bsg = base[base.kind == "seg"].set_index("key").calmar
    for v, x in g.groupby("variant"):
        st = x[x.kind == "start"].set_index("key").calmar.reindex(bst.index)
        sg = x[x.kind == "seg"].set_index("key").calmar.reindex(bsg.index)
        dd = st - bst
        share = float((dd > 0).sum() / len(dd))            # 缺失按非正
        med = float(dd.median()) if dd.notna().any() else np.nan
        segd = sg - bsg
        npos = int((segd > 0).sum())
        f = x[x.kind == "full"].iloc[0]
        rows.append(dict(strat=strat, cost_bp=bp, variant=v, cagr=f.cagr, mdd=f.mdd, calmar=f.calmar, turn=f.turn,
                         sat_lo=f.sat_lo, n_start_ok=int(st.notna().sum()), roll_med=float(st.median()),
                         share_up=share, med_d=med, n_seg_pos=npos,
                         **{f"seg_{k}": segd.get(k, np.nan) for k in bsg.index},
                         pass_=bool(share >= 0.70 and med > 0.05 and npos >= 3) if v != "BASE" else None,
                         size_med=sz.loc[v, "size_med"], size_min=sz.loc[v, "size_min"], n_ever=sz.loc[v, "n_ever"]))
T = pd.DataFrame(rows)
T.to_csv(RUN / "summary.csv", index=False)
# 稳健通过
p5 = T[T.cost_bp == 5].set_index(["strat", "variant"]).pass_
p20 = T[T.cost_bp == 20].set_index(["strat", "variant"]).pass_
ver = {}
for (s, v), ok in p5.items():
    if v == "BASE":
        continue
    ver.setdefault(s, {})[v] = "稳健通过" if ok and p20.get((s, v)) else ("通过(仅5bp)" if ok else "未通过")
# 最佳池
c5 = T[(T.cost_bp == 5) & (T.variant != "BASE")]
passed = [v for v, r in ver["C3 60%+S4"].items() if r != "未通过"] + [v for s in ("S4", "S2+VT15") for v, r in ver[s].items() if r != "未通过"]
passed = sorted(set(passed))
if passed:
    best = c5[(c5.strat == "C3 60%+S4") & c5.variant.isin(passed)].sort_values("med_d", ascending=False).variant.iloc[0]
    best_note = "通过"
else:
    best = c5[c5.strat == "S4"].sort_values("med_d", ascending=False).variant.iloc[0]
    best_note = "无通过者；取 S4 中位Δ最大者，未通过"
top3 = list(c5[c5.strat == "S4"].sort_values("med_d", ascending=False).variant.iloc[:3])
# 零假设
N = pd.read_csv(RUN / "null_long.csv.gz")
nul = {}
for (u, strat), g in N.groupby(["universe", "strat"]):
    st = g[g.kind == "start"].groupby("seed").calmar.median()
    nul[(u, strat)] = st
pct = []
for v in ["BASE", best] + top3:
    for strat in ("S4", "C3 60%+S4"):
        x = T[(T.cost_bp == 5) & (T.strat == strat) & (T.variant == v)].iloc[0]
        for u in ("elig", "top80"):
            s = nul[(u, strat)]
            pct.append(dict(variant=v, strat=strat, universe=u, roll_med=x.roll_med, null_med=float(s.median()),
                            null_p5=float(s.quantile(0.05)), null_p95=float(s.quantile(0.95)),
                            pctile=float((s < x.roll_med).mean())))
PN = pd.DataFrame(pct).drop_duplicates(["variant", "strat", "universe"])
PN.to_csv(RUN / "null_percentiles.csv", index=False)
json.dump(dict(verdict=ver, best=best, best_note=best_note, top3_s4=top3), open(RUN / "verdict.json", "w"), ensure_ascii=False, indent=1)
pd.set_option("display.width", 300); pd.set_option("display.max_rows", 300)
cols = ["strat", "cost_bp", "variant", "cagr", "mdd", "calmar", "n_start_ok", "share_up", "med_d", "n_seg_pos", "pass_", "size_med", "sat_lo"]
print(T[cols].round(3).to_string())
print(json.dumps(ver, ensure_ascii=False, indent=0)); print("best", best, best_note, "top3", top3)
print(PN.round(3).to_string())
