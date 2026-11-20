#!/usr/bin/env python3
"""第 3 部分补充（非预登记，只报告）：子集 PBO/DSR/滚动前推（只挑卫星规则 C0；只在 C80 内挑），赢家成本敏感性。"""
from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recombine_core as C  # noqa: E402
import recombine_p3_analyze as A  # noqa: E402


def pbo(Mm, S=16, metric="calmar"):
    N = Mm.shape[1]
    blocks = np.array_split(np.arange(len(Mm)), S)
    lam = []
    for comb in itertools.combinations(range(S), S // 2):
        isx = np.concatenate([blocks[b] for b in comb])
        oos = np.concatenate([blocks[b] for b in range(S) if b not in comb])
        f = (lambda X: A.calmar_mat(X, 12)) if metric == "calmar" else A.sharpe_mat
        vi, vo = f(Mm[isx]), f(Mm[oos])
        vi = np.where(np.isfinite(vi), vi, -1e9)
        vo = np.nan_to_num(vo, posinf=1e9, neginf=-1e9)
        b = int(np.argmax(vi))
        r = (vo < vo[b]).sum() + 0.5 * ((vo == vo[b]).sum() - 1) + 1
        om = r / (N + 1)
        lam.append(math.log(om / (1 - om)))
    lam = np.array(lam)
    return float((lam <= 0).mean()), float(np.median(lam))


def main():
    D = C.data()
    cal = D["cal"]
    Z = np.load(C.CACHE / "combos_5bp.npz")
    R13, LO13 = Z["R_W2013"], Z["LO_W2013"]
    names = [C.combo_name(c) for c in C.COMBOS]
    core = np.array([c[6] for c in C.COMBOS])
    inc_i = C.COMBOS.index(C.INCUMBENT)
    dfm = pd.DataFrame(R13.T.astype(float), index=cal)
    mon = ((1 + dfm).resample("ME").prod() - 1).loc[:"2026-10-31"].iloc[-160:].to_numpy()
    out = {}
    for lab, sel in (("C0_satellite_only", core == 0), ("C80_only", core == 0.8), ("C70_only", core == 0.7)):
        idx = np.flatnonzero(sel)
        Mm = mon[:, idx]
        d = {"n": int(len(idx))}
        d["PBO_calmar"], d["lambda_med_calmar"] = pbo(Mm, metric="calmar")
        d["PBO_sharpe"], _ = pbo(Mm, metric="sharpe")
        # 赢家 + DSR（日频，N=子集数）
        cm = np.array([A.perf(R13[i, LO13[i]:])["calmar"] for i in idx])
        w = idx[int(np.nanargmax(cm))]
        r = R13[w, LO13[w]:].astype(float)
        sr = float(r.mean() / r.std(ddof=1))
        srs = np.array([R13[i, LO13[i]:].mean() / R13[i, LO13[i]:].std(ddof=1) for i in idx])
        ev = np.linalg.eigvalsh(np.nan_to_num(np.corrcoef(Mm.T)))
        d["winner"] = names[w]
        d["winner_W2013"] = A.perf(r)
        d["n_eff_participation"] = float(ev.sum() ** 2 / (ev ** 2).sum())
        for nl, nn in (("Nsub", len(idx)), ("Nsub+25", len(idx) + 25)):
            d[f"DSR_{nl}"] = A.dsr(sr, len(r), A.skew(r), A.kurtosis(r), float(np.var(srs, ddof=1)), nn)[0]
        # 子集内滚动前推
        chain = np.zeros(len(cal)); prev = None; picks = []
        for y in range(2015, 2027):
            ym = cal.year == y
            tr = np.full(len(idx), -np.inf)
            for k, i in enumerate(idx):
                m = (np.arange(len(cal)) >= LO13[i]) & (cal <= pd.Timestamp(f"{y-1}-12-31"))
                c = A.perf(R13[i, m])["calmar"]
                tr[k] = c if np.isfinite(c) else -np.inf
            b = idx[int(np.argmax(tr))]
            rr = R13[b, ym].astype(float).copy()
            if prev is not None and b != prev:
                rr[0] -= 0.001
            chain[ym] = rr; prev = b; picks.append(names[b])
        wf = cal >= pd.Timestamp("2015-01-01")
        d["WF_chain"] = A.perf(chain[wf])
        d["WF_picks"] = picks
        d["n_distinct_picks"] = len(set(picks))
        out[lab] = d
    out["incumbent_WF_period"] = A.perf(R13[inc_i, cal >= pd.Timestamp("2015-01-01")])

    # 赢家与在位、DIV-IV、MIX70/30 的成本敏感性（三窗口）
    win = C.COMBOS[names.index(json.loads((C.RUN / "verdict_part3.json").read_text())["winner"])]
    rows = []
    for bp in (5, 10, 15, 20):
        cost = bp / 1e4
        for wn, sdate in C.START_W.items():
            o = C.run_base(win[:5], sdate, cost, overlays=("none", win[5]))
            sat = o[win[5]]
            d = C.mix(C.div_ret(sat["lo"], cost), sat, win[6], sat["lo"], cost)
            rows.append({"cost_bp": bp, "window": wn, "arm": "WINNER", **C.metr(d["ret"], d["lo"])})
            o2 = C.run_base(("M3", "MA60", 5, "IV", 10), sdate, cost, overlays=("none", "VT15"))
            s2 = o2["VT15"]
            d2 = C.mix(C.div_ret(s2["lo"], cost), s2, 0.8, s2["lo"], cost)
            rows.append({"cost_bp": bp, "window": wn, "arm": "MIX80/20", **C.metr(d2["ret"], d2["lo"])})
            dv = C.div_ret(s2["lo"], cost)
            rows.append({"cost_bp": bp, "window": wn, "arm": "DIV-IV", **C.metr(dv["ret"], dv["lo"])})
    cs = pd.DataFrame(rows)
    cs.to_csv(C.RUN / "part3_winner_cost.csv", index=False)
    (C.RUN / "part3_extra.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float))
    print(json.dumps(out, ensure_ascii=False, indent=1, default=float))
    print(cs[["cost_bp", "window", "arm", "cagr", "mdd", "calmar"]].round(3).to_string())


if __name__ == "__main__":
    main()
