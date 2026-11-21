#!/usr/bin/env python3
"""framework_layers（2026-11-21）：在两个底座上逐层加「市场状态 L0 / 两级趋势 L1 / 独立离场 L2」，
顺序闸门（每层先单独过判据才叠加）。规则见 runs/2026-11-21_framework_layers/FREEZE.md。样本内。
用法：python3 work/fw_layers.py check | solo | stack | null | post"""
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
from fw_engine import simulate_x  # noqa: E402

PV, K, RC, E = X.PV, X.K, X.RC, X.E
RUN = PV.ROOT / "runs" / "2026-11-21_framework_layers"
CACHE = PV.ROOT / "data" / "cache_fw"
COSTS = (0.0005, 0.0020)
BASES = {"B1": ("P12_HYST60", "DONCH", 10), "B2": ("BASE", "S2", 15)}   # B1 = combo3 最佳；B2 = S4
L0 = [f"{rg}|{mp}" for rg in ("RG_TREND", "RG_BREADTH", "RG_COMBO") for mp in ("SWITCH", "BEAR30", "BEARCASH")]
L1 = ["W30", "T120", "W30_BO", "W30_PB"]
L2 = ["TS8", "TS12", "TS15", "DCL20", "TIME30", "HYST10"]
LAYERS = {"L0": L0, "L1": L1, "L2": L2}
IDX = "sh510300"      # 沪深300 ETF 总收益价格，作为大盘指数代理
N_NULL = 100
G = {}


# ------------------------------------------------------------------ 基础
def setup():
    X.setup_parent()
    G["close"], G["M"] = {}, {}
    for p in {b[0] for b in BASES.values()}:
        ever = sorted({c for v in X.G["lists"][p].values() for c in v})
        G["close"][p] = PV.G["C"][ever]
        G["M"][p] = X.G["M"][p]
    G["sv"] = {p: np.array([PV.G["sec"].get(c, "") for c in G["close"][p].columns]) for p in G["close"]}


def cap2(cand, rm, sv):
    rmv, cv = rm.to_numpy(), cand.to_numpy().copy()
    for i in range(len(cv)):
        idx = np.flatnonzero(cv[i] & ~np.isnan(rmv[i]))
        if len(idx) <= 2:
            continue
        cnt = {}
        for j in idx[np.argsort(-rmv[i, idx])]:
            cnt[sv[j]] = cnt.get(sv[j], 0) + 1
            if cnt[sv[j]] > 2:
                cv[i, j] = False
    return pd.DataFrame(cv, index=cand.index, columns=cand.columns)


def weekly_above(close, n=30):
    """周收盘 > n 周均线；只用已收完的周（周线标签日起生效）。"""
    wk = close.resample("W-FRI").last()
    ok = (wk > wk.rolling(n, min_periods=n).mean()).astype(float).where(wk.rolling(n, min_periods=n).mean().notna())
    return ok.reindex(close.index, method="ffill").fillna(0.0) > 0.5


def feats_l(pool, sig, l1, seed=None):
    close, M = G["close"][pool], G["M"][pool]
    f = X.feats(close, M, sig, "NOCAP")
    if seed is not None:       # 随机零假设：同一候选结构内随机排序
        rng = np.random.default_rng(seed)
        f["rm"] = pd.DataFrame(rng.random(f["rm"].shape), index=close.index, columns=close.columns).where(f["rm"].notna())
    cand = f["cand"]
    if l1:
        if l1.startswith("W30"):
            big = weekly_above(close, 30)
        elif l1 == "T120":
            ma120 = close.rolling(120, min_periods=120).mean()
            big = (close / close.shift(120) - 1 > 0) & (ma120 > ma120.shift(20))
        else:
            raise KeyError(l1)
        cand = cand & big.fillna(False)
        if l1.endswith("_BO"):     # 小趋势择时：新买入须接近突破（收盘 ≥ 20 日最高收盘的 98%）
            f["entry_ok"] = (close >= 0.98 * close.rolling(20, min_periods=20).max()).fillna(False)
        elif l1.endswith("_PB"):   # 小趋势择时：上升趋势中的回调买入（收盘 ≤ MA20）
            f["entry_ok"] = (close <= close.rolling(20, min_periods=20).mean()).fillna(False)
    f["cand"] = cap2(cand, f["rm"], G["sv"][pool])
    return f


