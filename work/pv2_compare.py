#!/usr/bin/env python3
"""parts_v2 补充：把事后最佳叠加，和纯底仓 C3、现有候选 C3 60% + (S4+CREDIT) 直接对比（滚动起点 + 四段）。样本内。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pv2_core as C  # noqa: E402
import pv2_stack as ST  # noqa: E402

C.setup()
rows = []
for cost in (0.0005, 0.002):
    core = C.core_run(cost)
    s4c = C.sat_run(cost, credit=True)
    ref = {"C3": core["ret"], "C3_60+(S4+CREDIT)": C.combo(core, s4c, cost)["ret"], "S4+CREDIT": s4c["ret"]}
    arms = {}
    for parts in (["DISP80H", "VOV80H", "US10Y30H"], ["DISP80H", "US10Y30H"], ["US10Y30H"]):
        for cr in (False, True):
            s = ST.build(cost, parts, cr)
            tag = "+".join(parts)
            arms[f"S4{'+CREDIT' if cr else ''}+{tag}"] = s["ret"]
            arms[f"C3_60+(S4{'+CREDIT' if cr else ''}+{tag})"] = C.combo(core, s, cost)["ret"]
    for a, r in arms.items():
        for rn, rr in ref.items():
            if ("C3_60" in a) != (rn != "S4+CREDIT"):
                continue
            g = C.gate1(r, rr)
            rows.append(dict(arm=a, vs=rn, cost_bp=int(round(cost * 1e4)), **C.full(r), share_up=g["share_up"], med_d=g["med_d"],
                             n_seg_pos=g["n_seg_pos"], **{k: v for k, v in g.items() if k.startswith("seg_")}))
t = pd.DataFrame(rows)
t.to_csv(C.RUN / "compare_vs_refs.csv", index=False)
pd.set_option("display.width", 250)
print(t.round(3).to_string())
