#!/usr/bin/env python3
"""第 3 部分：1458 组合 × 3 窗口回测，日收益缓存到 data/cache_recombine/combos_{bp}bp.npz。"""
from __future__ import annotations

import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recombine_core as C  # noqa: E402

COST = 0.0005


def work(args):
    bk, w, cost = args
    sdate = C.START_W[w]
    out = C.run_base(bk, sdate, cost)
    res = []
    for o in C.O_SETS:
        sat = out[o]
        lo = sat["lo"]
        core = C.div_ret(lo, cost)
        for c in C.C_SETS:
            d = C.mix(core, sat, c, lo, cost)
            res.append((bk + (o, c), w, np.asarray(d["ret"], np.float32), np.asarray(d["expo"], np.float32),
                        float(np.sum(np.asarray(sat["turn"])[lo:])), lo))
    return res


def main(bp=5, windows=tuple(C.START_W)):
    cost = bp / 1e4
    C.data()
    tasks = [(bk, w, cost) for w in windows for bk in C.BASE_KEYS]
    idx = {c: i for i, c in enumerate(C.COMBOS)}
    T = len(C.data()["cal"])
    R = {w: np.zeros((len(C.COMBOS), T), np.float32) for w in windows}
    X = {w: np.zeros((len(C.COMBOS), T), np.float32) for w in windows}
    LO = {w: np.zeros(len(C.COMBOS), int) for w in windows}
    TURN = {w: np.zeros(len(C.COMBOS)) for w in windows}
    t0 = time.time()
    with Pool(6) as p:
        for k, res in enumerate(p.imap_unordered(work, tasks, chunksize=4)):
            for combo, w, r, x, tn, lo in res:
                i = idx[combo]
                R[w][i], X[w][i], LO[w][i], TURN[w][i] = r, x, lo, tn
            if k % 50 == 0:
                print(k, len(tasks), round(time.time() - t0, 1), flush=True)
    np.savez_compressed(C.CACHE / f"combos_{bp}bp.npz",
                        **{f"R_{w}": R[w] for w in windows}, **{f"X_{w}": X[w] for w in windows},
                        **{f"LO_{w}": LO[w] for w in windows}, **{f"TURN_{w}": TURN[w] for w in windows})
    print("saved", time.time() - t0)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
