#!/usr/bin/env python3
"""第 3 部分分析：赢家、窗口排名、PBO(CSCV)、DSR、滚动前推。规则见 FREEZE.md。"""
from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from statistics import NormalDist

_N = NormalDist()


class norm:  # noqa: N801  (scipy 不可用，用标准库)
    cdf = staticmethod(_N.cdf)
    ppf = staticmethod(_N.inv_cdf)


def skew(r):
    r = np.asarray(r, float); d = r - r.mean()
    return float((d ** 3).mean() / (d ** 2).mean() ** 1.5)


def kurtosis(r, fisher=False):
    r = np.asarray(r, float); d = r - r.mean()
    k = float((d ** 4).mean() / (d ** 2).mean() ** 2)
    return k - 3 if fisher else k

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recombine_core as C  # noqa: E402

TD = 252


def perf(r):
    r = np.asarray(r, float)
    eq = np.cumprod(1 + r)
    yrs = len(r) / TD
    cagr = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    vol = float(np.std(r, ddof=1) * np.sqrt(TD))
    return dict(cagr=cagr, vol=vol, sharpe=float(np.mean(r) * TD / vol) if vol > 0 else np.nan,
                mdd=dd, calmar=cagr / abs(dd) if dd < 0 else np.nan)


def calmar_mat(Rm, ppy):
    """Rm: (T, N) 收益矩阵 → 每列卡玛（向量化）。"""
    eq = np.cumprod(1 + Rm, axis=0)
    n = Rm.shape[0]
    cagr = np.where(eq[-1] > 0, eq[-1] ** (ppy / n) - 1, -1.0)
    dd = (eq / np.maximum.accumulate(eq, axis=0) - 1).min(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cm = np.where(dd < 0, cagr / np.abs(dd), np.inf * np.sign(cagr))
    return cm


def sharpe_mat(Rm):
    sd = Rm.std(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0, Rm.mean(axis=0) / sd, 0.0)


def dsr(sr, T, g3, g4, var_sr, N):
    gam = 0.5772156649
    sr0 = math.sqrt(var_sr) * ((1 - gam) * norm.ppf(1 - 1 / N) + gam * norm.ppf(1 - 1 / (N * math.e)))
    z = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)
    return float(norm.cdf(z)), float(sr0)


def psr(sr, T, g3, g4, sr_star=0.0):
    z = (sr - sr_star) * math.sqrt(T - 1) / math.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)
    return float(norm.cdf(z))


