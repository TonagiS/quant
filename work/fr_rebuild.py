#!/usr/bin/env python3
"""full_rebuild（2026-11-21）：从零重搭，把各层候选放进一个全因子网格（4×3×3×4×2×4×4 = 2304 个配置 × 5/20bp）。
规则见 runs/2026-11-21_full_rebuild/FREEZE.md。复用 c3x_combo（信号/评估）、fw_layers（状态/离场/合成）、fw_engine（回测循环）。样本内。
用法：python3 work/fr_rebuild.py check | sleeves | assemble | post"""
from __future__ import annotations

import itertools
import json
import sys
import time
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c3x_combo as X  # noqa: E402
import fw_layers as F  # noqa: E402
from fw_engine import simulate_x  # noqa: E402

PV, K, RC, E = X.PV, X.K, X.RC, X.E
RUN = PV.ROOT / "runs" / "2026-11-21_full_rebuild"
CACHE = PV.ROOT / "data" / "cache_full_rebuild"
COSTS = (0.0005, 0.0020)
FACT = {
    "L0": ["NONE", "TREND_SWITCH", "TREND_BEAR30", "BREADTH_BEAR30"],
    "L1": ["BASE", "P12_HYST60", "P10_LOWCORR"],
    "L2": ["NONE", "T120", "W30"],
    "L3": ["S2", "RAM", "DONCH", "TSM"],
    "CAP": ["CAP2", "NOCAP"],
    "L4": ["VT10", "VT15", "ERC_VT15", "BRD_VT15"],
    "L5": ["NONE", "HYST10", "TS12", "TIME30"],
}
NAMES = list(FACT)
S4 = ("NONE", "BASE", "NONE", "S2", "CAP2", "VT15", "NONE")
CHAMP = ("NONE", "P12_HYST60", "NONE", "DONCH", "CAP2", "VT10", "NONE")
SPLIT = {"A": (("2014-06-01", "2019-12-31"), ("2020-01-01", "2026-11-13")),   # A：前段选、后段测
         "B": (("2020-01-01", "2026-11-13"), ("2014-06-01", "2019-12-31"))}   # B：反向
MIN_YEARS = 3
G = {}


def key(c):
    return "|".join(c)


def configs():
    return list(itertools.product(*FACT.values()))


# ------------------------------------------------------------------ 袖子（L1 池 × L2 大趋势 × L3 信号 × 板块 × L5 离场）
def setup():
    X.setup_parent()
    F.G["close"], F.G["M"], F.G["sv"] = {}, {}, {}
    for p in FACT["L1"]:
        ever = sorted({c for v in X.G["lists"][p].values() for c in v})
        F.G["close"][p] = PV.G["C"][ever]
        F.G["M"][p] = X.G["M"][p]
        F.G["sv"][p] = np.array([PV.G["sec"].get(c, "") for c in ever])


def feats_c(pool, sig, big, cap):
    close, M = F.G["close"][pool], F.G["M"][pool]
    f = X.feats(close, M, sig, "NOCAP")
    cand = f["cand"]
    if big == "W30":
        cand = cand & F.weekly_above(close, 30)
    elif big == "T120":
        ma120 = close.rolling(120, min_periods=120).mean()
        cand = cand & ((close / close.shift(120) - 1 > 0) & (ma120 > ma120.shift(20))).fillna(False)
    f["cand"] = F.cap2(cand, f["rm"], F.G["sv"][pool]) if cap == "CAP2" else cand
    return f


def pk(rr, lo):
    return dict(ret=np.asarray(rr["ret"], float), turn=np.asarray(rr["turn"], float), expo=np.asarray(rr["expo"], float), lo=lo)


def task_sleeve(args):
    pool, big, sig, cap, ex, cost = args
    close = F.G["close"][pool]
    f = feats_c(pool, sig, big, cap)
    lo = f["start"]
    exs = F.EXITS.get(ex)
    out = {}
    for wm in ("invvol", "erc"):
        cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost, weight_mode=wm, buffer_n=10 if ex == "HYST10" else None)
        r0 = simulate_x(close, f, cfg, exit_spec=exs)
        f2 = dict(f); f2["base_ret"] = r0["ret"]
        for v in ((10, 15) if wm == "invvol" else (15,)):
            rr = simulate_x(close, f2, replace(cfg, rule=E.VolTarget(v / 100, 60, 40, every=10)), exit_spec=exs)
            out[("ERC_" if wm == "erc" else "") + f"VT{v}"] = pk(rr, lo)
    m_brd, _ = X.pool_mults(close, f)
    out["BRD_VT15"] = K.apply_mult(out["VT15"], m_brd, cost)
    bp = int(round(cost * 1e4))
    np.savez_compressed(CACHE / f"sl_{pool}_{big}_{sig}_{cap}_{ex}_{bp}.npz",
                        **{f"{a}__{z}": out[z][a] for z in FACT["L4"] for a in ("ret", "turn", "expo")}, lo=np.array([lo]))
    return args


