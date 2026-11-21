#!/usr/bin/env python3
"""combo3（2026-11-21）：选池 × 趋势/选股信号 × 板块上限 × 仓位控制 全因子网格。
规则见 runs/2026-11-21_combo3/FREEZE.md。总收益面板 v2（trfix 7b3ef1d 之后）。样本内。
用法：python3 work/c3x_combo.py check | grid | null | post"""
from __future__ import annotations

import itertools
import json
import math
import sys
import time
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd
from statistics import NormalDist

_N = NormalDist()


class norm:  # 代替 scipy.stats.norm
    cdf = staticmethod(_N.cdf)
    ppf = staticmethod(_N.inv_cdf)


def skew(x):
    x = np.asarray(x); d = x - x.mean(); return float((d ** 3).mean() / (d ** 2).mean() ** 1.5)


def kurtosis(x, fisher=False):
    x = np.asarray(x); d = x - x.mean(); k = float((d ** 4).mean() / (d ** 2).mean() ** 2); return k - 3 if fisher else k

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pv_pools as PV  # noqa: E402

K, RC, E = PV.K, PV.RC, PV.E
RUN = PV.ROOT / "runs" / "2026-11-21_combo3"
CACHE = PV.ROOT / "data" / "cache_combo3"
POOLS = ["BASE", "P12_HYST60", "P5_N20", "P10_LOWCORR"]
SIGS = ["S2", "RAM", "REG", "RIBBON", "DONCH", "TSM"]
CAPS = ["CAP2", "NOCAP"]
SIZES = ["VT15", "NOVT", "VT10", "VT20", "ERC_VT15", "DDC_VT15", "BRD_VT15", "TSS_VT15"]
COSTS = (0.0005, 0.0020)
S4_KEY = ("BASE", "S2", "CAP2", "VT15")
PASS_SHARE, PASS_MED, PASS_SEG = 0.70, 0.05, 3
N_NULL = 100
G = {}


def key(p, s, c, z):
    return f"{p}|{s}|{c}|{z}"


# ------------------------------------------------------------------ 信号（资产层）
def feats(close, member, sig, cap):
    ret = close.pct_change(fill_method=None)
    vol20 = ret.rolling(20, min_periods=15).std()
    H = (20, 60, 120)
    mom = {n: close / close.shift(n) - 1.0 for n in H}
    ma = {n: close.rolling(n, min_periods=n).mean() for n in (20, 60, 120)}
    ma60 = ma[60]
    bars = close.notna().cumsum()
    valid = (bars >= 150) & close.notna() & mom[120].notna() & vol20.notna() & member
    rk = lambda X: X.where(valid).rank(axis=1, pct=True)  # noqa: E731
    s2 = (rk(mom[20]) + rk(mom[60]) + rk(mom[120])) / 3.0
    above = close > ma60
    if sig == "S2":
        rm, cand = s2, above
    elif sig == "RAM":       # 风险调整动量：n 日收益 / (n 日日收益标准差 × √n)
        sc = 0
        for n in H:
            sd = ret.rolling(n, min_periods=int(n * 0.75)).std() * np.sqrt(n)
            sc = sc + rk(mom[n] / sd)
        rm, cand = sc / 3.0, above
    elif sig == "REG":       # 90 日对数价线性回归：年化斜率 × R²；要求斜率 > 0 且 R² ≥ 0.3
        n = 90
        y = np.log(close)
        t = pd.Series(np.arange(len(close), dtype=float), index=close.index)
        Sy = y.rolling(n, min_periods=n).sum()
        Syy = (y * y).rolling(n, min_periods=n).sum()
        Sty = y.mul(t, axis=0).rolling(n, min_periods=n).sum()
        St = t.rolling(n).sum(); Stt = (t * t).rolling(n).sum()
        vt = (n * Stt - St * St)
        cov = n * Sty - Sy.mul(St, axis=0)
        slope = cov.div(vt, axis=0)
        vy = n * Syy - Sy * Sy
        r2 = (cov * cov).div(vt, axis=0) / vy.where(vy > 0)
        score = slope * 252 * r2
        rm = rk(score)
        cand = above & (slope > 0) & (r2 >= 0.3)
    elif sig == "RIBBON":    # 均线多头排列：MA20 > MA60 > MA120 且 收盘 > MA20
        rm = s2
        cand = (close > ma[20]) & (ma[20] > ma60) & (ma60 > ma[120])
    elif sig == "DONCH":     # 唐奇安：60/120 日通道位置的排名均值；要求收盘在 60 日通道中线之上
        pos = 0
        for n in (60, 120):
            hi = close.rolling(n, min_periods=n).max(); lo = close.rolling(n, min_periods=n).min()
            pos = pos + rk((close - lo) / (hi - lo).where(hi > lo))
        rm = pos / 2.0
        hi60 = close.rolling(60, min_periods=60).max(); lo60 = close.rolling(60, min_periods=60).min()
        cand = close > (hi60 + lo60) / 2
    elif sig == "TSM":       # 时序动量 + 截面：S2 排名，另要求 120 日与 60 日绝对收益 > 0
        rm = s2
        cand = above & (mom[120] > 0) & (mom[60] > 0)
    else:
        raise KeyError(sig)
    cand = valid & rm.notna() & cand.fillna(False)
    if cap == "CAP2":
        rmv, cv = rm.to_numpy(), cand.to_numpy().copy()
        sv = np.array([PV.G["sec"].get(c, "") for c in close.columns])
        for i in range(len(cv)):
            idx = np.flatnonzero(cv[i] & ~np.isnan(rmv[i]))
            if len(idx) <= 2:
                continue
            cnt = {}
            for j in idx[np.argsort(-rmv[i, idx])]:
                cnt[sv[j]] = cnt.get(sv[j], 0) + 1
                if cnt[sv[j]] > 2:
                    cv[i, j] = False
        cand = pd.DataFrame(cv, index=close.index, columns=close.columns)
    enough = (valid.sum(axis=1) >= 8) & s2.notna().any(axis=1)
    enough &= close.index >= pd.Timestamp(PV.SDATE)
    start = int(np.argmax(enough.to_numpy()))
    return {"ret": ret, "vol20": vol20, "mom": mom, "ma60": ma60, "valid": valid, "rm": rm,
            "cand": cand, "start": start, "bench": (1.0 + ret.mean(axis=1).fillna(0.0)).cumprod(),
            "_ma120": ma[120]}