EXITS = {"TS8": ("TS", 0.08), "TS12": ("TS", 0.12), "TS15": ("TS", 0.15), "DCL20": ("DCL", 20), "TIME30": ("TIME", 30)}


def sleeve(pool, sig, vt, cost, l1=None, l2=None, seed=None):
    close = G["close"][pool]
    f = feats_l(pool, sig, l1, seed)
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost, buffer_n=10 if l2 == "HYST10" else None)
    ex = EXITS.get(l2)
    r0 = simulate_x(close, f, cfg, exit_spec=ex)
    f2 = dict(f); f2["base_ret"] = r0["ret"]
    rr = simulate_x(close, f2, replace(cfg, rule=E.VolTarget(vt / 100, 60, 40, every=10)), exit_spec=ex)
    return dict(ret=np.asarray(rr["ret"], float), turn=np.asarray(rr["turn"], float), expo=np.asarray(rr["expo"], float),
                lo=f["start"], n_exit=rr.get("n_exit", 0))


# ------------------------------------------------------------------ L0 市场状态
def regime(pool, rg):
    """0 = 牛，1 = 震荡，2 = 熊；t 日收盘的数据定。"""
    C = PV.G["C"]
    ix = C[IDX]
    close, M = G["close"][pool], G["M"][pool]
    f = X.feats(close, M, "S2", "NOCAP")
    br = (close > f["ma60"]).where(f["valid"]).mean(axis=1)
    if rg == "RG_TREND":       # 指数 vs MA200 + MA200 的 20 日斜率
        ma = ix.rolling(200, min_periods=200).mean(); up = ma > ma.shift(20)
        r = np.where((ix > ma) & up, 0, np.where((ix < ma) & ~up, 2, 1))
        r = np.where(ma.isna() | ma.shift(20).isna(), 1, r)
    elif rg == "RG_BREADTH":   # 池内收盘 > MA60 占比
        r = np.where(br > 0.6, 0, np.where(br < 0.35, 2, 1))
        r = np.where(br.isna(), 1, r)
    elif rg == "RG_COMBO":     # 指数 > MA120、宽度 > 0.5、指数 20 日波动 < 自身 250 日中位数，三项计分
        ma = ix.rolling(120, min_periods=120).mean()
        v = ix.pct_change(fill_method=None).rolling(20, min_periods=15).std()
        vm = v.rolling(250, min_periods=120).median()
        s = (ix > ma).astype(int) + (br > 0.5).astype(int) + (v < vm).astype(int)
        r = np.where(s == 3, 0, np.where(s == 2, 1, 2))
        r = np.where(ma.isna() | vm.isna(), 1, r)
    else:
        raise KeyError(rg)
    return np.asarray(r, int)


def combine(sl, sel, m, cost, lo):
    """sl: {name: sleeve}；sel/m: t 日收盘决定的选用袖子与乘数；t+1 收盘成交，t+2 起生效。
    换袖子按全卖全买计成本（保守），只改乘数按 |Δm|×敞口 计成本。"""
    names = list(sl)
    T = len(sel)
    se = np.empty(T, int); se[:2] = sel[0]; se[2:] = sel[:-2]
    me = np.ones(T); me[:2] = m[0]; me[2:] = m[:-2]
    R = np.column_stack([sl[n]["ret"] for n in names]); EX = np.column_stack([sl[n]["expo"] for n in names])
    TU = np.column_stack([sl[n]["turn"] for n in names])
    ar = np.arange(T)
    ret = me * R[ar, se]; expo = me * EX[ar, se]; turn = me * TU[ar, se]
    tc = np.zeros(T); tt = np.zeros(T)
    for t in range(1, T - 1):           # t 日决定、t+1 成交
        a, b = sel[t - 1], sel[t]
        if a != b:
            x = m[t - 1] * EX[t + 1, a] + m[t] * EX[t + 1, b]
        else:
            x = abs(m[t] - m[t - 1]) * EX[t + 1, b]
        tc[t + 1] = cost * x; tt[t + 1] = x
    return dict(ret=ret - tc, expo=expo, turn=turn + tt, lo=lo)


