#!/usr/bin/env python3
"""sat_weight_scan 补表：冻结脚本 summary.csv 的 seg_dcalmar_*/seg_dcagr_* 列因 MultiIndex 取值写成 NaN（seg_both/qualifies 不受影响）。
本脚本只读 all_metrics.csv.gz 重算分段 Δ，写 segments.csv；不改冻结脚本、不改判定。"""
from pathlib import Path
import pandas as pd
RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-21_sat_weight_scan"
d = pd.read_csv(RUN / "all_metrics.csv.gz")
s = d[d.kind == "seg"]
b = s[s.w_sat == 0].set_index(["arm", "core", "cost_bp", "key"])[["cagr", "mdd", "calmar"]]
x = s[s.w_sat > 0].set_index(["arm", "core", "cost_bp", "key", "w_sat"])[["cagr", "mdd", "calmar"]]
dd = x - b.reindex(x.index.droplevel("w_sat")).set_axis(x.index)
out = dd.reset_index().rename(columns={"arm": "sat", "key": "segment", "cagr": "d_cagr", "mdd": "d_mdd", "calmar": "d_calmar"})
out["both"] = (out.d_cagr > 0) & (out.d_calmar >= 0)
out.sort_values(["sat", "core", "cost_bp", "w_sat", "segment"]).to_csv(RUN / "segments.csv", index=False)
print(out.pivot_table(index=["sat", "core", "cost_bp", "w_sat"], columns="segment", values="d_calmar").round(3).to_string())
