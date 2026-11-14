#!/usr/bin/env python3
"""A-FAST 诊断汇总 + 用户假设 H1/H2 机械判定（见 PREREG）。"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-14_bull_fast"
PANELS = ["index", "etf_long", "hv_ashare", "hv_xmkt", "cs951", "xmkt"]
JUDGE = ["index", "etf_long", "hv_ashare", "hv_xmkt"]
FL = {"MKT": "A-BULL-MKT", "OWN": "A-BULL-OWN", "FAST-MKT": "A-FAST-MKT", "FAST-OWN": "A-FAST-OWN"}


def main():
    d = pd.read_csv(RUN / "diagnostics_by_name.csv")
    d = d[d.status == "ok"]
    p = pd.read_csv(RUN / "pairwise.csv")
    cols = [c for c in d.columns if "|" in c]
    out = []
    for pn in PANELS:
        g = d[d.panel == pn]
        for k in FL:
            rec = dict(panel=pn, flag=k, n=len(g))
            for c in [c for c in cols if c.startswith(k + "|")]:
                m = c.split("|", 1)[1]
                v = g[c]
                rec[m] = v.median()
                if m.endswith("_lag") or m.endswith("_days_on") or m.endswith("_sumd120") or m.endswith("_sumd250"):
                    rec[m + "_n"] = int(v.notna().sum())
            for w in ("y2008", "y2015"):
                ex, on = g.get(f"{k}|{w}_mdd_excess"), g.get(f"{k}|{w}_mdd_excess_on")
                if ex is not None:
                    neg = ex < 0  # 有多出损失的名称
                    share = (on[neg] / ex[neg]).clip(-5, 5)
                    rec[f"{w}_excess_on_share_med"] = share.median() if neg.any() else np.nan
                    rec[f"{w}_excess_total_share_on"] = on[neg].sum() / ex[neg].sum() if neg.any() else np.nan
                    rec[f"{w}_n_names_extra_loss"] = int(neg.sum())
            out.append(rec)
    D = pd.DataFrame(out)
    D.to_csv(RUN / "diagnostics_summary.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    # 假设判定
    hyp = {}
    for fast, slow in (("FAST-MKT", "MKT"), ("FAST-OWN", "OWN")):
        per = {}
        for pn in JUDGE:
            gf = p[(p.panel == pn) & (p.kind == "window") & (p.window == "full") & (p.arm == FL[fast])]
            gs = p[(p.panel == pn) & (p.kind == "window") & (p.window == "full") & (p.arm == FL[slow])]
            r = D[(D.panel == pn) & (D.flag == fast)].iloc[0]
            h1 = bool(gf.d_cagr.iloc[0] > gs.d_cagr.iloc[0] and r["contrib_first60"] > 0)
            shares = [r.get(f"{w}_excess_on_share_med") for w in ("y2008", "y2015")]
            shares = [s for s in shares if pd.notna(s)]
            h2 = bool(r["contrib_postpeak"] >= 0 and (np.median(shares) < 0.5 if shares else False))
            per[pn] = dict(H1=h1, H2=h2, d_cagr_fast=float(gf.d_cagr.iloc[0]), d_cagr_slow=float(gs.d_cagr.iloc[0]),
                           first60=float(r["contrib_first60"]), postpeak=float(r["contrib_postpeak"]),
                           excess_on_share=[float(s) for s in shares])
        nboth = sum(v["H1"] and v["H2"] for v in per.values())
        hyp[fast] = dict(per_panel=per, n_both=nboth, supported=nboth >= 3)
    (RUN / "hypothesis.json").write_text(json.dumps(hyp, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(hyp, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