def build(base, l0=None, l1=None, l2=None, cost=0.0005, seed=None):
    pool, sig, vt = BASES[base]
    if not l0:
        return sleeve(pool, sig, vt, cost, l1, l2, seed)
    rg, mp = l0.split("|")
    reg = regime(pool, rg)
    if mp == "SWITCH":   # 牛：DONCH+VT15；震荡：RAM+VT10；熊：RAM+VT10 × 0.3
        sl = {"bull": sleeve(pool, "DONCH", 15, cost, l1, l2, seed), "range": sleeve(pool, "RAM", 10, cost, l1, l2, seed)}
        sel = np.where(reg == 0, 0, 1); m = np.where(reg == 2, 0.3, 1.0)
    else:                # 牛/震荡：底座原样；熊：0.3 或 0
        sl = {"base": sleeve(pool, sig, vt, cost, l1, l2, seed)}
        sel = np.zeros(len(reg), int); m = np.where(reg == 2, 0.3 if mp == "BEAR30" else 0.0, 1.0)
    lo = max(s["lo"] for s in sl.values())
    out = combine(sl, sel, m, cost, lo)
    out["regime_share"] = [float(np.mean(reg[PV.starts()[0]:] == k)) for k in range(3)]
    return out


# ------------------------------------------------------------------ 评估
def evalr(d):
    rv = X.roll_vec(d["ret"], d["lo"])
    return rv


def stats(d):
    s0 = PV.starts()[0]
    m = E.perf(d["ret"][s0:])
    return dict(cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"], sharpe=m["sharpe"],
                turn=float(np.mean(d["turn"][s0:]) * 252), expo=float(np.mean(d["expo"][s0:])))


def tag(base, l0, l1, l2):
    return f"{base}|L0={l0 or '-'}|L1={l1 or '-'}|L2={l2 or '-'}"


def task(args):
    base, l0, l1, l2, cost = args
    d = build(base, l0, l1, l2, cost)
    bp = int(round(cost * 1e4))
    np.savez_compressed(CACHE / f"{tag(base, l0, l1, l2).replace('|', '__')}_{bp}.npz", ret=d["ret"], expo=d["expo"],
                        turn=d["turn"], lo=np.array([d["lo"]]))
    return tag(base, l0, l1, l2), bp


def load(t, bp):
    z = np.load(CACHE / f"{t.replace('|', '__')}_{bp}.npz")
    return dict(ret=z["ret"], expo=z["expo"], turn=z["turn"], lo=int(z["lo"][0]))


def run_tasks(tasks, t0):
    with Pool(8) as pl:
        for k, r in enumerate(pl.imap_unordered(task, tasks)):
            print("task", k, len(tasks), r, round(time.time() - t0), flush=True)


def cmp_rows(t, ref_t, refs_extra=()):
    """对 ref_t（同成本）的判据，5bp 与 20bp。"""
    out = {}
    for bp in (5, 20):
        d = load(t, bp); rv = evalr(d)
        r0 = evalr(load(ref_t, bp))
        a = X.cmp(rv, r0)
        out.update({f"share_{bp}": a[0], f"med_{bp}": a[1], f"seg_{bp}": a[2], f"pass_{bp}": a[3],
                    **{f"{k}_{bp}": v for k, v in stats(d).items()}, f"roll_med_{bp}": float(np.nanmedian(rv[0]))})
    out["pass_both_cost"] = bool(out["pass_5"] and out["pass_20"])
    return out


def best_pass(df):
    p = df[df.pass_both_cost]
    if p.empty:
        return None
    return p.sort_values(["med_5", "roll_med_5"], ascending=False).iloc[0]


