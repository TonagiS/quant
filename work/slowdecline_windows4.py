#!/usr/bin/env python3
"""slowdecline 补充：四个起点（W2013/W2015/W2017/W2019 → 最新）的关键组合表，5bp/20bp。输出 windows4.csv。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slowdecline as S  # noqa: E402

S.setup()
RC, I, K, G = S.RC, S.I, S.K, S.G
WIN = {"W2013": "2013-01-04", "W2015": "2015-01-01", "W2017": "2017-01-03", "W2019": "2019-01-02"}
G["base_f"]["W2015"] = K.sat_feats(WIN["W2015"])   # 新起点，信号/调仓从 2015 首个可交易日重新开始
SATS = {"S2+VT15": ("S2+VT15", ()), "S4": ("S4", ()), "S2+CREDIT": ("S2+CREDIT", ()), "S4+CREDIT": ("S4+CREDIT", ()),
        "S4+CREDIT+STOP12": ("S4+CREDIT", ("TS12",)), "S4+CREDIT+OFF03+STOP12": ("S4+CREDIT", ("OFF03", "TS12"))}
ARMS = [("S2+VT15", None, 0), ("S4", None, 0), ("S2+CREDIT", None, 0), ("S4+CREDIT", None, 0),
        ("S4+CREDIT+STOP12", None, 0), ("C3", "C3", 1.0), ("C3 60% + S4+CREDIT", "S4+CREDIT", 0.6),
        ("C3 60% + (S4+CREDIT+STOP12)", "S4+CREDIT+STOP12", 0.6), ("C3 50% + (S4+CREDIT+STOP12)", "S4+CREDIT+STOP12", 0.5),
        ("C3 50% + (S4+CREDIT+OFF03+STOP12)", "S4+CREDIT+OFF03+STOP12", 0.5),
        ("C3 60% + (S4+CREDIT+OFF03+STOP12)", "S4+CREDIT+OFF03+STOP12", 0.6)]
rows = []
for cost in (0.0005, 0.002):
    for w in WIN:
        sd = {}
        for nm, (base, arms) in SATS.items():
            b = S.BASES[base]
            sd[nm] = S.sat(w, b["sector"], b["credit"], S.merge_arms(list(arms)), cost)
        lo = sd["S4"]["lo"]
        core = I.core("C3", lo, cost)
        for arm, src, wc in ARMS:
            if src is None:
                d = sd[arm]
            elif src == "C3":
                d = core
            else:
                d = RC.mix(core, sd[src], wc, sd[src]["lo"], cost)
            m = RC.metr(d["ret"], d["lo"])
            rows.append({"arm": arm, "window": w, "cost_bp": int(round(cost * 1e4)), "from": str(RC.data()["cal"][d["lo"]].date()),
                         "cagr": m["cagr"], "mdd": m["mdd"], "calmar": m["calmar"]})
t = pd.DataFrame(rows)
t.to_csv(S.RUN / "windows4.csv", index=False)
pd.set_option("display.width", 250)
for c in (5, 20):
    x = t[t.cost_bp == c]
    print(c, x.groupby("window")["from"].first().to_dict())
    p = x.pivot_table(index="arm", columns="window", values=["cagr", "mdd", "calmar"], sort=False)
    print(p.reindex([a[0] for a in ARMS]).round(3).to_string())