def load_sleeve(pool, big, sig, cap, ex, bp, z):
    zf = np.load(CACHE / f"sl_{pool}_{big}_{sig}_{cap}_{ex}_{bp}.npz")
    return dict(ret=zf[f"ret__{z}"], turn=zf[f"turn__{z}"], expo=zf[f"expo__{z}"], lo=int(zf["lo"][0]))


# ------------------------------------------------------------------ 合成 L0 与 L4
def assemble(c, bp):
    l0, pool, big, sig, cap, l4, ex = c
    cost = bp / 1e4
    d = load_sleeve(pool, big, sig, cap, ex, bp, l4)
    if l0 == "NONE":
        return d
    if l0 == "TREND_SWITCH":    # 牛：配置本身；震荡：同池/同大趋势/同板块/同离场的 RAM+VT10；熊：震荡袖子 × 0.3
        reg = G["reg"][(pool, "RG_TREND")]
        rng = load_sleeve(pool, big, "RAM", cap, ex, bp, "VT10")
        sl = {"bull": d, "range": rng}
        sel = np.where(reg == 0, 0, 1); m = np.where(reg == 2, 0.3, 1.0)
    else:
        reg = G["reg"][(pool, "RG_TREND" if l0 == "TREND_BEAR30" else "RG_BREADTH")]
        sl = {"base": d}
        sel = np.zeros(len(reg), int); m = np.where(reg == 2, 0.3, 1.0)
    lo = max(s["lo"] for s in sl.values())
    return F.combine(sl, sel, m, cost, lo)


# ------------------------------------------------------------------ 评估
def seg_idx(a, b):
    cal = PV.G["cal"]
    return int(cal.searchsorted(pd.Timestamp(a))), int(cal.searchsorted(pd.Timestamp(b), side="right"))


def seg_starts(a, b):
    """段内滚动起点：每月第一个交易日，窗口到段末，至少 3 年。"""
    cal = PV.G["cal"]
    lo, hi = seg_idx(a, b)
    last = pd.Timestamp(b) - pd.DateOffset(years=MIN_YEARS)
    s = pd.Series(np.arange(lo, hi), index=cal[lo:hi])
    s = s[s.index <= last]
    return s.groupby(s.index.to_period("M")).min().to_list(), hi


def seg_metrics(ret, lo_sat, a, b):
    st, hi = seg_starts(a, b)
    l0, _ = seg_idx(a, b)
    v = [PV.calmar(ret[s:hi]) if lo_sat <= s else np.nan for s in st]
    full = PV.calmar(ret[l0:hi]) if lo_sat <= l0 else np.nan
    m = E.perf(ret[l0:hi])
    return float(np.nanmedian(v)) if np.isfinite(v).any() else np.nan, full, m["cagr"], m["mdd"]


def task_eval(args):
    c, bp = args
    d = assemble(c, bp)
    rv = X.roll_vec(d["ret"], d["lo"])
    s0 = PV.starts()[0]
    m = E.perf(d["ret"][s0:])
    row = dict(zip(NAMES, c), key=key(c), cost_bp=bp, cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"], sharpe=m["sharpe"],
               turn=float(np.mean(d["turn"][s0:]) * 252), expo=float(np.mean(d["expo"][s0:])), roll_med=float(np.nanmedian(rv[0])),
               **{f"seg_{g}": rv[1][i] for i, g in enumerate(PV.SEGS)})
    for nm, (a, b) in (("P1", SPLIT["A"][0]), ("P2", SPLIT["A"][1])):
        rm, fc, cg, md = seg_metrics(d["ret"], d["lo"], a, b)
        row.update({f"{nm}_roll_med": rm, f"{nm}_calmar": fc, f"{nm}_cagr": cg, f"{nm}_mdd": md})
    rS4 = G["rS4"][bp]
    a = X.cmp(rv, rS4)
    row.update(s4_share=a[0], s4_med=a[1], s4_seg=a[2], s4_pass=a[3])
    np.save(CACHE / f"ret_{bp}_{key(c).replace('|', '__')}.npy", d["ret"].astype(np.float32))
    return row


