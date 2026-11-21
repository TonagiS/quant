#!/usr/bin/env python3
"""卫星权重扫描（2026-11-21 sat_weight_scan）：卫星 0/10/20/30/40% × {S4, S4+CREDIT} × 底仓 {C3, DIV-IV} × {5,20}bp。
规则见 runs/2026-11-21_sat_weight_scan/FREEZE.md。复用 prereg_satellite（滚动起点/分段/mix 约定）与 slowdecline（社融乘数），只 import。样本内。
用法：python3 work/sat_weight_scan.py run | smoke <outdir>"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prereg_satellite as PS  # noqa: E402
import slowdecline as S  # noqa: E402

K, RC, G = PS.K, PS.RC, PS.G
RUN = K.ROOT / "runs" / "2026-11-21_sat_weight_scan"
COSTS = (0.0005, 0.0020)
WSAT = (0.0, 0.1, 0.2, 0.3, 0.4)
Q_SHARE, Q_SEG = 0.70, 3          # "小权重改善年化且不降卡玛"的描述性门槛


def sats(cost):
    s4 = K.sat_run(RC.START_W["W2013"], cost, sector=True)
    cr = np.asarray(S.G["credit"], float)
    assert len(cr) == len(s4["ret"])
    s4c = K.apply_mult(s4, cr, cost)        # 社融乘数：t 日决定，t+2 生效（与 slowdecline/parts_v2 相同）
    return {"S4": s4, "S4+CREDIT": s4c}


def main(mode, outdir=None):
    smoke = mode == "smoke"
    S.setup()
    PS.setup(starts_n=3 if smoke else None)
    out = Path(outdir) if smoke else RUN
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows = []
    for c in COSTS:
        cs = PS.cores(c)
        st = sats(c)
        for cn, core in cs.items():
            for sn, sd in st.items():
                for w in WSAT:
                    ev = PS.evaluate(core, sd, c, wc=1.0 - w)
                    for r in PS.rows_of(ev, sn, cn, c):
                        r["w_sat"] = w
                        rows.append(r)
        print("cost", c, round(time.time() - t0), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "all_metrics.csv.gz", index=False)
    summarize(df, out)
    print("done", round(time.time() - t0), flush=True)


def summarize(df, out):
    key = ["arm", "core", "cost_bp", "kind", "key"]
    base = df[df.w_sat == 0].set_index(key)[["cagr", "mdd", "calmar"]]
    rows = []
    for (arm, core, cb, w), g in df[df.w_sat > 0].groupby(["arm", "core", "cost_bp", "w_sat"]):
        x = g.set_index(key)[["cagr", "mdd", "calmar"]]
        b = base.reindex(x.index)
        d = x - b
        st, sg = d.xs("start", level="kind"), d.xs("seg", level="kind")
        xs = x.xs("start", level="kind")
        both = (st.cagr > 0) & (st.calmar >= 0)
        both_seg = (sg.cagr > 0) & (sg.calmar >= 0)
        rows.append({"sat": arm, "core": core, "cost_bp": int(cb), "w_sat": w,
                     "calmar_med": xs.calmar.median(), "calmar_p10": xs.calmar.quantile(.1), "calmar_p90": xs.calmar.quantile(.9),
                     "cagr_med": xs.cagr.median(), "mdd_med": xs.mdd.median(), "mdd_worst": xs.mdd.min(),
                     "d_cagr_med": st.cagr.median(), "d_calmar_med": st.calmar.median(), "d_mdd_med": st.mdd.median(),
                     "share_cagr_up": float((st.cagr > 0).mean()), "share_calmar_nondown": float((st.calmar >= 0).mean()),
                     "share_both": float(both.mean()), "seg_both": int(both_seg.sum()),
                     **{f"seg_dcalmar_{k}": float(sg.calmar.get(k, np.nan)) for k in PS.SEGS},
                     **{f"seg_dcagr_{k}": float(sg.cagr.get(k, np.nan)) for k in PS.SEGS},
                     "qualifies": bool(both.mean() >= Q_SHARE and both_seg.sum() >= Q_SEG)})
    T = pd.DataFrame(rows)
    T.to_csv(out / "summary.csv", index=False)
    b0 = df[(df.w_sat == 0) & (df.kind == "start")].groupby(["core", "cost_bp", "arm"]).agg(
        calmar_med=("calmar", "median"), cagr_med=("cagr", "median"), mdd_med=("mdd", "median")).reset_index()
    b0.to_csv(out / "pure_core.csv", index=False)
    (out / "qualifies.json").write_text(json.dumps(
        T[T.qualifies][["sat", "core", "cost_bp", "w_sat", "share_both", "seg_both"]].to_dict("records"), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
