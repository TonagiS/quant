#!/usr/bin/env python3
"""parts_v2 叠加（事后探索，FREEZE 外）：主臂里没有两关都过的，所以按 FREEZE 的规则，正式叠加集合为空。
这里把唯一两关都过的变体 IVOL30，和只过一关的 DISP80H / VOV80H / US10Y30H 放在一起，枚举所有子集，
分别叠在 S4、S4+CREDIT 和对应的 C3 60% 组合上，用同样的两道关检验。属于样本内、事后挑选，不算证据。"""
from __future__ import annotations

import itertools
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pv2_core as C  # noqa: E402

COSTS = (0.0005, 0.002)
POOL = ["IVOL30", "DISP80H", "VOV80H", "US10Y30H"]
N_NULL = 100


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def build(cost, parts, credit, rng=None):
    sels = tuple(p for p in parts if p in C.SEL)
    ovs = [p for p in parts if p in C.OV]
    m = None
    if ovs:
        m = np.ones(C.P["T"])
        for a in ovs:
            m = m * C.ov_mult(a)
        if rng is not None:
            m = np.roll(m, int(rng.integers(252, C.P["T"] - 252)))
    sat = C.sat_run(cost, sels=sels, mult=m, credit=credit, rng=rng if sels else None)
    return sat


_B = {}


def base(cost, credit):
    k = (cost, credit)
    if k not in _B:
        s = C.sat_run(cost, credit=credit)
        core = C.core_run(cost)
        cb = C.combo(core, s, cost)
        _B[k] = dict(sat=s, core=core, combo=cb, rs=C.roll_cal(s["ret"]), rc=C.roll_cal(cb["ret"]),
                     ss=C.seg_cal(s["ret"]), sc=C.seg_cal(cb["ret"]))
    return _B[k]


def task(args):
    C.setup()
    parts, cost, credit = args
    B = base(cost, credit)
    sat = build(cost, parts, credit)
    cb = C.combo(B["core"], sat, cost)
    g_s = C.gate1(sat["ret"], B["sat"]["ret"], B["rs"], B["ss"])
    g_c = C.gate1(cb["ret"], B["combo"]["ret"], B["rc"], B["sc"])
    rng = np.random.default_rng(4242)
    ns, nc = [], []
    for _ in range(N_NULL):
        s2 = build(cost, parts, credit, rng=rng)
        c2 = C.combo(B["core"], s2, cost)
        ns.append(np.nanmedian(C.roll_cal(s2["ret"]) - B["rs"]))
        nc.append(np.nanmedian(C.roll_cal(c2["ret"]) - B["rc"]))
    rows = []
    bn = "S4+CREDIT" if credit else "S4"
    for nm, d, g, nl in ((bn, sat, g_s, ns), (f"C3_60+{bn}", cb, g_c, nc)):
        nl = np.array(nl)
        p = float((1 + np.sum(nl >= g["med_d"])) / (1 + len(nl)))
        rows.append(dict(stack="+".join(parts), n_parts=len(parts), base=nm, cost_bp=int(round(cost * 1e4)), **C.full(d["ret"]), **g,
                         p_rand=p, null_med=float(np.median(nl)), pass2=bool(p <= 0.10),
                         roll_med_calmar=float(np.nanmedian(C.roll_cal(d["ret"])))))
    return rows


def main():
    subsets = [s for r in range(1, len(POOL) + 1) for s in itertools.combinations(POOL, r)]
    tasks = [(list(s), c, cr) for s in subsets for c in COSTS for cr in (False, True)]
    rows = []
    with Pool(6, initializer=C.setup) as p:
        for k, r in enumerate(p.imap_unordered(task, tasks)):
            rows += r
            if k % 10 == 0:
                log(k, len(tasks))
    df = pd.DataFrame(rows)
    df.to_csv(C.RUN / "stack_explore.csv", index=False)
    return df


if __name__ == "__main__":
    df = main()
    pd.set_option("display.width", 250)
    x = df[df.cost_bp == 5].sort_values(["base", "med_d"], ascending=[True, False])
    print(x[["stack", "base", "cagr", "mdd", "calmar", "roll_med_calmar", "share_up", "med_d", "n_seg_pos", "pass1", "p_rand", "pass2"]].round(3).to_string())
