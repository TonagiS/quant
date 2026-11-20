#!/usr/bin/env python3
"""S4 随机持仓零假设。规则见 runs/2026-11-21_s4_random/FREEZE.md（已冻结）。"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import components_core as K  # noqa: E402

RC, E = K.RC, K.E
RUN = K.ROOT / "runs" / "2026-11-21_s4_random"
NULLS = {"N1": dict(filt=True, cap=True, seeds=range(0, 1000)),
         "N2": dict(filt=False, cap=True, seeds=range(1000, 2000)),
         "N3": dict(filt=True, cap=False, seeds=range(2000, 3000))}
SEGS = ["Y2015", "Y2018", "S2021_24", "S2023_now"]
G = {}


def setup():
    D = RC.data()
    sec = K.sector_map()
    cols = list(D["closeT"].columns)
    groups = {}
    for j, c in enumerate(cols):
        groups.setdefault(sec[c], []).append(j)
    G["groups"] = [np.array(v) for v in groups.values()]
    G["base"] = {w: K.sat_feats(sd) for w, sd in RC.START_W.items()}
    close = D["closeT"]
    G["ma_ok"] = (close > close.rolling(60, min_periods=60).mean()).to_numpy()
    G["valid"] = G["base"]["W2013"]["valid"].to_numpy()   # valid 与起点无关
    cal = D["cal"]
    G["masks"] = {s: RC.seg_mask(*RC.SUB_W[s]).__array__() for s in SEGS}
    G["cal"] = cal


def cap2(score, allowed):
    """每行：按分数降序，每板块只保留前 2。score 中不允许的位置为 -inf。"""
    keep = np.zeros_like(allowed)
    for idx in G["groups"]:
        sub = score[:, idx]
        if len(idx) <= 2:
            keep[:, idx] = allowed[:, idx]
            continue
        order = np.argsort(-sub, axis=1)
        rk = np.empty_like(order)
        rows = np.arange(sub.shape[0])[:, None]
        rk[rows, order] = np.arange(len(idx))[None, :]
        keep[:, idx] = allowed[:, idx] & (rk < 2)
    return keep


def perf_all(ret, lo, win):
    out = {}
    m = RC.metr(ret, lo)
    out[win] = m
    if win == "W2013":
        for s in SEGS:
            out[s] = RC.metr(ret, lo, G["masks"][s])
    return out


def run_one(f, cost):
    D = RC.data()
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    r0 = E.simulate(D["closeT"], f, cfg)
    f2 = dict(f); f2["base_ret"] = r0["ret"]
    rr = E.simulate(D["closeT"], f2, replace(cfg, rule=E.VolTarget(0.15, 60, 40, every=10)))
    return np.asarray(rr["ret"]), np.asarray(rr["turn"]), np.asarray(rr["expo"])


def random_feats(base, seed, filt, cap):
    rng = np.random.default_rng(seed)
    T, N = G["valid"].shape
    u = rng.random((T, N))
    allowed = G["valid"] & (G["ma_ok"] if filt else True)
    score = np.where(allowed, u, -np.inf)
    cand = cap2(score, allowed) if cap else allowed
    f = dict(base)
    idx, cols = base["rm"].index, base["rm"].columns
    f["rm"] = pd.DataFrame(np.where(allowed, u, np.nan), index=idx, columns=cols)
    f["cand"] = pd.DataFrame(cand, index=idx, columns=cols)
    return f


def task(args):
    null, seed, cost = args
    o = NULLS[null]
    rows = []
    for w in RC.START_W:
        base = G["base"][w]
        f = random_feats(base, seed, o["filt"], o["cap"])
        ret, turn, expo = run_one(f, cost)
        for seg, m in perf_all(ret, base["start"], w).items():
            rows.append({"null": null, "seed": seed, "cost_bp": int(round(cost * 1e4)), "window": seg,
                         **{k: m[k] for k in ("cagr", "mdd", "calmar", "sharpe", "vol")}})
    return rows


def strategies(cost):
    rows = []
    for nm, o in (("S4", dict(sector=True)), ("INCUMBENT", {})):
        for w, sd in RC.START_W.items():
            d = K.sat_run(sd, cost, **o)
            for seg, m in perf_all(d["ret"], d["lo"], w).items():
                rows.append({"arm": nm, "cost_bp": int(round(cost * 1e4)), "window": seg,
                             **{k: m[k] for k in ("cagr", "mdd", "calmar", "sharpe", "vol")}})
    return rows


def main(n=None):
    setup()
    st = pd.DataFrame(strategies(0.0005) + strategies(0.002))
    st.to_csv(RUN / "strategies.csv", index=False)
    print(st[st.window.isin(list(RC.START_W))].round(3).to_string(), flush=True)
    tasks = [(nl, s, c) for c in (0.0005, 0.002) for nl, o in NULLS.items() for s in (list(o["seeds"])[:n] if n else o["seeds"])]
    t0 = time.time()
    rows = []
    with Pool(6) as p:
        for k, r in enumerate(p.imap_unordered(task, tasks, chunksize=5)):
            rows += r
            if k % 200 == 0:
                print(k, len(tasks), round(time.time() - t0), flush=True)
    nm = pd.DataFrame(rows)
    nm.to_csv(RUN / ("null_metrics.csv.gz" if not n else "null_metrics_test.csv.gz"), index=False)
    print("done", time.time() - t0)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
