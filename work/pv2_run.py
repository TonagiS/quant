#!/usr/bin/env python3
"""parts_v2 主程序：单零件两道关 → 只叠"两关都过"的零件。输出 runs/2026-11-21_parts_v2/*.csv。样本内。"""
from __future__ import annotations

import itertools
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pv2_core as C  # noqa: E402

COSTS = (0.0005, 0.002)
N_SHIFT = 200
N_SEED = 100
OUT = C.RUN


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


_B = {}


def bases(cost):
    if cost not in _B:
        sat = C.sat_run(cost)
        core = C.core_run(cost)
        cmb = C.combo(core, sat, cost)
        _B[cost] = dict(sat=sat, core=core, combo=cmb,
                        roll=dict(sat=C.roll_cal(sat["ret"]), combo=C.roll_cal(cmb["ret"])),
                        seg=dict(sat=C.seg_cal(sat["ret"]), combo=C.seg_cal(cmb["ret"])))
    return _B[cost]


def evaluate(arm_sat, arm_combo, cost):
    """返回 {base: (full, gate1, roll_arr)}；arm_sat 可为 None（底仓零件）。"""
    B = bases(cost)
    out = {}
    for nm, d in (("S4", arm_sat), ("C3_60+S4", arm_combo)):
        if d is None:
            continue
        key = "sat" if nm == "S4" else "combo"
        g = C.gate1(d["ret"], B[key]["ret"], B["roll"][key], B["seg"][key])
        out[nm] = (C.full(d["ret"]), g, C.roll_cal(d["ret"]))
    return out


def stat_med(roll_arm, roll_base):
    return float(np.nanmedian(roll_arm - roll_base))


def task_ov(args):
    C.setup()
    arm, cost, mults = args            # mults: 叠加的乘数名列表（单零件或组合）
    B = bases(cost)
    m = np.ones(C.P["T"])
    for a in mults:
        m = m * C.ov_mult(a)
    rows = []
    lo0 = C.starts()[0]
    sat = C.K.apply_mult(B["sat"], m, cost)
    cmb = C.combo(B["core"], sat, cost)
    ev = evaluate(sat, cmb, cost)
    # 同平均敞口常数缩放
    cbar = float(np.mean(m[lo0:]))
    sat_c = C.K.apply_mult(B["sat"], np.full(C.P["T"], cbar), cost)
    cmb_c = C.combo(B["core"], sat_c, cost)
    evc = evaluate(sat_c, cmb_c, cost)
    # 随机择时：循环平移乘数（保持触发比例和持续长度）
    rng = np.random.default_rng(12345)
    T = C.P["T"]
    null = {"S4": [], "C3_60+S4": []}
    for _ in range(N_SHIFT):
        k = int(rng.integers(252, T - 252))
        ms = np.roll(m, k)
        s2 = C.K.apply_mult(B["sat"], ms, cost)
        c2 = C.combo(B["core"], s2, cost)
        null["S4"].append(stat_med(C.roll_cal(s2["ret"]), B["roll"]["sat"]))
        null["C3_60+S4"].append(stat_med(C.roll_cal(c2["ret"]), B["roll"]["combo"]))
    for base in ev:
        full, g, ra = ev[base]
        rc = evc[base][2]
        nl = np.array(null[base])
        p = float((1 + np.sum(nl >= g["med_d"])) / (1 + len(nl)))
        dc = ra - rc
        g2 = dict(ctrl="const+shift", c_bar=cbar, p_rand=p, null_med=float(np.median(nl)), null_p90=float(np.quantile(nl, 0.9)),
                  vs_const_share=float(np.mean(dc > 0)), vs_const_med=float(np.nanmedian(dc)),
                  pass2=bool(p <= 0.10 and np.nanmedian(dc) > 0))
        rows.append(dict(arm=arm, kind="ov", base=base, cost_bp=int(round(cost * 1e4)), trig=float(np.mean(m[lo0:] < 1)), **full, **g, **g2))
    return rows


def task_sel(args):
    C.setup()
    arm, cost, sels, mults = args
    B = bases(cost)
    m = None
    if mults:
        m = np.ones(C.P["T"])
        for a in mults:
            m = m * C.ov_mult(a)
    sat = C.sat_run(cost, sels=sels, mult=m)
    base_sat = B["sat"] if m is None else C.K.apply_mult(B["sat"], m, cost)
    cmb = C.combo(B["core"], sat, cost)
    ev = evaluate(sat, cmb, cost)
    null = {"S4": [], "C3_60+S4": []}
    for seed in range(N_SEED):
        s2 = C.sat_run(cost, sels=sels, mult=m, rng=np.random.default_rng(1000 + seed))
        c2 = C.combo(B["core"], s2, cost)
        null["S4"].append(stat_med(C.roll_cal(s2["ret"]), B["roll"]["sat"]))
        null["C3_60+S4"].append(stat_med(C.roll_cal(c2["ret"]), B["roll"]["combo"]))
    rows = []
    lo0 = C.starts()[0]
    for base in ev:
        full, g, ra = ev[base]
        nl = np.array(null[base])
        p = float((1 + np.sum(nl >= g["med_d"])) / (1 + len(nl)))
        g2 = dict(ctrl="random-exclude-same-count" if arm != "ONM" else "random-rank-blend", p_rand=p,
                  null_med=float(np.median(nl)), null_p90=float(np.quantile(nl, 0.9)),
                  avg_expo=float(np.mean(sat["expo"][lo0:])), base_expo=float(np.mean(base_sat["expo"][lo0:])),
                  pass2=bool(p <= 0.10))
        rows.append(dict(arm=arm, kind="sel", base=base, cost_bp=int(round(cost * 1e4)), **full, **g, **g2))
    return rows