def init_worker():
    pass


# ------------------------------------------------------------------ 主程序
def prep():
    G["reg"] = {(p, rg): F.regime(p, rg) for p in FACT["L1"] for rg in ("RG_TREND", "RG_BREADTH")}
    G["rS4"] = {}
    for bp in (5, 20):
        z = np.load(X.CACHE / f"cell_BASE_S2_CAP2_{bp}.npz")
        G["rS4"][bp] = X.roll_vec(z["VT15"], int(z["lo"][0]))


def main(mode):
    RUN.mkdir(parents=True, exist_ok=True); CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    setup()
    if mode == "check":
        for pool, sig, v in (("BASE", "S2", "VT15"), ("P12_HYST60", "DONCH", "VT10")):
            task_sleeve((pool, "NONE", sig, "CAP2", "NONE", 0.0005))
            d = load_sleeve(pool, "NONE", sig, "CAP2", "NONE", 5, v)
            z = np.load(X.CACHE / f"cell_{pool}_{sig}_CAP2_5.npz")
            print(pool, sig, v, "与 combo3 缓存最大差", float(np.max(np.abs(d["ret"] - z[v]))))
            zb = z["BRD_VT15"]; db = load_sleeve(pool, "NONE", sig, "CAP2", "NONE", 5, "BRD_VT15")
            print("  BRD_VT15 最大差", float(np.max(np.abs(db["ret"] - zb))))
        prep()
        task_sleeve(("P12_HYST60", "NONE", "RAM", "CAP2", "NONE", 0.0005))
        d = assemble(("TREND_SWITCH",) + CHAMP[1:3] + ("DONCH", "CAP2", "VT15", "NONE"), 5)
        fl = F.build("B1", "RG_TREND|SWITCH", None, None, 0.0005)
        print("TREND_SWITCH(P12,DONCH,VT15) 与 framework_layers B1 SWITCH 最大差", float(np.max(np.abs(d["ret"] - fl["ret"]))))
        for a, b in list(SPLIT["A"]):
            st, hi = seg_starts(a, b); print(a, b, "起点数", len(st), PV.G["cal"][st[0]].date(), PV.G["cal"][st[-1]].date())
        print("t", round(time.time() - t0))
        return
    if mode == "sleeves":
        tasks = [(p, bg, s, cp, ex, cost) for cost in COSTS for p in FACT["L1"] for bg in FACT["L2"] for s in FACT["L3"]
                 for cp in FACT["CAP"] for ex in FACT["L5"]]
        with Pool(8) as pl:
            for k, r in enumerate(pl.imap_unordered(task_sleeve, tasks)):
                if k % 24 == 0:
                    print("sleeve", k, len(tasks), round(time.time() - t0), flush=True)
        return
    if mode == "assemble":
        prep()
        tasks = [(c, bp) for bp in (5, 20) for c in configs()]
        rows = []
        with Pool(8) as pl:
            for k, r in enumerate(pl.imap_unordered(task_eval, tasks, chunksize=8)):
                rows.append(r)
                if k % 500 == 0:
                    print("eval", k, len(tasks), round(time.time() - t0), flush=True)
        pd.DataFrame(rows).to_csv(RUN / "grid_results.csv.gz", index=False)
        return
    if mode == "post":
        post(t0)


# ------------------------------------------------------------------ 分析
def anova(X_, met):
    y = X_[met].to_numpy(); gm = y.mean(); sst = ((y - gm) ** 2).sum()
    out = {}
    for f in NAMES:
        g = X_.groupby(f)[met]
        out[f] = float((g.count() * (g.mean() - gm) ** 2).sum()) / sst
    for a, b in itertools.combinations(NAMES, 2):
        g = X_.groupby([a, b])[met]
        ss_ab = float((g.count() * (g.mean() - gm) ** 2).sum()) / sst
        out[f"{a}×{b}"] = ss_ab - out[a] - out[b]
    out["残差(3阶及以上)"] = 1 - sum(out.values())
    return out