# ------------------------------------------------------------------ 仓位（组合层）
def pool_mults(close, f):
    v = f["valid"]
    b = (close > f["ma60"]).where(v).mean(axis=1)
    m_brd = ((b - 0.2) / 0.4).clip(0, 1).fillna(1.0).to_numpy()          # 池内收盘>MA60 占比 0.2→0，0.6→1
    x = (close / f["_ma120"] - 1).where(v).median(axis=1)
    m_tss = (0.5 + 5 * x).clip(0, 1).fillna(1.0).to_numpy()              # 池内中位 close/MA120−1：−10%→0，0→0.5，+10%→1
    return m_brd, m_tss


def ddc_mult(ret, lo):
    """CPPI 式：用未叠加的 VT15 净值算回撤 dd，m = clip(4·(1+dd−0.75)/(1+dd), 0, 1)（地板 = 峰值 75%，乘数 4）。"""
    r = np.where(np.arange(len(ret)) >= lo, ret, 0.0)
    nav = np.cumprod(1 + r)
    dd = nav / np.maximum.accumulate(nav) - 1
    return np.clip(4 * (1 + dd - 0.75) / (1 + dd), 0, 1)


def pack(rr, lo):
    return dict(ret=np.asarray(rr["ret"], float), turn=np.asarray(rr["turn"], float), expo=np.asarray(rr["expo"], float), lo=lo)


def run_cell(pool, sig, cap, cost):
    """返回 {sizing: dict(ret, turn, expo, lo)}。"""
    lists = G["lists"][pool]
    ever = sorted({c for v in lists.values() for c in v})
    close = PV.G["C"][ever]
    M = G["M"][pool]
    f = feats(close, M, sig, cap)
    lo = f["start"]
    out = {}
    for wm, tag in (("invvol", ""), ("erc", "ERC_")):
        cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost, weight_mode=wm)
        r0 = E.simulate(close, f, cfg)
        if wm == "invvol":
            out["NOVT"] = pack(r0, lo)
        f2 = dict(f); f2["base_ret"] = r0["ret"]
        tg = [15, 10, 20] if wm == "invvol" else [15]
        for v in tg:
            rr = E.simulate(close, f2, replace(cfg, rule=E.VolTarget(v / 100, 60, 40, every=10)))
            out[f"{tag}VT{v}"] = pack(rr, lo)
    base = out["VT15"]
    m_brd, m_tss = pool_mults(close, f)
    out["DDC_VT15"] = K.apply_mult(base, ddc_mult(base["ret"], lo), cost)
    out["BRD_VT15"] = K.apply_mult(base, m_brd, cost)
    out["TSS_VT15"] = K.apply_mult(base, m_tss, cost)
    for d in out.values():
        d["lo"] = lo
    return out


