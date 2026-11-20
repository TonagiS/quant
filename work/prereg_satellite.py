#!/usr/bin/env python3
"""预登记卫星对照检验（2026-11-21 prereg_satellite）。规则见 runs/2026-11-21_prereg_satellite/PREREG.md（冻结后不得修改）。
用法：python3 work/prereg_satellite.py run            # 正式运行（写 runs/.../ 下结果）
      python3 work/prereg_satellite.py smoke <outdir> # 冒烟（2 个随机种子、3 个起点，写到 outdir，不看数字）"""
from __future__ import annotations

import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import components_core as K  # noqa: E402
import s4_random_null as SR  # noqa: E402

RC, PB = K.RC, K.PB
RUN = K.ROOT / "runs" / "2026-11-21_prereg_satellite"
COSTS = (0.0005, 0.0010, 0.0020)
W_CORE = 0.60
BROAD = ["sh510300", "sh510500", "sz159915", "sh588000", "sh512100"]
SEEDS = range(20000, 20200)          # 随机卫星 N=200，固定种子
START_FIRST, START_LAST = "2014-06-01", "2023-11-13"   # 滚动起点：池满 40 只的首月 ~ END 前 3 年
SEGS = {"G1_2014_16": ("2014-06-03", "2016-12-31"), "G2_2017_19": ("2017-01-01", "2019-12-31"),
        "G3_2020_22": ("2020-01-01", "2022-12-31"), "G4_2023_26": ("2023-01-01", "2026-11-13")}
PASS_SHARE, PASS_MED, PASS_SEG = 0.70, 0.05, 3
G = {}


def setup(starts_n=None):
    D = RC.data()
    cal = D["cal"]
    G["cal"] = cal
    SR.setup()
    G["lo0"] = int(SR.G["base"]["W2013"]["start"])           # 所有腿从 W2013 起点连续跑
    me = pd.Series(np.arange(len(cal)), index=cal)
    ms = me[(cal >= START_FIRST) & (cal <= START_LAST)]
    starts = ms.groupby(ms.index.to_period("M")).min().to_list()
    G["starts"] = starts[:starts_n] if starts_n else starts
    G["segs"] = {}
    for g, (a, b) in SEGS.items():
        m = np.asarray((cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b)))
        G["segs"][g] = (int(np.argmax(m)), m)
    G["m_erp"] = K.erp_mult()[0]


def broad_run(cost):
    """对照 (b)：5 只宽基 ETF，每只上市满 150 个交易日后入选集；每 10 个交易日（相位同 S4，自 lo0 起）
    在信号日收盘判断：权重 = 1/N_入选集 给收盘价 > MA60 的，其余现金（0 收益）；s+1 收盘成交。"""
    P = K.panel()
    cal = G["cal"]
    c = P["close_tr"].reindex(cal)[BROAD]
    R = c.pct_change(fill_method=None)
    ma = c.rolling(60, min_periods=60).mean()
    bars = c.notna().cumsum()
    lo = G["lo0"]
    tg = {}
    for s in range(lo, len(cal), 10):
        el = [k for k in BROAD if bars[k].iat[s] >= 150 and np.isfinite(c[k].iat[s])]
        if not el:
            tg[s] = pd.Series(dtype=float)
            continue
        on = [k for k in el if np.isfinite(ma[k].iat[s]) and c[k].iat[s] > ma[k].iat[s]]
        tg[s] = pd.Series({k: 1.0 / len(el) for k in on}, dtype=float)
    net, turn = PB.sim_targets(R, tg, lo, cost)
    expo = np.zeros(len(cal))
    return dict(ret=net, turn=turn, expo=expo, lo=lo)


def cores(cost):
    lo = G["lo0"]
    return {"C3": K.core_run(lo, cost, mult=G["m_erp"]), "DIV-IV": K.core_run(lo, cost)}


def evaluate(core, sat, cost, wc=W_CORE):
    """返回 {(kind, key): (cagr, mdd, calmar)}；kind=start（起点→END）/ seg（段内重新起步、段内统计）。"""
    out = {}
    for s in G["starts"]:
        d = RC.mix(core, sat, wc, s, cost)
        m = RC.metr(d["ret"], s)
        out[("start", str(G["cal"][s].date()))] = (m["cagr"], m["mdd"], m["calmar"])
    for g, (s, mask) in G["segs"].items():
        d = RC.mix(core, sat, wc, s, cost)
        m = RC.metr(d["ret"], s, mask)
        out[("seg", g)] = (m["cagr"], m["mdd"], m["calmar"])
    return out


def rows_of(ev, arm, core, cost, seed=None):
    return [{"arm": arm, "core": core, "cost_bp": int(round(cost * 1e4)), "seed": seed, "kind": k, "key": key,
             "cagr": v[0], "mdd": v[1], "calmar": v[2]} for (k, key), v in ev.items()]