# ------------------------------------------------------------------ 主程序
def main(mode):
    RUN.mkdir(parents=True, exist_ok=True); CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    setup()
    if mode == "check":
        for b, (p, s, v) in BASES.items():
            d = build(b, cost=0.0005)
            z = np.load(X.CACHE / f"cell_{p}_{s}_CAP2_5.npz")
            print(b, "与 combo3 缓存最大差", float(np.max(np.abs(d["ret"] - z[f"VT{v}"]))), "lo", d["lo"])
        for l0 in L0:
            d = build("B2", l0=l0, cost=0.0005)
            print(l0, "regime share 牛/震/熊", np.round(d["regime_share"], 3), round(stats(d)["calmar"], 3))
        for l1 in L1:
            print(l1, round(stats(build("B2", l1=l1))["calmar"], 3))
        for l2 in L2:
            d = build("B2", l2=l2); print(l2, round(stats(d)["calmar"], 3))
        print("t", round(time.time() - t0))
        return
    if mode == "solo":
        tasks = [(b, None, None, None, c) for b in BASES for c in COSTS]
        tasks += [(b, l0, None, None, c) for b in BASES for l0 in L0 for c in COSTS]
        tasks += [(b, None, l1, None, c) for b in BASES for l1 in L1 for c in COSTS]
        tasks += [(b, None, None, l2, c) for b in BASES for l2 in L2 for c in COSTS]
        run_tasks(tasks, t0)
        rows = []
        for b in BASES:
            for L, vs in LAYERS.items():
                for v in vs:
                    kw = {"L0": (v, None, None), "L1": (None, v, None), "L2": (None, None, v)}[L]
                    rows.append(dict(base=b, layer=L, variant=v, **cmp_rows(tag(b, *kw), tag(b, None, None, None))))
        S = pd.DataFrame(rows); S.to_csv(RUN / "solo.csv", index=False)
        print(S[["base", "layer", "variant", "share_5", "med_5", "seg_5", "share_20", "med_20", "seg_20", "pass_both_cost"]].round(3).to_string())
        return
    if mode == "stack":
        S = pd.read_csv(RUN / "solo.csv")
        log = []
        for b in BASES:
            cur = {"L0": None, "L1": None, "L2": None}
            for L in ("L0", "L1", "L2"):
                bp_ = best_pass(S[(S.base == b) & (S.layer == L)])
                if bp_ is None:
                    log.append(dict(base=b, layer=L, variant=None, status="单独未通过，不叠加")); continue
                ref = tag(b, cur["L0"], cur["L1"], cur["L2"])
                nxt = dict(cur); nxt[L] = bp_["variant"]
                t = tag(b, nxt["L0"], nxt["L1"], nxt["L2"])
                if not (CACHE / f"{t.replace('|', '__')}_5.npz").exists():
                    run_tasks([(b, nxt["L0"], nxt["L1"], nxt["L2"], c) for c in COSTS], t0)
                r = cmp_rows(t, ref)
                ok = r["pass_both_cost"]
                log.append(dict(base=b, layer=L, variant=bp_["variant"], ref=ref, combo=t,
                                status="叠加后仍通过，保留" if ok else "叠加后未通过，丢弃", **r))
                if ok:
                    cur = nxt
            log.append(dict(base=b, layer="FINAL", combo=tag(b, cur["L0"], cur["L1"], cur["L2"])))
        pd.DataFrame(log).to_csv(RUN / "stack.csv", index=False)
        print(pd.DataFrame(log)[["base", "layer", "variant", "status", "combo"]].to_string())
        return
    if mode == "null":
        st = pd.read_csv(RUN / "stack.csv")
        fin = st[st.layer == "FINAL"].combo.tolist()
        tasks = []
        for t in fin:
            b, l0, l1, l2 = parse(t)
            if (l0, l1, l2) == (None, None, None):
                continue
            tasks += [(t, s) for s in range(50000, 50000 + N_NULL)]
        rows = []
        with Pool(8) as pl:
            for i, r in enumerate(pl.imap_unordered(task_null, tasks)):
                rows.append(r)
                if i % 50 == 0:
                    print("null", i, len(tasks), round(time.time() - t0), flush=True)
        if rows:
            pd.DataFrame(rows).to_csv(RUN / "null_long.csv", index=False)
        else:
            print("最终框架都等于底座，没有可做零假设的层")
        return
    if mode == "explore":   # 冻结后追加（非闸门）：未通过但改善最大的几个层，在随机底座上是否仍有改善
        ts = [tag("B2", "RG_TREND|SWITCH", None, None), tag("B2", "RG_TREND|BEARCASH", None, None),
              tag("B2", None, "W30_BO", None), tag("B1", "RG_TREND|BEAR30", None, None)]
        tasks = [(t, s) for t in ts for s in range(50000, 50000 + N_NULL)]
        rows = []
        with Pool(8) as pl:
            for i, r in enumerate(pl.imap_unordered(task_null, tasks)):
                rows.append(r)
                if i % 100 == 0:
                    print("explore", i, len(tasks), round(time.time() - t0), flush=True)
        pd.DataFrame(rows).to_csv(RUN / "explore_null_long.csv", index=False)
        return
    if mode == "post":
        post(t0)