def post(t0):
    prep()
    T = pd.read_csv(RUN / "grid_results.csv.gz")
    res = {}
    # (1) 时间切分选择
    sel_rows = []
    T20 = T[T.cost_bp == 20].set_index("key"); T5 = T[T.cost_bp == 5].set_index("key")
    for sp, (isn, oos) in (("A", ("P1", "P2")), ("B", ("P2", "P1"))):
        for rule, col in (("roll_med", f"{isn}_roll_med"), ("calmar", f"{isn}_calmar")):
            r = T20[col].sort_values(ascending=False)
            top = list(r.index[:10]); win = top[0]
            for bp, TT in ((20, T20), (5, T5)):
                oc = TT[f"{oos}_roll_med"].rank(ascending=False, method="min"); of = TT[f"{oos}_calmar"].rank(ascending=False, method="min")
                for nm, ks in (("第1名", [win]), ("前10平均", top)):
                    sel_rows.append(dict(split=sp, rule=rule, who=nm, cost_bp=bp, configs=";".join(ks),
                                         IS_val=float(T20.loc[ks, col].mean()),
                                         OOS_roll_med=float(TT.loc[ks, f"{oos}_roll_med"].mean()), OOS_calmar=float(TT.loc[ks, f"{oos}_calmar"].mean()),
                                         OOS_cagr=float(TT.loc[ks, f"{oos}_cagr"].mean()), OOS_mdd=float(TT.loc[ks, f"{oos}_mdd"].mean()),
                                         OOS_rank_roll=float(oc.loc[ks].mean()), OOS_rank_calmar=float(of.loc[ks].mean()),
                                         S4_OOS_roll=float(TT.loc[key(S4), f"{oos}_roll_med"]), S4_OOS_calmar=float(TT.loc[key(S4), f"{oos}_calmar"]),
                                         CH_OOS_roll=float(TT.loc[key(CHAMP), f"{oos}_roll_med"]), CH_OOS_calmar=float(TT.loc[key(CHAMP), f"{oos}_calmar"]),
                                         CH_OOS_rank_roll=float(oc.loc[key(CHAMP)]), S4_OOS_rank_roll=float(oc.loc[key(S4)])))
    # 前 10 等权组合（日收益平均）的 OOS
    for row in sel_rows:
        if row["who"] != "前10平均":
            continue
        ks = row["configs"].split(";")
        R = np.mean([np.load(CACHE / f"ret_{row['cost_bp']}_{k.replace('|', '__')}.npy").astype(float) for k in ks], axis=0)
        a, b = SPLIT[row["split"]][1]
        rm, fc, cg, md = seg_metrics(R, PV.G["lo0"], a, b)
        row.update(EW_OOS_roll_med=rm, EW_OOS_calmar=fc, EW_OOS_cagr=cg, EW_OOS_mdd=md)
    # 宽基 VT15 的 OOS
    for row in sel_rows:
        ref = np.load(X.CACHE / f"ref_{row['cost_bp']}.npz")
        a, b = SPLIT[row["split"]][1]
        rm, fc, _, _ = seg_metrics(ref["broad"], int(ref["broad_lo"][0]), a, b)
        row.update(BR_OOS_roll=rm, BR_OOS_calmar=fc)
    pd.DataFrame(sel_rows).to_csv(RUN / "oos_selection.csv", index=False)
    # (2) 主效应与交互
    an, mm = [], []
    for bp in (5, 20):
        Xb = T[T.cost_bp == bp]
        for met in ("calmar", "roll_med", "s4_med", "mdd", "cagr", "P1_calmar", "P2_calmar"):
            an.append(dict(cost_bp=bp, metric=met, **anova(Xb, met)))
            gm = Xb[met].mean()
            for f in NAMES:
                for lv, g in Xb.groupby(f)[met]:
                    mm.append(dict(cost_bp=bp, metric=met, factor=f, level=lv, mean=g.mean(), delta=g.mean() - gm,
                                   top10pct_share=float((g >= Xb[met].quantile(0.9)).mean())))
    pd.DataFrame(an).to_csv(RUN / "anova.csv", index=False)
    pd.DataFrame(mm).to_csv(RUN / "marginal_means.csv", index=False)
    # (3) PBO / DSR（5bp 与 20bp）
    s0 = PV.starts()[0]
    ks = list(T5.index)
    for bp in (5, 20):
        Mx = np.column_stack([np.load(CACHE / f"ret_{bp}_{k.replace('|', '__')}.npy").astype(float)[s0:] for k in ks])
        srd = Mx.mean(0) / Mx.std(0, ddof=1)
        pbo, lam = X.pbo_cscv(Mx, 16)
        j = int(np.argmax(srd)); r = Mx[:, j]
        dv, sr0 = X.dsr(srd[j], srd, len(r), X.skew(r), X.kurtosis(r))
        o = dict(pbo=pbo, lambda_median=float(np.median(lam)), best=ks[j], best_sharpe_ann=float(srd[j] * np.sqrt(252)),
                 sr0_ann=float(sr0 * np.sqrt(252)), dsr_best=dv)
        for nm, c in (("S4", S4), ("CHAMP", CHAMP)):
            jj = ks.index(key(c)); rr = Mx[:, jj]
            o[f"dsr_{nm}"] = X.dsr(srd[jj], srd, len(rr), X.skew(rr), X.kurtosis(rr))[0]
            o[f"sharpe_{nm}"] = float(srd[jj] * np.sqrt(252))
        res[f"overfit_{bp}"] = o
    # (4) 稳健集
    w = T.pivot_table(index="key", columns="cost_bp", values="s4_pass", aggfunc="first")
    rob = list(w.index[(w[5].astype(bool)) & (w[20].astype(bool))])
    res["robust_n"] = len(rob)
    feat = []
    R5 = T5.loc[rob] if rob else T5.iloc[:0]
    for f in NAMES:
        for lv in FACT[f]:
            feat.append(dict(factor=f, level=lv, grid_share=float((T5[f] == lv).mean()),
                             robust_share=float((R5[f] == lv).mean()) if len(R5) else np.nan, robust_n=int((R5[f] == lv).sum())))
    pd.DataFrame(feat).to_csv(RUN / "robust_features.csv", index=False)
    T5.loc[rob].reset_index().to_csv(RUN / "robust_set_5bp.csv", index=False)
    # (5) 卫星：20bp 全期滚动中位卡玛前 3 + 两个方向的 IS 第 1 名（roll_med 规则）+ 冠军
    top3 = list(T20["roll_med"].sort_values(ascending=False).index[:3])
    sa = pd.DataFrame(sel_rows)
    wins = list(sa[(sa.rule == "roll_med") & (sa.who == "第1名") & (sa.cost_bp == 20)].configs)
    picks = list(dict.fromkeys(top3 + wins + [key(CHAMP), key(S4)]))
    res["sat_picks"] = picks
    srows = []
    for cost in COSTS:
        bp = int(round(cost * 1e4))
        ref = np.load(X.CACHE / f"ref_{bp}.npz")
        core = dict(ret=ref["core"], expo=np.ones(len(ref["core"])), lo=PV.G["lo0"])
        bro = dict(ret=ref["broad"], expo=np.ones(len(ref["broad"])), lo=int(ref["broad_lo"][0]))
        def cv(d):
            return X.roll_vec(d["ret"], d["lo"], "combo", core, cost)
        dS4 = assemble(S4, bp); dCH = assemble(CHAMP, bp)
        cS4, cCH, cB = cv(dS4), cv(dCH), cv(bro)
        for k in picks + ["宽基VT15"]:
            d = bro if k == "宽基VT15" else assemble(tuple(k.split("|")), bp)
            mix = RC.mix(core, dict(ret=d["ret"], expo=d["expo"], lo=d["lo"]), PV.WC, s0, cost)["ret"]
            m = E.perf(mix[s0:]); c_ = cv(d)
            a1, a2, a3 = X.cmp(c_, cS4), X.cmp(c_, cCH), X.cmp(c_, cB)
            fmt = lambda a: f"{a[0]:.3f}/{a[1]:+.3f}/{a[2]}/{a[3]}"  # noqa: E731
            srows.append(dict(sat=k, cost_bp=bp, cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"], roll_med=float(np.nanmedian(c_[0])),
                              vs_C3S4=fmt(a1), vs_C3CH=fmt(a2), vs_C3BR=fmt(a3)))
    pd.DataFrame(srows).to_csv(RUN / "satellite.csv", index=False)
    json.dump(res, open(RUN / "summary.json", "w"), ensure_ascii=False, indent=1)
    print("post done", round(time.time() - t0))


if __name__ == "__main__":
    main(sys.argv[1])
