#!/usr/bin/env python3
"""卫星对照（含 VT15 版对照）：拆分 S4 选股/板块上限 与 VT15 各自的贡献（2026-11-21 sat_vt15_control）。
规则见 runs/2026-11-21_sat_vt15_control/PREREG_ADDENDUM.md（冻结后不得修改）。复用已冻结的 work/prereg_satellite.py（只 import）。
用法：python3 work/sat_vt15_control.py run | smoke <outdir>"""
from __future__ import annotations

import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prereg_satellite as PS  # noqa: E402

K, RC, PB, SR, G = PS.K, PS.RC, PS.PB, PS.SR, PS.G
RUN = K.ROOT / "runs" / "2026-11-21_sat_vt15_control"
COSTS = PS.COSTS
SEEDS = {"RANDOM_N1": (True, range(20000, 20200)),     # 同约束（MA60+每板块≤2）+VT15，与 prereg_satellite 同种子
         "RANDOM_N3": (False, range(21000, 21200))}    # 无板块上限（MA60）+VT15 —— S2+VT15 的随机对应物
PRIMARY = ["vs_BROAD_MA60_VT15", "vs_RANDOM_N1_median"]
EXTRA_JUDGED = ["vs_S2_VT15"]
INFO = ["vs_RANDOM_N3_median", "vs_BROAD_MA60(无VT)", "vs_PURE_CORE(次要)"]


def broad_vt_run(cost):
    """宽基 5 只等权+MA60（同 prereg_satellite.broad_run 的目标权重）再叠 VT15：
    每个调仓信号日 s，scale = clip(0.15 / (未缩放宽基卫星日收益近 60 日标准差×√252), 0, 1)，近 60 日有效样本 <40 时 scale=1；
    目标权重 = 原目标 × scale（其余现金），s+1 收盘成交。未缩放收益用同成本的 broad_run 结果。"""
    base = PS.broad_run(cost)["ret"]
    P = K.panel()
    cal = G["cal"]
    c = P["close_tr"].reindex(cal)[PS.BROAD]
    R = c.pct_change(fill_method=None)
    ma = c.rolling(60, min_periods=60).mean()
    bars = c.notna().cumsum()
    lo = G["lo0"]
    tg = {}
    for s in range(lo, len(cal), 10):
        el = [k for k in PS.BROAD if bars[k].iat[s] >= 150 and np.isfinite(c[k].iat[s])]
        r = base[max(lo, s - 59): s + 1]
        sc = 1.0
        if len(r) >= 40:
            sd = float(np.std(r, ddof=1)) * np.sqrt(252)
            if np.isfinite(sd) and sd > 1e-8:
                sc = float(np.clip(0.15 / sd, 0.0, 1.0))
        if not el:
            tg[s] = pd.Series(dtype=float)
            continue
        on = [k for k in el if np.isfinite(ma[k].iat[s]) and c[k].iat[s] > ma[k].iat[s]]
        tg[s] = pd.Series({k: sc / len(el) for k in on}, dtype=float)
    net, turn = PB.sim_targets(R, tg, lo, cost)
    return dict(ret=net, turn=turn, expo=np.zeros(len(cal)), lo=lo)


def task_random(args):
    nm, seed, cost = args
    cap = SEEDS[nm][0]
    f = SR.random_feats(SR.G["base"]["W2013"], seed, True, cap)
    ret, turn, expo = SR.run_one(f, cost)
    sat = dict(ret=ret, turn=turn, expo=expo, lo=G["lo0"])
    out = []
    for cn, cr in G["cores"][cost].items():
        out += PS.rows_of(PS.evaluate(cr, sat, cost), nm, cn, cost, seed)
    return out