def parse(t):
    p = t.split("|")
    g = lambda s: None if s.split("=")[1] == "-" else s.split("=", 1)[1]  # noqa: E731
    return p[0], g(p[1] + ("|" + p[2] if p[1].startswith("L0=RG") else "")), *[g(x) for x in p[-2:]]


def task_null(args):
    t, seed = args
    b, l0, l1, l2 = parse(t)
    d = build(b, l0, l1, l2, 0.0005, seed=seed)
    rv = evalr(d)
    db = build(b, None, None, None, 0.0005, seed=seed)    # 同种子的随机底座，看层本身是否在随机排序下也有用
    rb = evalr(db)
    return dict(combo=t, seed=seed, roll_med=float(np.nanmedian(rv[0])), full=stats(d)["calmar"],
                base_roll_med=float(np.nanmedian(rb[0])), base_full=stats(db)["calmar"], layer_med_delta=X.cmp(rv, rb)[1])


def post(t0):
    S = pd.read_csv(RUN / "solo.csv"); st = pd.read_csv(RUN / "stack.csv")
    fin = st[st.layer == "FINAL"].combo.tolist()
    # 最终框架 vs 底座 / S4 / 宽基VT15，及卫星
    rows, srows = [], []
    for cost in COSTS:
        bp = int(round(cost * 1e4))
        ref = np.load(X.CACHE / f"ref_{bp}.npz")
        broad = dict(ret=ref["broad"], lo=int(ref["broad_lo"][0]), expo=np.ones(len(ref["broad"])), turn=np.zeros(len(ref["broad"])))
        core = dict(ret=ref["core"], expo=np.ones(len(ref["core"])), lo=PV.G["lo0"])
        S4 = load(tag("B2", None, None, None), bp)
        cands = {**{t: load(t, bp) for t in fin}, "B1 底座": load(tag("B1", None, None, None), bp), "S4": S4, "宽基VT15": broad}
        rS4, rB = evalr(S4), evalr(broad)
        cS4 = X.roll_vec(S4["ret"], S4["lo"], "combo", core, cost); cB = X.roll_vec(broad["ret"], broad["lo"], "combo", core, cost)
        for nm, d in cands.items():
            rv = evalr(d)
            base_t = tag(parse(nm)[0], None, None, None) if nm in fin else None
            rbase = evalr(load(base_t, bp)) if base_t else None
            a, b2 = X.cmp(rv, rS4), X.cmp(rv, rB)
            c = X.cmp(rv, rbase) if rbase is not None else (np.nan,) * 4
            rows.append(dict(name=nm, cost_bp=bp, **stats(d), roll_med=float(np.nanmedian(rv[0])),
                             vs_base=f"{c[0]:.3f}/{c[1]:+.3f}/{c[2]}/{c[3]}" if rbase is not None else "",
                             vs_S4=f"{a[0]:.3f}/{a[1]:+.3f}/{a[2]}/{a[3]}", vs_broad=f"{b2[0]:.3f}/{b2[1]:+.3f}/{b2[2]}/{b2[3]}",
                             **{f"seg_{g}": rv[1][i] for i, g in enumerate(PV.SEGS)}))
            if nm == "宽基VT15":
                continue
            mix = RC.mix(core, dict(ret=d["ret"], expo=d["expo"], lo=d["lo"]), PV.WC, PV.starts()[0], cost)["ret"]
            m = E.perf(mix[PV.starts()[0]:])
            cv = X.roll_vec(d["ret"], d["lo"], "combo", core, cost)
            a, b2 = X.cmp(cv, cS4), X.cmp(cv, cB)
            srows.append(dict(name="C3 60% + " + nm, cost_bp=bp, cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"],
                              roll_med=float(np.nanmedian(cv[0])), vs_C3S4=f"{a[0]:.3f}/{a[1]:+.3f}/{a[2]}/{a[3]}",
                              vs_C3broad=f"{b2[0]:.3f}/{b2[1]:+.3f}/{b2[2]}/{b2[3]}"))
        mixb = RC.mix(core, dict(ret=broad["ret"], expo=broad["expo"], lo=broad["lo"]), PV.WC, PV.starts()[0], cost)["ret"]
        m = E.perf(mixb[PV.starts()[0]:])
        srows.append(dict(name="C3 60% + 宽基VT15", cost_bp=bp, cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"], roll_med=float(np.nanmedian(cB[0]))))
    pd.DataFrame(rows).to_csv(RUN / "final_compare.csv", index=False)
    pd.DataFrame(srows).to_csv(RUN / "satellite.csv", index=False)
    # PBO / DSR：全部试过的变体（两个底座的单层 + 叠加步骤，5bp）
    s0 = PV.starts()[0]
    ts = sorted({tag(b, *{"L0": (v, None, None), "L1": (None, v, None), "L2": (None, None, v)}[L]) for b, L, v in S[["base", "layer", "variant"]].itertuples(index=False)}
                | set(st.combo.dropna()) | {tag(b, None, None, None) for b in BASES})
    Mx = np.column_stack([load(t, 5)["ret"][s0:] for t in ts])
    srd = Mx.mean(0) / Mx.std(0, ddof=1)
    out = dict(n_trials=len(ts), trials=ts)
    pbo, lam = X.pbo_cscv(Mx, 16)
    out.update(pbo_sharpe_S16=pbo, lambda_median=float(np.median(lam)))
    for nm in fin + [tag(b, None, None, None) for b in BASES]:
        j = ts.index(nm); r = Mx[:, j]
        dv, sr0 = X.dsr(srd[j], srd, len(r), X.skew(r), X.kurtosis(r))
        out[f"dsr::{nm}"] = dict(dsr=dv, sharpe_ann=float(srd[j] * np.sqrt(252)), sr0_ann=float(sr0 * np.sqrt(252)))
    # 把 combo3 的 384 次也算进去时的 DSR（更保守）
    R3 = X.load_cells(5)
    Mall = np.column_stack([Mx] + [R3[k]["ret"][s0:][:, None] for k in R3])
    sra = Mall.mean(0) / Mall.std(0, ddof=1)
    for nm in fin:
        j = ts.index(nm); r = Mx[:, j]
        dv, sr0 = X.dsr(srd[j], sra, len(r), X.skew(r), X.kurtosis(r))
        out[f"dsr_incl_combo3::{nm}"] = dict(dsr=dv, n=len(sra), sr0_ann=float(sr0 * np.sqrt(252)))
    json.dump(out, open(RUN / "overfit.json", "w"), ensure_ascii=False, indent=1)
    if (RUN / "null_long.csv").exists() and (RUN / "null_long.csv").stat().st_size > 0:
        N = pd.read_csv(RUN / "null_long.csv"); fc = pd.read_csv(RUN / "final_compare.csv")
        rr = []
        for t, g in N.groupby("combo"):
            a = fc[(fc.name == t) & (fc.cost_bp == 5)].iloc[0]
            rr.append(dict(combo=t, roll_med=a.roll_med, null_p50=g.roll_med.median(), null_p95=g.roll_med.quantile(.95),
                           pct_roll=float((g.roll_med < a.roll_med).mean()), full=a.calmar, null_full_p50=g.full.median(),
                           null_full_p95=g.full.quantile(.95), pct_full=float((g.full < a.calmar).mean()),
                           layer_delta_on_random_med=g.layer_med_delta.median(), layer_delta_on_random_pos=float((g.layer_med_delta > 0).mean())))
        pd.DataFrame(rr).to_csv(RUN / "null_percentiles.csv", index=False)
    print("post done", round(time.time() - t0))


if __name__ == "__main__":
    main(sys.argv[1])