# ------------------------------------------------------------------ 评估
def roll_vec(ret, lo, kind="sat", core=None, cost=None):
    cal = PV.G["cal"]
    st = PV.starts()
    v = []
    for s in st:
        if lo > s:
            v.append(np.nan); continue
        r = ret[s:] if kind == "sat" else RC.mix(core, dict(ret=ret, expo=np.ones(len(ret)), lo=lo), PV.WC, s, cost)["ret"][s:]
        v.append(PV.calmar(r))
    sg = []
    for g, (a, b) in PV.SEGS.items():
        l0 = int(cal.searchsorted(pd.Timestamp(a))); h0 = int(cal.searchsorted(pd.Timestamp(b), side="right"))
        if lo > l0:
            sg.append(np.nan); continue
        r = ret[l0:h0] if kind == "sat" else RC.mix(core, dict(ret=ret, expo=np.ones(len(ret)), lo=lo), PV.WC, l0, cost)["ret"][l0:h0]
        sg.append(PV.calmar(r))
    return np.array(v), np.array(sg)


def cmp(a, b):
    """a, b: (roll, seg) → share（缺失按非正）、中位差、正段数、pass。"""
    d = a[0] - b[0]
    share = float(np.sum(d > 0) / len(d))
    med = float(np.nanmedian(d)) if np.isfinite(d).any() else np.nan
    sd = a[1] - b[1]
    npos = int(np.sum(sd > 0))
    return share, med, npos, bool(share >= PASS_SHARE and med > PASS_MED and npos >= PASS_SEG)


def task_cell(args):
    pool, sig, cap, cost = args
    out = run_cell(pool, sig, cap, cost)
    bp = int(round(cost * 1e4))
    np.savez_compressed(CACHE / f"cell_{pool}_{sig}_{cap}_{bp}.npz", **{z: out[z]["ret"] for z in SIZES},
                        lo=np.array([out["VT15"]["lo"]]), **{f"turn_{z}": out[z]["turn"] for z in SIZES},
                        **{f"expo_{z}": out[z]["expo"] for z in SIZES})
    return pool, sig, cap, bp


def setup_parent():
    PV.setup()
    K.core_returns(); K.erp_mult()
    G["lists"] = {p: PV.lists_of(p) for p in POOLS}
    G["M"] = {}
    for p in POOLS:
        ever = sorted({c for v in G["lists"][p].values() for c in v})
        G["M"][p] = PV.PB.member_mask(G["lists"][p], PV.G["cal"], ever)


def load_cells(bp):
    R = {}
    for p, s, c in itertools.product(POOLS, SIGS, CAPS):
        z = np.load(CACHE / f"cell_{p}_{s}_{c}_{bp}.npz")
        lo = int(z["lo"][0])
        for sz in SIZES:
            R[key(p, s, c, sz)] = dict(ret=z[sz], lo=lo, turn=z[f"turn_{sz}"], expo=z[f"expo_{sz}"])
    return R


def broad_vt(cost):
    import prereg_satellite as PS
    import sat_vt15_control as SVC
    if not PS.G:
        PS.setup()
    return SVC.broad_vt_run(cost)


