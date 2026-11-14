#!/usr/bin/env python3
"""FC-G60-M15/M10 诊断汇总：个体层面关帽时机、关帽次数、事件分解；主题子集 2023-26。"""
from pathlib import Path
import pandas as pd

RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-14_fc_fastgate"
PANELS = ["index", "etf_long", "hv_ashare", "hv_xmkt", "cs951", "xmkt"]
ARMS = ["A-FC", "A-FAST-MKT", "FC-G60-M15", "FC-G60-M10"]
THEME = r"芯片|半导体|人工智能|通信|CPO|机器人|科创|软件|计算机|算力|游戏|传媒"


def main():
    d = pd.read_csv(RUN / "diagnostics_by_name.csv")
    d = d[d.status == "ok"]
    out = []
    for pn in PANELS:
        g = d[d.panel == pn]
        for k in ARMS:
            rec = dict(panel=pn, arm=k, n=len(g))
            for c in [c for c in g.columns if c.startswith(k + "|")]:
                m = c.split("|", 1)[1]
                rec[m] = g[c].median()
                if any(m.endswith(x) for x in ("_lag", "_days_off", "_sumd120", "_sumd250")):
                    rec[m + "_n"] = int(g[c].notna().sum())
            out.append(rec)
    pd.DataFrame(out).to_csv(RUN / "diagnostics_summary.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    r = pd.read_csv(RUN / "results_long.csv")
    t = r[(r.panel == "cs951") & (r.window == "2023-2026") & r.group.astype(str).str.contains(THEME)]
    th = t.groupby("arm").agg(n=("code", "size"), cagr=("cagr", "median"), mdd=("mdd", "median"),
                              calmar=("calmar", "median"), turn=("turn", "median")).reset_index()
    th.to_csv(RUN / "theme_2023_2026.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    print(th)
    print(t.drop_duplicates("code").group.value_counts().head(20))


if __name__ == "__main__":
    main()
