#!/usr/bin/env python3
"""components 后处理：成本敏感性表、DSR/PSR、核心权重构成、事后参考组合（不进判定）。"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import components_core as K  # noqa: E402
import recombine_p3_analyze as A3  # noqa: E402

RC, PB = K.RC, K.PB


def main():
    D = RC.data()
    cal = D["cal"]
    res = pd.read_csv(K.RUN / "results.csv")
    ver = json.loads((K.RUN / "verdict.json").read_text())
    # ---- 成本敏感性
    arms = ["DIV-IV", "C1", "C2", "C3", "ALLIN-core", "S2+VT15", "S1", "S2r", "S3", "S4", "R1@S2+VT15", "R2@S2+VT15",
            "ALLIN-sat", "MIX80/20", "R1@MIX", "R2@MIX", "ALL-IN", "ADOPTED-ONLY"]
    x = res[res.window.isin(list(RC.START_W)) & res.arm.isin(arms)]
    cs = x.pivot_table(index="arm", columns=["window", "cost_bp"], values="calmar").reindex(arms)
    tv = x[x.cost_bp == 5].pivot_table(index="arm", columns="window", values="turn_two_way").reindex(arms)
    cs.round(3).to_csv(K.RUN / "cost_calmar.csv")
    tv.round(2).to_csv(K.RUN / "turnover_5bp.csv")
    # 成本下判定是否仍成立（卡玛条件 ①，崩盘段不随成本复算）
    INC = {"C1": "DIV-IV", "C2": "DIV-IV", "C3": "DIV-IV", "ALLIN-core": "DIV-IV",
           **{a: "S2+VT15" for a in ["S1", "S2r", "S3", "S4", "R1@S2+VT15", "R2@S2+VT15", "ALLIN-sat"]},
           **{a: "MIX80/20" for a in ["R1@MIX", "R2@MIX", "ALL-IN", "ADOPTED-ONLY"]}}
    c1_cost = {}
    for a, inc in INC.items():
        c1_cost[a] = {bp: int(sum(cs.loc[a, (w, bp)] >= cs.loc[inc, (w, bp)] for w in RC.START_W)) for bp in (5, 10, 15, 20)}
    # ---- DSR
    Z = np.load(K.CC / "w2013_rets.npz")
    lo = int(Z["lo"])
    Zr = np.load(RC.CACHE / "combos_5bp.npz")
    R13, LO13 = Zr["R_W2013"], Zr["LO_W2013"]
    srs = np.array([R13[i, LO13[i]:].mean() / R13[i, LO13[i]:].std(ddof=1) for i in range(R13.shape[0])])
    vsr = float(np.var(srs, ddof=1))
    dsr = {}
    passers = [a for a, v in ver["verdicts"].items() if v.get("adopt")] + ["ALL-IN"]
    for a in dict.fromkeys(passers):
        r = Z[a.replace("/", "_")][lo:] if a.replace("/", "_") in Z.files else None
        if r is None:
            continue
        inc = INC.get(a, "MIX80/20")
        ri = Z[inc.replace("/", "_")][lo:]
        sr = float(r.mean() / r.std(ddof=1)); g3, g4 = A3.skew(r), A3.kurtosis(r)
        sri = float(ri.mean() / ri.std(ddof=1))
        d = {"SR_ann": sr * math.sqrt(252), "SR_inc_ann": sri * math.sqrt(252), "PSR_vs_inc": A3.psr(sr, len(r), g3, g4, sri)}
        for nl, n in (("N13", 13), ("N1496", 1496)):
            d[f"DSR_{nl}"] = A3.dsr(sr, len(r), g3, g4, vsr, n)[0]
        # 超额（相对在位）收益的 PSR：差额序列夏普 > 0 的概率
        ex = r - ri
        se = float(ex.mean() / ex.std(ddof=1)) if ex.std() > 0 else 0.0
        d["PSR_excess_gt0"] = A3.psr(se, len(ex), A3.skew(ex), A3.kurtosis(ex), 0.0) if ex.std() > 0 else None
        d["excess_SR_ann"] = se * math.sqrt(252)
        dsr[a] = d
    # ---- 核心权重构成（W2013，月末目标均值）
    m, erp = K.erp_mult()
    R = K.core_returns()
    comp = {}
    for nm, kw in (("DIV-IV", {}), ("C1", dict(assets=K.ALL13, eq=K.EQ13)), ("C2", dict(mode="ERC")),
                   ("C3", dict(mult=m)), ("ALLIN-core", dict(mode="ERC", assets=K.ALL13, eq=K.EQ13, mult=m))):
        tg, mu = K.core_targets(R, lo, **kw)
        W = pd.DataFrame(tg).T.fillna(0.0)
        comp[nm] = W.mean().round(3).to_dict()
        comp[nm]["_bondlike(511010+511360+511380)"] = round(float(W.reindex(columns=["sh511010", "sh511360", "sh511380"], fill_value=0).sum(axis=1).mean()), 3)
        comp[nm]["_equity"] = round(float(W.reindex(columns=K.EQ13, fill_value=0).sum(axis=1).mean()), 3)
    # ERP 描述
    erp_desc = {"erp_first": str(erp.index[0].date()), "m_mean_2013+": float(m.loc["2013":].mean()),
                "m_share_gt1_2013+": float((m.loc["2013":] > 1).mean()), "m_last": float(m.iloc[-1]), "erp_last": float(erp.iloc[-1])}
    # ---- 事后参考组合（不进判定）：C3 核心 + S2r+S4 卫星，80/20
    rows = []
    for bp in (5, 10, 15, 20):
        cost = bp / 1e4
        for w, sdate in RC.START_W.items():
            sat = K.sat_run(sdate, cost, skip=5, sector=True)
            inc = K.sat_run(sdate, cost)
            lo_w = inc["lo"]
            core = K.core_run(lo_w, cost, mult=m)
            mx = RC.mix(core, sat, 0.8, lo_w, cost)
            segs = [(w, cal[lo_w], RC.END)]
            if w == "W2013":
                segs += [(s, pd.Timestamp(a), pd.Timestamp(b)) for s, (a, b) in RC.SUB_W.items()]
            for seg, a, b in segs:
                msk = RC.seg_mask(a, b)
                rows.append({"cost_bp": bp, "window": seg, "arm": "POSTHOC[C3+S2r+S4,80/20]", **RC.metr(mx["ret"], lo_w, msk)})
                rows.append({"cost_bp": bp, "window": seg, "arm": "POSTHOC-sat[S2r+S4+VT15]", **RC.metr(sat["ret"], sat["lo"], msk, sat["turn"], sat["expo"])})
    ph = pd.DataFrame(rows)
    ph.to_csv(K.RUN / "posthoc_reference.csv", index=False)
    out = {"c1_count_by_cost": c1_cost, "dsr": dsr, "var_SR_trials_from_recombine": vsr, "core_weights_mean_W2013": comp,
           "erp": erp_desc}
    (K.RUN / "post.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float))
    with pd.option_context("display.width", 250, "display.max_rows", 200, "display.max_columns", 30):
        print(cs.round(3).to_string()); print(tv.round(1).to_string())
        print(ph[ph.window.isin(list(RC.START_W) + ["Y2015", "Y2018", "S2021_24", "S2023_now"])][["cost_bp", "window", "arm", "cagr", "mdd", "calmar"]].round(3).to_string())
    print(json.dumps(out, ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