def task_null(args):
    k, seed, cost = args
    pool, sig, cap, sz = k.split("|")
    lists = G["lists"][pool]
    ever = sorted({c for v in lists.values() for c in v})
    close = PV.G["C"][ever]
    f = feats(close, G["M"][pool], sig, cap)
    rng = np.random.default_rng(seed)
    rnd = pd.DataFrame(rng.random(f["rm"].shape), index=f["rm"].index, columns=f["rm"].columns)
    f["rm"] = rnd.where(f["rm"].notna())          # 同一候选集内随机排序（保留趋势过滤、板块上限前的候选）
    if cap == "CAP2":                               # 板块上限按随机分数重新施加
        f0 = feats(close, G["M"][pool], sig, "NOCAP")
        cv, rmv = f0["cand"].to_numpy().copy(), f["rm"].to_numpy()
        sv = np.array([PV.G["sec"].get(c, "") for c in close.columns])
        for i in range(len(cv)):
            idx = np.flatnonzero(cv[i] & ~np.isnan(rmv[i]))
            if len(idx) <= 2:
                continue
            cnt = {}
            for j in idx[np.argsort(-rmv[i, idx])]:
                cnt[sv[j]] = cnt.get(sv[j], 0) + 1
                if cnt[sv[j]] > 2:
                    cv[i, j] = False
        f["cand"] = pd.DataFrame(cv, index=close.index, columns=close.columns)
    lo = f["start"]
    wm = "erc" if sz.startswith("ERC") else "invvol"
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost, weight_mode=wm)
    r0 = E.simulate(close, f, cfg)
    if sz == "NOVT":
        d = pack(r0, lo)
    else:
        v = int(sz.split("VT")[1].split("_")[0]) if sz.startswith(("VT", "ERC")) else 15
        f2 = dict(f); f2["base_ret"] = r0["ret"]
        d = pack(E.simulate(close, f2, replace(cfg, rule=E.VolTarget(v / 100, 60, 40, every=10))), lo)
        if sz == "DDC_VT15":
            d = K.apply_mult(d, ddc_mult(d["ret"], lo), cost)
        elif sz in ("BRD_VT15", "TSS_VT15"):
            mb, mt = pool_mults(close, feats(close, G["M"][pool], sig, cap))
            d = K.apply_mult(d, mb if sz == "BRD_VT15" else mt, cost)
    rv, sg = roll_vec(d["ret"], lo)
    return dict(combo=k, seed=seed, roll_med=float(np.nanmedian(rv)), full=float(PV.calmar(d["ret"][PV.starts()[0]:])))


# ------------------------------------------------------------------ 过拟合校正
def dsr(sr_best, sr_all, T, skew, kurt):
    """Bailey & López de Prado 2014。SR 为日频（非年化）。"""
    N = len(sr_all)
    v = np.var(sr_all, ddof=1)
    g = 0.5772156649
    sr0 = math.sqrt(v) * ((1 - g) * norm.ppf(1 - 1 / N) + g * norm.ppf(1 - 1 / (N * math.e)))
    z = (sr_best - sr0) * math.sqrt(T - 1) / math.sqrt(1 - skew * sr_best + (kurt - 1) / 4 * sr_best ** 2)
    return float(norm.cdf(z)), float(sr0)