def main():
    D = C.data()
    cal = D["cal"]
    Z = np.load(C.CACHE / "combos_5bp.npz")
    W = list(C.START_W)
    N = len(C.COMBOS)
    names = [C.combo_name(c) for c in C.COMBOS]
    inc_i = C.COMBOS.index(C.INCUMBENT)
    mix70_i = C.COMBOS.index(C.INCUMBENT[:6] + (0.7,))
    out = {"n_combos": N}

    # ---------- 每窗口指标
    rows = []
    mets = {}
    for w in W:
        R, X, LO, TURN = Z[f"R_{w}"], Z[f"X_{w}"], Z[f"LO_{w}"], Z[f"TURN_{w}"]
        ms = []
        for i in range(N):
            lo = LO[i]
            m = perf(R[i, lo:])
            m["avg_expo"] = float(X[i, lo:].mean())
            m["sat_turn_two_way"] = TURN[i] / ((len(cal) - lo) / TD)
            ms.append(m)
        mets[w] = pd.DataFrame(ms, index=names)
        mets[w]["lo"] = [str(cal[l].date()) for l in LO]
    # 崩盘段（W2013 路径切片）
    R13, LO13 = Z["R_W2013"], Z["LO_W2013"]
    seg = {}
    for s, (a, b) in C.SUB_W.items():
        m = C.seg_mask(a, b)
        seg[s] = pd.DataFrame([perf(R13[i, m]) for i in range(N)], index=names)
    out["lo_W2013_range"] = [str(cal[LO13.min()].date()), str(cal[LO13.max()].date())]

    m13 = mets["W2013"]
    cal13 = m13["calmar"].to_numpy()
    win = int(np.nanargmax(cal13))
    wname = names[win]
    out["winner"] = wname
    out["incumbent_check_calmar_W2013"] = float(cal13[inc_i])
    out["mix70_check_calmar_W2013"] = float(cal13[mix70_i])

    def rank_desc(v, i):
        v = np.where(np.isfinite(v), v, -np.inf)
        return int((v > v[i]).sum() + 1)

    rk = {}
    for nm, i in (("winner", win), ("incumbent", inc_i), ("mix70_30", mix70_i)):
        d = {}
        for w in W:
            d[w] = {"calmar_rank": rank_desc(mets[w]["calmar"].to_numpy(), i),
                    **{k: float(mets[w].iloc[i][k]) for k in ("cagr", "vol", "mdd", "calmar", "sharpe", "avg_expo", "sat_turn_two_way")}}
        for s in seg:
            d[s] = {"mdd_rank(shallow=1)": rank_desc(seg[s]["mdd"].to_numpy(), i),
                    "cagr_rank": rank_desc(seg[s]["cagr"].to_numpy(), i),
                    **{k: float(seg[s].iloc[i][k]) for k in ("cagr", "mdd", "calmar")}}
        rk[nm] = d
    out["ranks"] = rk

    # top 20 表
    top = m13.sort_values("calmar", ascending=False).head(20).copy()
    for w in ("W2017", "W2019"):
        top[f"calmar_{w}"] = mets[w].loc[top.index, "calmar"]
        top[f"rank_{w}"] = [rank_desc(mets[w]["calmar"].to_numpy(), names.index(n)) for n in top.index]
    top.to_csv(C.RUN / "part3_top20_W2013.csv")
    allm = pd.concat({w: mets[w] for w in W}, axis=1)
    allm.to_csv(C.RUN / "part3_all_combos_metrics.csv")
    # 维度边际：每个维度取值下 W2013 卡玛中位数
    marg = {}
    parts = pd.DataFrame(C.COMBOS, columns=["H", "F", "N", "W", "R", "O", "C"], index=names)
    for col in parts.columns:
        marg[col] = {str(k): {w: round(float(mets[w]["calmar"][parts[col] == k].median()), 4) for w in W}
                     for k in parts[col].unique()}
    out["dimension_median_calmar"] = marg
    # 分布：多少组合在三窗口都 ≥ 在位
    ge = np.all([mets[w]["calmar"].to_numpy() >= mets[w]["calmar"].iloc[inc_i] for w in W], axis=0)
    out["n_ge_incumbent_all3"] = int(ge.sum())
    out["n_ge_mix70_all3"] = int(np.all([mets[w]["calmar"].to_numpy() >= mets[w]["calmar"].iloc[mix70_i] for w in W], axis=0).sum())
    out["winner_rank_by_core"] = {str(c): int(rank_desc(np.where(parts["C"] == c, cal13, -np.inf), int(np.nanargmax(np.where(parts["C"] == c, cal13, -np.inf))))) for c in C.C_SETS}
    out["best_by_core"] = {str(c): names[int(np.nanargmax(np.where(parts["C"] == c, np.nan_to_num(cal13, nan=-9), -9)))] for c in C.C_SETS}
    out["best_by_core_calmar"] = {str(c): float(np.nanmax(np.where(parts["C"] == c, cal13, np.nan))) for c in C.C_SETS}

    # ---------- 月收益矩阵（W2013）
    dfm = pd.DataFrame(R13.T.astype(float), index=cal)
    mon = (1 + dfm).resample("ME").prod() - 1
    mon = mon.loc[:"2026-10-31"].iloc[-160:]
    out["pbo_months"] = [str(mon.index[0].date()), str(mon.index[-1].date()), len(mon)]
    Mm = mon.to_numpy()
    S = 16
    blocks = np.array_split(np.arange(len(Mm)), S)
    res_pbo = {}
    for metric in ("calmar", "sharpe"):
        lam, oos_best, is_best, loss = [], [], [], []
        for comb in itertools.combinations(range(S), S // 2):
            isx = np.concatenate([blocks[b] for b in comb])
            oos = np.concatenate([blocks[b] for b in range(S) if b not in comb])
            if metric == "calmar":
                vi, vo = calmar_mat(Mm[isx], 12), calmar_mat(Mm[oos], 12)
            else:
                vi, vo = sharpe_mat(Mm[isx]), sharpe_mat(Mm[oos])
            vi = np.where(np.isfinite(vi), vi, -1e9)
            vo2 = np.where(np.isfinite(vo), vo, np.nan_to_num(vo, posinf=1e9, neginf=-1e9))
            b = int(np.argmax(vi))
            r = (vo2 < vo2[b]).sum() + 0.5 * ((vo2 == vo2[b]).sum() - 1) + 1   # 1..N，N=最好
            om = r / (N + 1)
            lam.append(math.log(om / (1 - om)))
            is_best.append(vi[b]); oos_best.append(vo2[b])
            loss.append(float(sharpe_mat(Mm[oos][:, [b]])[0] < 0))
        lam = np.array(lam)
        slope = float(np.polyfit(np.array(is_best), np.array(oos_best), 1)[0])
        res_pbo[metric] = {"PBO": float((lam <= 0).mean()), "lambda_median": float(np.median(lam)),
                           "n_splits": len(lam), "p_oos_sharpe_lt0": float(np.mean(loss)),
                           "is_oos_slope": slope, "oos_median_of_is_best": float(np.median(oos_best)),
                           "oos_median_all": None}
    out["pbo"] = res_pbo

    # ---------- DSR
    lo_w = LO13[win]
    rd = R13[win, lo_w:].astype(float)
    srs_d = np.array([np.mean(R13[i, LO13[i]:]) / np.std(R13[i, LO13[i]:], ddof=1) for i in range(N)])
    srs_m = sharpe_mat(Mm)
    corr = np.corrcoef(Mm.T)
    ev = np.linalg.eigvalsh(np.nan_to_num(corr))
    n_eff = float(ev.sum() ** 2 / (ev ** 2).sum())
    out["n_eff_participation"] = n_eff
    rm = Mm[:, win]
    dres = {}
    for lab, r, srs in (("daily", rd, srs_d), ("monthly", rm, srs_m)):
        sr = float(np.mean(r) / np.std(r, ddof=1))
        g3, g4 = float(skew(r)), float(kurtosis(r, fisher=False))
        dd = {"SR": sr, "SR_ann": sr * math.sqrt(TD if lab == "daily" else 12), "T": len(r), "skew": g3, "kurt": g4,
              "var_SR_trials": float(np.var(srs, ddof=1)), "PSR_vs0": psr(sr, len(r), g3, g4)}
        inc_r = R13[inc_i, LO13[inc_i]:] if lab == "daily" else Mm[:, inc_i]
        dd["PSR_vs_incumbent_SR"] = psr(sr, len(r), g3, g4, float(np.mean(inc_r) / np.std(inc_r, ddof=1)))
        for nl, nn in (("N1458", N), ("N_eff", max(n_eff, 2)), ("N1458+25", N + 25)):
            p, s0 = dsr(sr, len(r), g3, g4, dd["var_SR_trials"], nn)
            dd[f"DSR_{nl}"], dd[f"SR0_{nl}"] = p, s0
        dres[lab] = dd
    out["dsr"] = dres

    # ---------- 滚动前推
    years = list(range(2015, 2027))
    chain = np.zeros(len(cal))
    picks = []
    prev = None
    for y in years:
        tr_end = pd.Timestamp(f"{y - 1}-12-31")
        ym = (cal.year == y)
        tr = np.full(N, -np.inf)
        for i in range(N):
            m = np.arange(len(cal)) >= LO13[i]
            m &= cal <= tr_end
            if m.sum() > 60:
                c = perf(R13[i, m])["calmar"]
                tr[i] = c if np.isfinite(c) else -np.inf
        b = int(np.argmax(tr))
        r = R13[b, ym].astype(float).copy()
        sw = prev is not None and b != prev
        if sw:
            r[0] -= 2 * 0.0005
        chain[ym] = r
        picks.append({"year": y, "pick": names[b], "train_calmar": float(tr[b]), "switched": bool(sw),
                      "oos_ret": float(np.prod(1 + r) - 1),
                      "inc_ret": float(np.prod(1 + R13[inc_i, ym]) - 1),
                      "mix70_ret": float(np.prod(1 + R13[mix70_i, ym]) - 1)})
        prev = b
    wf_m = (cal >= pd.Timestamp("2015-01-01"))
    div = C.div_ret(LO13[inc_i], 0.0005)["ret"]
    wf = {"WF_chain": perf(chain[wf_m]), "incumbent": perf(R13[inc_i, wf_m]), "mix70_30": perf(R13[mix70_i, wf_m]),
          "DIV-IV": perf(np.asarray(div)[wf_m]), "IS_winner": perf(R13[win, wf_m])}
    out["walk_forward"] = {"period": ["2015-01-05", "2026-11-13"], "metrics": wf, "picks": picks}
    pd.DataFrame(picks).to_csv(C.RUN / "part3_walkforward_picks.csv", index=False)
    pd.DataFrame({"WF_chain": np.cumprod(1 + chain[wf_m]), "incumbent": np.cumprod(1 + R13[inc_i, wf_m]),
                  "mix70_30": np.cumprod(1 + R13[mix70_i, wf_m]), "DIV-IV": np.cumprod(1 + np.asarray(div)[wf_m]),
                  "IS_winner": np.cumprod(1 + R13[win, wf_m])}, index=cal[wf_m]).to_csv(C.RUN / "part3_walkforward_nav.csv")

    # ---------- 判定
    c1 = res_pbo["calmar"]["PBO"] < 0.5
    c2 = dres["daily"]["DSR_N_eff"] >= 0.95
    c3 = (wf["WF_chain"]["calmar"] >= wf["incumbent"]["calmar"]) and (wf["WF_chain"]["calmar"] >= wf["mix70_30"]["calmar"])
    out["verdict"] = {"c1_PBO_lt_0.5": bool(c1), "c2_DSR_Neff_ge_0.95(daily)": bool(c2),
                      "c2_monthly_ref": bool(dres["monthly"]["DSR_N_eff"] >= 0.95),
                      "c3_WF_ge_inc_and_mix70": bool(c3),
                      "credible": bool(c1 and c2 and c3)}
    (C.RUN / "verdict_part3.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, default=float))
    print(json.dumps({k: v for k, v in out.items() if k not in ("dimension_median_calmar",)}, ensure_ascii=False, indent=1, default=float))
    with pd.option_context("display.width", 250):
        print(top[["cagr", "vol", "mdd", "calmar", "avg_expo", "calmar_W2017", "rank_W2017", "calmar_W2019", "rank_W2019"]].round(3))
    print(json.dumps(marg, ensure_ascii=False))


if __name__ == "__main__":
    main()