def main(mode, outdir=None):
    smoke = mode == "smoke"
    PS.setup(starts_n=3 if smoke else None)
    out = Path(outdir) if smoke else RUN
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows = []
    G["cores"] = {}
    for c in COSTS:
        cs = PS.cores(c)
        G["cores"][c] = cs
        sats = {"S4": K.sat_run(RC.START_W["W2013"], c, sector=True),
                "S2_VT15": K.sat_run(RC.START_W["W2013"], c),
                "BROAD_MA60_VT15": broad_vt_run(c),
                "BROAD_MA60": PS.broad_run(c)}
        assert all(s["lo"] == G["lo0"] for s in sats.values())
        for cn, cr in cs.items():
            rows += PS.rows_of(PS.evaluate(cr, sats["S4"], c, wc=1.0), "PURE_CORE", cn, c)
            for sn, sd in sats.items():
                rows += PS.rows_of(PS.evaluate(cr, sd, c), sn, cn, c)
        print("det", c, round(time.time() - t0), flush=True)
    tasks = [(nm, s, c) for c in COSTS for nm, (_, sd) in SEEDS.items() for s in (list(sd)[:2] if smoke else sd)]
    with Pool(8) as p:
        for k, r in enumerate(p.imap_unordered(task_random, tasks, chunksize=2)):
            rows += r
            if k % 100 == 0:
                print("rand", k, len(tasks), round(time.time() - t0), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "all_metrics.csv.gz", index=False)
    verdict(df, out)
    print("done", round(time.time() - t0), flush=True)


def verdict(df, out):
    det = df[~df.arm.str.startswith("RANDOM")].set_index(["arm", "core", "cost_bp", "kind", "key"]).sort_index()["calmar"]
    rnd = {nm: df[df.arm == nm].groupby(["core", "cost_bp", "kind", "key"])["calmar"].median() for nm in SEEDS}
    rows, dist = [], []
    for core in ("C3", "DIV-IV"):
        for cb in sorted(df.cost_bp.unique()):
            s4 = det.loc[("S4", core, cb)]
            ctrls = {"vs_BROAD_MA60_VT15": det.loc[("BROAD_MA60_VT15", core, cb)],
                     "vs_RANDOM_N1_median": rnd["RANDOM_N1"].loc[(core, cb)],
                     "vs_S2_VT15": det.loc[("S2_VT15", core, cb)],
                     "vs_RANDOM_N3_median": rnd["RANDOM_N3"].loc[(core, cb)],
                     "vs_BROAD_MA60(无VT)": det.loc[("BROAD_MA60", core, cb)],
                     "vs_PURE_CORE(次要)": det.loc[("PURE_CORE", core, cb)]}
            for nm, ct in ctrls.items():
                diff = s4 - ct.reindex(s4.index)
                st, sg = diff.loc["start"], diff.loc["seg"]
                share, med, nseg = float((st > 0).mean()), float(st.median()), int((sg > 0).sum())
                ok = share >= PS.PASS_SHARE and med > PS.PASS_MED and nseg >= PS.PASS_SEG
                rows.append({"core": core, "cost_bp": int(cb), "compare": nm, "role": "primary" if nm in PRIMARY else ("judged" if nm in EXTRA_JUDGED else "info"),
                             "n_starts": int(len(st)), "share_pos": share, "median_diff": med,
                             "p10_diff": float(st.quantile(0.1)), "p90_diff": float(st.quantile(0.9)),
                             **{f"seg_{g}": float(sg.get(g, np.nan)) for g in PS.SEGS}, "seg_pos": nseg, "pass": bool(ok)})
                for key, v in st.items():
                    dist.append({"core": core, "cost_bp": int(cb), "compare": nm, "start": key, "calmar_diff": float(v)})
    V = pd.DataFrame(rows)
    V.to_csv(out / "verdict_table.csv", index=False)
    pd.DataFrame(dist).to_csv(out / "start_diffs.csv", index=False)
    res = {}
    for cb in (5, 20):
        x = V[V.cost_bp == cb]
        res[f"primary_{cb}bp"] = {c: bool(x[(x.core == c) & x["compare"].isin(PRIMARY)]["pass"].all()) for c in ("C3", "DIV-IV")}
        res[f"vs_S2_VT15_{cb}bp"] = {c: bool(x[(x.core == c) & (x["compare"] == "vs_S2_VT15")]["pass"].all()) for c in ("C3", "DIV-IV")}
    res["overall_pass_5bp"] = all(res["primary_5bp"].values())
    (out / "verdict.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