def pbo_cscv(Mx, S=16, metric="sharpe"):
    """Mx: T×N 日收益。CSCV：S 块，全部 C(S, S/2) 组合；IS 最优在 OOS 的相对排名 ω，PBO = P(logit ω ≤ 0)。"""
    T, N = Mx.shape
    edges = np.linspace(0, T, S + 1).astype(int)
    blocks = [Mx[edges[i]:edges[i + 1]] for i in range(S)]
    s1 = np.array([b.sum(0) for b in blocks]); s2 = np.array([(b ** 2).sum(0) for b in blocks]); n = np.array([len(b) for b in blocks])
    lam = []
    combs = list(itertools.combinations(range(S), S // 2))
    for c in combs:
        isb = np.zeros(S, bool); isb[list(c)] = True
        def sh(m):
            nn = n[m].sum(); mu = s1[m].sum(0) / nn; var = s2[m].sum(0) / nn - mu ** 2
            return mu / np.sqrt(np.maximum(var, 1e-18))
        a, b = sh(isb), sh(~isb)
        j = int(np.argmax(a))
        rank = (b < b[j]).sum() + 0.5 * ((b == b[j]).sum() - 1) + 1
        w = rank / (N + 1)
        lam.append(math.log(w / (1 - w)))
    lam = np.array(lam)
    return float(np.mean(lam <= 0)), lam


# ------------------------------------------------------------------ 主程序
def main(mode):
    RUN.mkdir(parents=True, exist_ok=True); CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    setup_parent()
    if mode == "check":
        out = run_cell("BASE", "S2", "CAP2", 0.0005)
        ref = K.sat_run(PV.SDATE, 0.0005, sector=True)
        print("S4 复现 max abs diff:", float(np.max(np.abs(out["VT15"]["ret"] - ref["ret"]))), "lo", out["VT15"]["lo"], ref["lo"])
        for s in SIGS:
            f = feats(PV.G["C"][sorted({c for v in G["lists"]["BASE"].values() for c in v})], G["M"]["BASE"], s, "CAP2")
            print(s, "start", PV.G["cal"][f["start"]].date(), "avg cand/day", round(float(f["cand"].sum(axis=1)[f["start"]:].mean()), 2))
        print("t", round(time.time() - t0))
        return
    if mode == "grid":
        tasks = [(p, s, c, cost) for cost in COSTS for p in POOLS for s in SIGS for c in CAPS]
        with Pool(8) as pl:
            for k, r in enumerate(pl.imap_unordered(task_cell, tasks)):
                print("cell", k, r, round(time.time() - t0), flush=True)
        for cost in COSTS:
            bp = int(round(cost * 1e4))
            d = broad_vt(cost)
            core = K.core_run(PV.G["lo0"], cost, mult=K.erp_mult()[0])
            np.savez_compressed(CACHE / f"ref_{bp}.npz", broad=d["ret"], broad_lo=np.array([d["lo"]]), core=core["ret"])
        return
    if mode == "post":
        post(t0)
        return
    if mode == "null":
        top = json.load(open(RUN / "top3.json"))["top3"]
        tasks = [(k, s, 0.0005) for k in top for s in range(40000, 40000 + N_NULL)]
        rows = []
        with Pool(8) as pl:
            for i, r in enumerate(pl.imap_unordered(task_null, tasks)):
                rows.append(r)
                if i % 50 == 0:
                    print("null", i, len(tasks), round(time.time() - t0), flush=True)
        pd.DataFrame(rows).to_csv(RUN / "null_long.csv", index=False)
        return


def post(t0):
    cal = PV.G["cal"]
    s0 = PV.starts()[0]
    allrows, ev = [], {}
    for cost in COSTS:
        bp = int(round(cost * 1e4))
        R = load_cells(bp)
        ref = np.load(CACHE / f"ref_{bp}.npz")
        broad = ref["broad"]; blo = int(ref["broad_lo"][0])
        core = dict(ret=ref["core"], expo=np.ones(len(ref["core"])), lo=PV.G["lo0"])
        rS4 = roll_vec(R[key(*S4_KEY)]["ret"], R[key(*S4_KEY)]["lo"])
        rB = roll_vec(broad, blo)
        for k, d in R.items():
            rv = roll_vec(d["ret"], d["lo"])
            ev[(k, bp)] = rv
            m = E.perf(d["ret"][s0:])
            a = cmp(rv, rS4); b = cmp(rv, rB)
            p, s, c, z = k.split("|")
            allrows.append(dict(combo=k, pool=p, sig=s, cap=c, size=z, cost_bp=bp, cagr=m["cagr"], mdd=m["mdd"], vol=m["vol"],
                                sharpe=m["sharpe"], calmar=m["calmar"], turn=float(np.mean(d["turn"][s0:]) * 252),
                                expo=float(np.mean(d["expo"][s0:])), sat_lo=str(cal[d["lo"]].date()),
                                roll_med=float(np.nanmedian(rv[0])),
                                s4_share=a[0], s4_med=a[1], s4_seg=a[2], s4_pass=a[3],
                                br_share=b[0], br_med=b[1], br_seg=b[2], br_pass=b[3],
                                **{f"seg_{g}": rv[1][i] for i, g in enumerate(PV.SEGS)}))
        print("post", bp, round(time.time() - t0), flush=True)
    T = pd.DataFrame(allrows)
    T["pass_both"] = T.s4_pass & T.br_pass
    w = T.pivot_table(index="combo", columns="cost_bp", values="pass_both")
    T["robust"] = T.combo.map((w[5] & w[20]).to_dict())
    T.to_csv(RUN / "grid_results.csv", index=False)
    # 主效应（ANOVA 式）
    me_rows, ss_rows = [], []
    for bp in (5, 20):
        X = T[T.cost_bp == bp]
        for met in ("calmar", "roll_med", "s4_med", "sharpe", "mdd", "cagr"):
            y = X[met]; gm = y.mean(); sst = ((y - gm) ** 2).sum()
            ssr = {}
            for fac in ("pool", "sig", "cap", "size"):
                g = X.groupby(fac)[met]
                for lv, mu in g.mean().items():
                    me_rows.append(dict(cost_bp=bp, metric=met, factor=fac, level=lv, mean=mu, delta_vs_grand=mu - gm,
                                        best_share=float((X.loc[X[fac] == lv, met] >= X[met].quantile(0.9)).mean())))
                ssr[fac] = float((g.count() * (g.mean() - gm) ** 2).sum())
            ss_rows.append(dict(cost_bp=bp, metric=met, **{f"ss_{f}": v / sst for f, v in ssr.items()},
                                ss_interact_resid=1 - sum(ssr.values()) / sst))
    pd.DataFrame(me_rows).to_csv(RUN / "main_effects.csv", index=False)
    pd.DataFrame(ss_rows).to_csv(RUN / "anova_ss_share.csv", index=False)
    # 过拟合校正（5bp）
    R = load_cells(5)
    keys = list(R)
    Mx = np.column_stack([R[k]["ret"][s0:] for k in keys])
    srd = Mx.mean(0) / Mx.std(0, ddof=1)
    j = int(np.argmax(srd))
    rb = Mx[:, j]
    dsr_v, sr0 = dsr(srd[j], srd, len(rb), float(skew(rb)), float(kurtosis(rb, fisher=False)))
    jS4 = keys.index(key(*S4_KEY)); rs = Mx[:, jS4]
    dsr_s4, _ = dsr(srd[jS4], srd, len(rs), float(skew(rs)), float(kurtosis(rs, fisher=False)))
    pbo, lam = pbo_cscv(Mx, 16)
    # Calmar 版 PBO（IS 选卡玛最优）
    ov = dict(n_trials=len(keys), best_sharpe_combo=keys[j], best_sharpe_ann=float(srd[j] * np.sqrt(252)),
              sr0_ann=float(sr0 * np.sqrt(252)), dsr_best=dsr_v, s4_sharpe_ann=float(srd[jS4] * np.sqrt(252)), dsr_s4=dsr_s4,
              pbo_sharpe_S16=pbo, lambda_median=float(np.median(lam)), n_splits=len(lam))
    json.dump(ov, open(RUN / "overfit.json", "w"), ensure_ascii=False, indent=1)
    # 前 3（5bp：按对 S4 中位 Δ 排序，同分按滚动中位卡玛）
    X = T[(T.cost_bp == 5) & (T.combo != key(*S4_KEY))].sort_values(["s4_med", "roll_med"], ascending=False)
    top3 = list(X.combo.iloc[:3])
    json.dump(dict(top3=top3), open(RUN / "top3.json", "w"), ensure_ascii=False)
    # 卫星：C3 60% + 组合 40%，对 C3+S4、C3+宽基VT15
    srows = []
    for cost in COSTS:
        bp = int(round(cost * 1e4))
        R = load_cells(bp); ref = np.load(CACHE / f"ref_{bp}.npz")
        core = dict(ret=ref["core"], expo=np.ones(len(ref["core"])), lo=PV.G["lo0"])
        cS4 = roll_vec(R[key(*S4_KEY)]["ret"], R[key(*S4_KEY)]["lo"], "combo", core, cost)
        cB = roll_vec(ref["broad"], int(ref["broad_lo"][0]), "combo", core, cost)
        for k in top3:
            cv = roll_vec(R[k]["ret"], R[k]["lo"], "combo", core, cost)
            a, b = cmp(cv, cS4), cmp(cv, cB)
            mix = RC.mix(core, dict(ret=R[k]["ret"], expo=R[k]["expo"], lo=R[k]["lo"]), PV.WC, s0, cost)["ret"]
            m = E.perf(mix[s0:])
            srows.append(dict(combo=k, cost_bp=bp, cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"], roll_med=float(np.nanmedian(cv[0])),
                              vsS4_share=a[0], vsS4_med=a[1], vsS4_seg=a[2], vsS4_pass=a[3],
                              vsBR_share=b[0], vsBR_med=b[1], vsBR_seg=b[2], vsBR_pass=b[3]))
        for nm, (ret, lo) in {"C3+S4": (R[key(*S4_KEY)]["ret"], R[key(*S4_KEY)]["lo"]), "C3+宽基VT15": (ref["broad"], int(ref["broad_lo"][0]))}.items():
            mix = RC.mix(core, dict(ret=ret, expo=np.ones(len(ret)), lo=lo), PV.WC, s0, cost)["ret"]
            m = E.perf(mix[s0:])
            cv = cS4 if nm == "C3+S4" else cB
            srows.append(dict(combo=nm, cost_bp=bp, cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"], roll_med=float(np.nanmedian(cv[0]))))
    pd.DataFrame(srows).to_csv(RUN / "satellite_top3.csv", index=False)
    print("post done", round(time.time() - t0))


if __name__ == "__main__":
    main(sys.argv[1])
