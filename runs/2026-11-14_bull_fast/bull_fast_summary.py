#!/usr/bin/env python3
"""A-BULL 汇总与采用判定（按 PREREG 机械执行）。"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-14_bull_fast"
JUDGE = ["index", "etf_long", "hv_ashare", "hv_xmkt"]
CRASH = ["2000-2002", "y2008", "y2015", "y2018"]


def main():
    res = pd.read_csv(RUN / "results_long.csv")
    keys = ["panel", "kind", "window", "arm"]
    s = res.groupby(keys).agg(n=("code", "size"), cagr=("cagr", "median"), mdd=("mdd", "median"), calmar=("calmar", "median"),
                              avg_pos=("avg_pos", "median"), turn=("turn", "median"), d_static=("d_calmar_static", "median"),
                              pos_static=("d_calmar_static", lambda x: (x > 0).mean()),
                              on_fast_mkt=("on_FAST-MKT", "median"), on_fast_own=("on_FAST-OWN", "median")).reset_index()
    a = res[res.arm == "A"].set_index(["panel", "kind", "window", "code"])
    pw = []
    for arm in ["A-FC", "A-BULL-MKT", "A-BULL-OWN", "A-FAST-MKT", "A-FAST-OWN"]:
        b = res[res.arm == arm].set_index(["panel", "kind", "window", "code"]).join(a, rsuffix="_A", how="inner")
        for k, g in b.groupby(level=[0, 1, 2]):
            pw.append(dict(panel=k[0], kind=k[1], window=k[2], arm=arm, n=len(g), d_cagr=(g.cagr - g.cagr_A).median(),
                           d_mdd=(g.mdd - g.mdd_A).median(), win_calmar=(g.calmar > g.calmar_A).mean(),
                           win_cagr=(g.cagr > g.cagr_A).mean()))
    pw = pd.DataFrame(pw)
    s.to_csv(RUN / "summary.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    pw.to_csv(RUN / "pairwise.csv", index=False, encoding="utf-8-sig", float_format="%.4f")

    def g(panel, w, arm, col):
        r = s[(s.panel == panel) & (s.kind == "window") & (s.window == w) & (s.arm == arm)]
        return (float(r[col].iloc[0]), int(r.n.iloc[0])) if len(r) else (np.nan, 0)

    out = {}
    for arm in ["A-FAST-MKT", "A-FAST-OWN", "A-BULL-MKT", "A-BULL-OWN", "A-FC"]:
        c1, c2, c3 = [], [], []
        for p in JUDGE:
            for w in CRASH:
                (ma, n), (mb, _) = g(p, w, "A", "mdd"), g(p, w, arm, "mdd")
                if n < 5:
                    continue
                ca, cb = g(p, w, "A", "calmar")[0], g(p, w, arm, "calmar")[0]
                c1.append(dict(panel=p, window=w, n=n, mdd_A=ma, mdd=mb, calmar_A=ca, calmar=cb, ok=bool(mb >= ma - 0.03 and cb >= ca)))
            ca, cb = g(p, "full", "A", "calmar")[0], g(p, "full", arm, "calmar")[0]
            c2.append(dict(panel=p, calmar_A=ca, calmar=cb, ok=bool(cb >= ca)))
            md, pp = g(p, "full", arm, "d_static")[0], g(p, "full", arm, "pos_static")[0]
            c3.append(dict(panel=p, d_static=md, pos=pp, ok=bool(md > 0 and pp > 0.5)))
        ok = [all(x["ok"] for x in c) for c in (c1, c2, c3)]
        out[arm] = dict(c1_ok=ok[0], c2_ok=ok[1], c3_ok=ok[2], adopt=all(ok), role="判定" if arm.startswith("A-FAST") else "对照",
                        c1=c1, c2=c2, c3=c3)
    (RUN / "adoption.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for arm, v in out.items():
        print(arm, "c1", v["c1_ok"], "c2", v["c2_ok"], "c3", v["c3_ok"], "adopt", v["adopt"])
        for c in v["c1"] + v["c2"] + v["c3"]:
            if not c["ok"]:
                print("   FAIL", {k: (round(x, 3) if isinstance(x, float) else x) for k, x in c.items()})


if __name__ == "__main__":
    main()