def task_core(args):
    C.setup()
    arm, cost = args
    L = C.CORE[arm]
    B = bases(cost)
    onoff = C.core_onoff(L)
    core = C.core_run(cost, onoff=onoff)
    cmb = C.combo(core, B["sat"], cost)
    ev = evaluate(None, cmb, cost)
    me = list(C.RC.PB.month_end_pos(C.P["cal"], C.starts()[0]))
    const = onoff.iloc[me].mean().to_numpy()
    cc = C.core_run(cost, const=const)
    rc = C.roll_cal(C.combo(cc, B["sat"], cost)["ret"])
    rng = np.random.default_rng(777)
    T = C.P["T"]
    nl = []
    for _ in range(N_SHIFT):
        k = int(rng.integers(252, T - 252))
        o2 = onoff.copy()
        o2.iloc[:, :] = np.roll(onoff.to_numpy(), k, axis=0)
        c2 = C.combo(C.core_run(cost, onoff=o2), B["sat"], cost)
        nl.append(stat_med(C.roll_cal(c2["ret"]), B["roll"]["combo"]))
    nl = np.array(nl)
    full, g, ra = ev["C3_60+S4"]
    p = float((1 + np.sum(nl >= g["med_d"])) / (1 + len(nl)))
    dc = ra - rc
    # 底仓单独（信息）
    core_only = C.gate1(core["ret"], B["core"]["ret"])
    g2 = dict(ctrl="const+shift(core)", c_bar=float(np.mean(const)), p_rand=p, null_med=float(np.median(nl)),
              null_p90=float(np.quantile(nl, 0.9)), vs_const_share=float(np.mean(dc > 0)), vs_const_med=float(np.nanmedian(dc)),
              pass2=bool(p <= 0.10 and np.nanmedian(dc) > 0),
              core_only_share=core_only["share_up"], core_only_med=core_only["med_d"], core_only_calmar=C.full(core["ret"])["calmar"])
    return [dict(arm=arm, kind="core", base="C3_60+S4", cost_bp=int(round(cost * 1e4)), **full, **g, **g2)]


def run_tasks(tasks):
    rows = []
    with Pool(6, initializer=C.setup) as p:
        for k, r in enumerate(p.imap_unordered(_dispatch, tasks)):
            rows += r
            log("done", k + 1, len(tasks), r[0]["arm"], r[0]["cost_bp"])
    return pd.DataFrame(rows)


def _dispatch(t):
    kind = t[0]
    return {"ov": task_ov, "sel": task_sel, "core": task_core}[kind](t[1:])


def main_single():
    tasks = []
    for c in COSTS:
        tasks += [("ov", a, c, [a]) for a in C.OV]
        tasks += [("sel", a, c, (a,), ()) for a in C.SEL]
        tasks += [("core", a, c) for a in C.CORE]
    df = run_tasks(tasks)
    df.to_csv(OUT / "single.csv", index=False)
    return df


def base_rows():
    C.setup()
    rows = []
    for c in COSTS:
        B = bases(c)
        cr = C.sat_run(c, credit=True)
        for nm, d in (("S4", B["sat"]), ("C3", B["core"]), ("C3_60+S4", B["combo"]), ("S4+CREDIT", cr),
                      ("C3_60+(S4+CREDIT)", C.combo(B["core"], cr, c))):
            rows.append(dict(arm=nm, cost_bp=int(round(c * 1e4)), **C.full(d["ret"]), **{f"seg_{k}": v for k, v in C.seg_cal(d["ret"]).items()},
                             roll_med=float(np.nanmedian(C.roll_cal(d["ret"])))))
    t = pd.DataFrame(rows)
    t.to_csv(OUT / "bases.csv", index=False)
    return t


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "base":
        print(base_rows().round(3).to_string())
    elif cmd == "single":
        df = main_single()
        print(df[["arm", "base", "cost_bp", "calmar", "share_up", "med_d", "n_seg_pos", "pass1", "p_rand", "pass2"]].round(3).to_string())
