#!/usr/bin/env python3
"""汇总 results_long.csv → summary.csv / pairwise.csv / verdict.json。判定按 PREREG 机械执行。"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-14_highvol_stocks"
ARMS = ["BH", "A", "A-vol", "A-SV", "A-FC", "A-FC30", "A-FC20", "F"]


def agg(g):
    return pd.Series(dict(n=len(g), cagr=g.cagr.median(), mdd=g.mdd.median(), calmar=g.calmar.median(),
                          avg_pos=g.avg_pos.median(), turn=g.turn.median(),
                          d_calmar_static=g.d_calmar_static.median(),
                          pct_dstatic_pos=(g.d_calmar_static > 0).mean()))


def pair(res, keys):
    a = res[res.arm == "A"].set_index(keys + ["code"])
    out = []
    for arm in ARMS:
        if arm == "A":
            continue
        b = res[res.arm == arm].set_index(keys + ["code"])
        j = b.join(a, rsuffix="_A", how="inner")
        for k, g in j.groupby(level=list(range(len(keys)))):
            k = k if isinstance(k, tuple) else (k,)
            out.append(dict(zip(keys, k), arm=arm, n=len(g),
                            pct_calmar_gt_A=(g.calmar > g.calmar_A).mean(),
                            pct_cagr_gt_A=(g.cagr > g.cagr_A).mean(),
                            med_d_cagr_vs_A=(g.cagr - g.cagr_A).median(),
                            med_d_mdd_vs_A=(g.mdd - g.mdd_A).median(),
                            med_d_calmar_vs_A=(g.calmar - g.calmar_A).median()))
    return pd.DataFrame(out)


def main():
    res = pd.read_csv(RUN / "results_long.csv")
    keys = ["panel", "kind", "window", "arm"]
    s = res.groupby(keys).apply(agg, include_groups=False).reset_index()
    # 副方法：按全评估期 vol250 中位数分组（有前视，仅描述）
    f = res[(res.kind == "window") & (res.window == "full")].copy()
    f["medvol_grp"] = pd.cut(f.med_vol250, [0, 0.35, 0.40, 9], right=False, labels=["med<35", "med35-40", "med>=40"])
    s2 = f.groupby(["panel", "medvol_grp", "arm"], observed=True).apply(agg, include_groups=False).reset_index()
    s2.insert(1, "kind", "medvol_full"); s2 = s2.rename(columns={"medvol_grp": "window"})
    # 崩盘窗口：窗口起点 PIT vol≥40% 的子集
    c = res[(res.kind == "window") & (res.vol250_at_start >= 0.40)]
    s3 = c.groupby(["panel", "window", "arm"]).apply(agg, include_groups=False).reset_index()
    s3.insert(1, "kind", "window_startvol>=40")
    # xmkt 内分组
    x = res[res.panel == "xmkt"]
    s4 = x.groupby(["group", "kind", "window", "arm"]).apply(agg, include_groups=False).reset_index().rename(columns={"group": "panel"})
    s4["panel"] = "xmkt:" + s4["panel"]
    summ = pd.concat([s, s2, s3, s4], ignore_index=True)
    summ.to_csv(RUN / "summary.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    p = pd.concat([pair(res, ["panel", "kind", "window"]),
                   pair(c.assign(kind="window_startvol>=40"), ["panel", "kind", "window"]),
                   pair(f.assign(kind="medvol_full", window=f.medvol_grp.astype(str)), ["panel", "kind", "window"])])
    p.to_csv(RUN / "pairwise.csv", index=False, encoding="utf-8-sig", float_format="%.4f")

    def g(panel, kind, w, arm, col):
        r = summ[(summ.panel == panel) & (summ.kind == kind) & (summ.window == w) & (summ.arm == arm)]
        return float(r[col].iloc[0]) if len(r) else np.nan

    def pp(panel, kind, w, arm, col):
        r = p[(p.panel == panel) & (p.kind == kind) & (p.window == w) & (p.arm == arm)]
        return float(r[col].iloc[0]) if len(r) else np.nan

    crash = {"xmkt": ["2000-2002", "2007-2009"], "ashare": ["2007-2009", "y2015"]}
    ver = {}
    for arm in ["A-FC", "A-FC30", "A-FC20"]:
        vv = {}
        for panel in ("ashare", "xmkt"):
            c1 = g(panel, "pit", "pit>=40", arm, "calmar") >= g(panel, "pit", "pit>=40", "A", "calmar")
            c2 = pp(panel, "pit", "pit>=40", arm, "pct_calmar_gt_A") > 0.5
            c3 = g(panel, "pit", "pit>=40", arm, "d_calmar_static") > 0
            c4 = all(g(panel, "window", w, arm, "mdd") >= g(panel, "window", w, "A", "mdd") - 0.03 for w in crash[panel])
            vv[panel] = dict(c1_med_calmar_ge_A=bool(c1), c2_pct_calmar_gt_A_gt50=bool(c2),
                             c3_dstatic_pos=bool(c3), c4_crash_mdd_within3pp=bool(c4),
                             med_calmar=g(panel, "pit", "pit>=40", arm, "calmar"),
                             med_calmar_A=g(panel, "pit", "pit>=40", "A", "calmar"),
                             pct_calmar_gt_A=pp(panel, "pit", "pit>=40", arm, "pct_calmar_gt_A"),
                             med_dstatic=g(panel, "pit", "pit>=40", arm, "d_calmar_static"),
                             crash_mdd={w: (g(panel, "window", w, arm, "mdd"), g(panel, "window", w, "A", "mdd")) for w in crash[panel]})
        allc = all(all(v[k] for k in v if k.startswith("c")) for v in vv.values())
        c12 = all(v["c1_med_calmar_ge_A"] and v["c2_pct_calmar_gt_A_gt50"] for v in vv.values())
        vv["verdict"] = "支持" if allc else ("收益更高但风险代价不达标" if c12 else "不支持")
        vv["role"] = "主判定" if arm == "A-FC" else "敏感性"
        ver[arm] = vv
    (RUN / "verdict.json").write_text(json.dumps(ver, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(ver, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