def task_random(args):
    seed, cost = args
    f = SR.random_feats(SR.G["base"]["W2013"], seed, True, True)       # N1：在池、有效、close>MA60，每板块≤2
    ret, turn, expo = SR.run_one(f, cost)
    sat = dict(ret=ret, turn=turn, expo=expo, lo=G["lo0"])
    out = []
    for cn, cr in G["cores"][cost].items():
        out += rows_of(evaluate(cr, sat, cost), "RANDOM", cn, cost, seed)
    return out


def main(mode, outdir=None):
    smoke = mode == "smoke"
    setup(starts_n=3 if smoke else None)
    out = Path(outdir) if smoke else RUN
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows = []
    G["cores"] = {}
    for c in COSTS:
        cs = cores(c)
        G["cores"][c] = cs
        sats = {"S4": K.sat_run(RC.START_W["W2013"], c, sector=True), "BROAD_MA60": broad_run(c)}
        assert all(s["lo"] == G["lo0"] for s in sats.values())
        for cn, cr in cs.items():
            rows += rows_of(evaluate(cr, sats["S4"], c, wc=1.0), "PURE_CORE", cn, c)   # wc=1：纯底仓，同一 mix 约定
            for sn, sd in sats.items():
                rows += rows_of(evaluate(cr, sd, c), sn, cn, c)
        print("det", c, round(time.time() - t0), flush=True)
    seeds = list(SEEDS)[:2] if smoke else list(SEEDS)
    tasks = [(s, c) for c in COSTS for s in seeds]
    with Pool(8) as p:
        for k, r in enumerate(p.imap_unordered(task_random, tasks, chunksize=2)):
            rows += r
            if k % 50 == 0:
                print("rand", k, len(tasks), round(time.time() - t0), flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(out / "all_metrics.csv.gz", index=False)
    verdict(df, out)
    print("done", round(time.time() - t0), flush=True)


def verdict(df, out):
    """按 PREREG 判定。RANDOM 先在每个 (core,cost,kind,key) 上取 200 个种子卡玛的中位数作为对照值。"""
    det = df[df.arm != "RANDOM"].set_index(["arm", "core", "cost_bp", "kind", "key"])["calmar"]
    rnd = df[df.arm == "RANDOM"].groupby(["core", "cost_bp", "kind", "key"])["calmar"].median()
    rows, dist = [], []
    for core in ("C3", "DIV-IV"):
        for cb in sorted(df.cost_bp.unique()):
            s4 = det.loc[("S4", core, cb)]
            ctrls = {"vs_BROAD_MA60": det.loc[("BROAD_MA60", core, cb)], "vs_RANDOM_median": rnd.loc[(core, cb)],
                     "vs_PURE_CORE(次要)": det.loc[("PURE_CORE", core, cb)]}
            for nm, ct in ctrls.items():
                diff = (s4 - ct.reindex(s4.index))
                st = diff.loc["start"]
                sg = diff.loc["seg"]
                share, med, nseg = float((st > 0).mean()), float(st.median()), int((sg > 0).sum())
                ok = share >= PASS_SHARE and med > PASS_MED and nseg >= PASS_SEG
                rows.append({"core": core, "cost_bp": int(cb), "compare": nm, "n_starts": int(len(st)),
                             "share_pos": share, "median_diff": med, "p10_diff": float(st.quantile(0.1)),
                             "p90_diff": float(st.quantile(0.9)), "min_diff": float(st.min()),
                             **{f"seg_{g}": float(sg.get(g, np.nan)) for g in SEGS}, "seg_pos": nseg, "pass": bool(ok)})
                for key, v in st.items():
                    dist.append({"core": core, "cost_bp": int(cb), "compare": nm, "start": key, "calmar_diff": float(v)})
    V = pd.DataFrame(rows)
    V.to_csv(out / "verdict_table.csv", index=False)
    pd.DataFrame(dist).to_csv(out / "start_diffs.csv", index=False)
    prim = V[(V.cost_bp == 5) & V["compare"].isin(["vs_BROAD_MA60", "vs_RANDOM_median"])]
    p20 = V[(V.cost_bp == 20) & V["compare"].isin(["vs_BROAD_MA60", "vs_RANDOM_median"])]
    res = {"primary_5bp": {c: bool(prim[prim.core == c]["pass"].all()) for c in ("C3", "DIV-IV")},
           "cost_20bp": {c: bool(p20[p20.core == c]["pass"].all()) for c in ("C3", "DIV-IV")}}
    res["overall_pass_5bp"] = all(res["primary_5bp"].values())
    (out / "verdict.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
