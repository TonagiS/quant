#!/usr/bin/env python3
"""combo3 事后（看到结果之后）补充：DONCH 通道窗口与 P12 迟滞阈值的邻域敏感性。非预登记，单独记试验数。"""
import itertools, sys
from dataclasses import replace
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import c3x_combo as X
PV, E, K = X.PV, X.E, X.K
WINS = [(40, 80), (60, 120), (90, 180), (120, 250), (60,), (120,)]

def donch(close, member, wins, cap):
    f = X.feats(close, member, "S2", cap if cap == "NOCAP" else "NOCAP")
    v = f["valid"]; rk = lambda A: A.where(v).rank(axis=1, pct=True)
    pos = 0
    for n in wins:
        hi = close.rolling(n, min_periods=n).max(); lo = close.rolling(n, min_periods=n).min()
        pos = pos + rk((close - lo) / (hi - lo).where(hi > lo))
    f["rm"] = pos / len(wins)
    n0 = wins[0]
    hi = close.rolling(n0, min_periods=n0).max(); lo = close.rolling(n0, min_periods=n0).min()
    cand = v & f["rm"].notna() & (close > (hi + lo) / 2).fillna(False)
    if cap == "CAP2":
        rmv, cv = f["rm"].to_numpy(), cand.to_numpy().copy()
        sv = np.array([PV.G["sec"].get(c, "") for c in close.columns])
        for i in range(len(cv)):
            idx = np.flatnonzero(cv[i] & ~np.isnan(rmv[i]))
            if len(idx) <= 2: continue
            cnt = {}
            for j in idx[np.argsort(-rmv[i, idx])]:
                cnt[sv[j]] = cnt.get(sv[j], 0) + 1
                if cnt[sv[j]] > 2: cv[i, j] = False
        cand = pd.DataFrame(cv, index=close.index, columns=close.columns)
    f["cand"] = cand
    return f

def run(lists, wins, cost, vt):
    ever = sorted({c for v in lists.values() for c in v}); close = PV.G["C"][ever]
    M = PV.PB.member_mask(lists, PV.G["cal"], ever)
    f = donch(close, M, wins, "CAP2")
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    r0 = E.simulate(close, f, cfg); f2 = dict(f); f2["base_ret"] = r0["ret"]
    rr = E.simulate(close, f2, replace(cfg, rule=E.VolTarget(vt / 100, 60, 40, every=10)))
    return np.asarray(rr["ret"]), f["start"]

if __name__ == "__main__":
    X.setup_parent()
    import pv_pools
    pools = {"BASE": X.G["lists"]["BASE"], "P12_HYST60": X.G["lists"]["P12_HYST60"]}
    def hyst(band):   # 同 pv_pools P12_HYST60，只把 60 换成 band
        out, prev = {}, None
        for d in PV.G["me"]:
            sr = PV.ranked(d); top = set(sr.index[:band])
            keep = [c for c in (prev or []) if c in top]
            prev = (keep + [c for c in sr.index if c not in keep])[:40]
            out[d] = prev
        return out
    for b in (50, 80):
        pools[f"P12_HYST{b}"] = hyst(b)
    rows = []
    for cost in X.COSTS:
        bp = int(round(cost * 1e4))
        R = X.load_cells(bp); s4 = R[X.key(*X.S4_KEY)]; rS4 = X.roll_vec(s4["ret"], s4["lo"])
        for (pn, L), w, vt in itertools.product(pools.items(), WINS, (10, 15)):
            r, lo = run(L, w, cost, vt); rv = X.roll_vec(r, lo); a = X.cmp(rv, rS4)
            m = E.perf(r[PV.starts()[0]:])
            rows.append(dict(pool=pn, wins="/".join(map(str, w)), vt=vt, cost_bp=bp, calmar=m["calmar"], cagr=m["cagr"], mdd=m["mdd"],
                             roll_med=float(np.nanmedian(rv[0])), s4_share=a[0], s4_med=a[1], s4_seg=a[2], s4_pass=a[3]))
            print(rows[-1], flush=True)
    pd.DataFrame(rows).to_csv(X.RUN / "posthoc_donch_sens.csv", index=False)
