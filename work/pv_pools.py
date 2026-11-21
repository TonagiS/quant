#!/usr/bin/env python3
"""pool_variants（2026-11-21）：PIT 池选法变体 × S4 / S2+VT15 / C3 60%+S4。
规则见 runs/2026-11-21_pool_variants/FREEZE.md。总收益面板 v2（trfix 后）。样本内。
用法：python3 work/pv_pools.py check | run | null"""
from __future__ import annotations

import json
import sys
import time
import zlib
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import components_core as K  # noqa: E402
from fullmkt_single_asset import sector_tag  # noqa: E402

RC, PB, E, TD = K.RC, K.PB, K.E, K.TD
ROOT = K.ROOT
RUN = ROOT / "runs" / "2026-11-21_pool_variants"
CPV = ROOT / "data" / "cache_pool_variants"
END = pd.Timestamp("2026-11-13")
SDATE = RC.START_W["W2013"]
COSTS = (0.0005, 0.0020)
WC = 0.60
BROAD = ["sh510300", "sh510500", "sz159915", "sh588000", "sh512100"]
THEMES = ["科创芯片", "CPO", "半导体设备", "机器人", "银行", "猪肉", "传媒"]
START_FIRST, START_LAST = "2014-06-01", "2023-11-13"
SEGS = {"2014-16": ("2014-06-03", "2016-12-31"), "2017-19": ("2017-01-01", "2019-12-31"),
        "2020-22": ("2020-01-01", "2022-12-31"), "2023-26": ("2023-01-01", "2026-11-13")}
PASS_SHARE, PASS_MED, PASS_SEG = 0.70, 0.05, 3
N_NULL = 100
VARIANTS = ["BASE", "P1_DEDUP40", "P2_F1E8", "P2_F3E8", "P2_F5E8", "P2_F3E8_DEDUP", "P3_FEE40", "P3_QUAL40",
            "P4_THEME", "P5_N20", "P5_N30", "P5_N60", "P5_N80", "P6_SEC3", "P7_CROWDX", "P7_ATTN", "P8_NOXB",
            "P9_SEASON500", "P10_LOWCORR", "P11_A250", "P12_HYST60"]
G = {}


def setup():
    if G:
        return
    P = TD.build()
    D = RC.data()
    cal = D["cal"]
    cat = P["cat"]
    el = [c for c in P["close_tr"].columns if cat.get(c) not in ("货币", "债券")]
    C = P["close_tr"].reindex(cal)[el]
    A = P["amount"].reindex(columns=el).reindex(cal)
    u = pd.read_csv(ROOT / "runs/2026-11-14_fullmkt_single_asset/universe_all.csv").set_index("tx_code")
    nm1 = pd.read_csv(ROOT / "data/etf_universe.csv").set_index("code")["name"].to_dict()
    name = {c: nm1.get(c, u["name"].get(c, "")) for c in el}
    sec = {c: sector_tag(cat.get(c, ""), nm1.get(c, ""), "") for c in el}     # 同 K.sector_map 口径
    fee = pd.read_csv(CPV / "fees_em.csv")
    fee["tot"] = pd.to_numeric(fee.mgmt_fee, errors="coerce") + pd.to_numeric(fee.cust_fee, errors="coerce")
    G.update(cal=cal, C=C, A=A, cat=cat, el=el, name=name, sec=sec,
             idx=u["INDEX_CODE"].astype(str).to_dict(), focus=u["focus"].to_dict(),
             fee=fee.set_index("code")["tot"].to_dict(),
             bars=C.notna().cumsum(), a60=A.rolling(60, min_periods=40).mean(),
             a20=A.rolling(20, min_periods=15).mean(), a120=A.rolling(120, min_periods=90).mean(),
             a250=A.rolling(250, min_periods=200).mean(), R=C.pct_change(fill_method=None))
    me = cal.to_series().groupby(cal.to_period("M")).max()
    G["me"] = list(me[(me >= pd.Timestamp("2011-01-01")) & (me <= END)])
    # 上交所周度份额 × 不复权价 = PIT 规模（仅沪市；深市无历史 → 中性）
    sh = pd.read_csv(ROOT / "data/cache_ideas/sse_etf_shares_weekly.csv", parse_dates=["date"])
    G["shares"] = sh.pivot_table(index="date", columns="code", values="shares_wan").sort_index()
    raw = {}
    for c in el:
        if c.startswith("sh"):
            for d in (K.TD.D.RAW1, K.TD.D.RAW2, K.TD.RAW3):
                p = d / f"{c}.csv"
                if p.exists():
                    raw[c] = pd.read_csv(p, parse_dates=["date"]).drop_duplicates("date").set_index("date").close
                    break
    G["raw"] = pd.DataFrame(raw).reindex(cal)
    G["lo0"] = int(K.sat_feats(SDATE)["start"])


def ranked(d, min_bars=250, excl_cats=()):
    ok = (G["bars"].loc[d] >= min_bars) & G["C"].loc[d].notna()
    s = G["a60"].loc[d][ok].dropna()
    if excl_cats:
        s = s[[G["cat"].get(c) not in excl_cats for c in s.index]]
    return s.sort_values(ascending=False)


def dedup(codes):
    seen, out = set(), []
    for c in codes:
        k = G["idx"].get(c, c)
        if k in ("nan", "None", ""):
            k = c
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def aum_rank(d, codes):
    sh = G["shares"]
    i = sh.index.searchsorted(d, side="right") - 1
    row = sh.iloc[i] if i >= 0 else pd.Series(dtype=float)
    v = {}
    for c in codes:
        x = row.get(c, np.nan) if len(row) else np.nan
        px = G["raw"][c].loc[d] if c in G["raw"].columns else np.nan
        v[c] = x * px if np.isfinite(x) and np.isfinite(px) else np.nan
    s = pd.Series(v)
    r = s.rank(ascending=False, pct=True)
    return r.fillna(0.5)            # 深市/缺失：中性


def pool_on(var, d, prev):
    if var == "BASE":
        return list(ranked(d).index[:40])
    if var == "P1_DEDUP40":
        return dedup(ranked(d).index)[:40]
    if var.startswith("P2_F"):
        thr = float(var[4]) * 1e8
        s = ranked(d)
        out = list(s[s >= thr].index)
        return dedup(out) if var.endswith("DEDUP") else out
    if var == "P3_FEE40":
        s = ranked(d).iloc[:80]
        f = pd.Series({c: G["fee"].get(c, np.nan) for c in s.index}).fillna(np.inf)
        o = pd.DataFrame({"fee": f, "a": -s}).sort_values(["fee", "a"])
        return list(o.index[:40])
    if var == "P3_QUAL40":
        s = ranked(d).iloc[:80]
        f = pd.Series({c: G["fee"].get(c, np.nan) for c in s.index})
        sc = f.rank(pct=True).fillna(1.0) + aum_rank(d, s.index)
        o = pd.DataFrame({"sc": sc, "a": -s}).sort_values(["sc", "a"])
        return list(o.index[:40])
    if var == "P4_THEME":
        s = ranked(d)
        out = [c for c in BROAD if c in s.index]
        for th in THEMES:
            m = [c for c in s.index if G["focus"].get(c) == th or (th == "CPO" and G["sec"].get(c) == "通信")]
            out += [c for c in m[:2] if c not in out]
        return out
    if var.startswith("P5_N"):
        return list(ranked(d).index[:int(var[4:])])
    if var == "P6_SEC3":
        cnt, out = {}, []
        for c in ranked(d).index:
            k = G["sec"].get(c, "")
            if cnt.get(k, 0) < 3:
                cnt[k] = cnt.get(k, 0) + 1
                out.append(c)
            if len(out) == 40:
                break
        return out
    if var == "P7_CROWDX":
        s = ranked(d)
        g = (G["a20"].loc[d] / G["a120"].loc[d]).reindex(s.index)
        return list(s[~(g > 3.0)].index[:40])
    if var == "P7_ATTN":
        s = ranked(d).iloc[:80]
        g = (G["a20"].loc[d] / G["a120"].loc[d]).reindex(s.index).fillna(1.0)
        return list(g.sort_values(ascending=False).index[:40])
    if var == "P8_NOXB":
        return list(ranked(d, excl_cats=("跨境", "商品")).index[:40])
    if var == "P9_SEASON500":
        return list(ranked(d, min_bars=500).index[:40])
    if var == "P10_LOWCORR":
        s = ranked(d).iloc[:80]
        i = G["cal"].get_loc(d)
        R = G["R"].iloc[max(0, i - 119): i + 1][list(s.index)]
        cm = R.corr(min_periods=60).fillna(1.0).to_numpy()
        sel = [0]
        rest = list(range(1, len(s)))
        while len(sel) < min(40, len(s)) and rest:
            mc = cm[np.ix_(rest, sel)].mean(axis=1)
            j = rest[int(np.argmin(mc))]
            sel.append(j)
            rest.remove(j)
        return [s.index[k] for k in sel]
    if var == "P11_A250":
        ok = (G["bars"].loc[d] >= 250) & G["C"].loc[d].notna()
        return list(G["a250"].loc[d][ok].dropna().sort_values(ascending=False).index[:40])
    if var == "P12_HYST60":
        s = ranked(d)
        top60 = set(s.index[:60])
        keep = [c for c in (prev or []) if c in top60]
        out = keep + [c for c in s.index if c not in keep]
        out = out[:40]
        return out
    raise KeyError(var)


def lists_of(var):
    out, prev = {}, None
    for d in G["me"]:
        prev = pool_on(var, d, prev)
        out[d] = prev
    return out


def random_lists(seed, sizes, universe="elig"):
    rng = np.random.default_rng(seed)
    out = {}
    for d in G["me"]:
        s = ranked(d)
        if universe == "top80":
            s = s.iloc[:80]
        n = min(sizes[d], len(s))
        out[d] = list(rng.choice(np.array(s.index), size=n, replace=False)) if n > 0 else []
    return out


def sat_feats_pool(close, member, sector):
    """同 components_core.sat_feats（skip=0，无 quality），只把 close/member 换成该池。"""
    ret = close.pct_change(fill_method=None)
    vol20 = ret.rolling(20, min_periods=15).std()
    H = (20, 60, 120)
    mom = {n: close / close.shift(n) - 1.0 for n in H}
    ma60 = close.rolling(60, min_periods=60).mean()
    bars = close.notna().cumsum()
    valid = (bars >= 150) & close.notna() & mom[120].notna() & vol20.notna() & member
    rk = {n: mom[n].where(valid).rank(axis=1, pct=True) for n in H}
    rm = (rk[20] + rk[60] + rk[120]) / 3.0
    cand = valid & rm.notna() & (close > ma60)
    if sector:
        rmv, cv = rm.to_numpy(), cand.to_numpy().copy()
        sv = np.array([G["sec"].get(c, "") for c in close.columns])
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
    enough = (valid.sum(axis=1) >= 8) & rm.notna().any(axis=1)
    enough &= close.index >= pd.Timestamp(SDATE)
    start = int(np.argmax(enough.to_numpy()))
    return {"ret": ret, "vol20": vol20, "mom": mom, "ma60": ma60, "valid": valid, "rm": rm,
            "cand": cand, "start": start, "bench": (1.0 + ret.mean(axis=1).fillna(0.0)).cumprod()}


def sat_pool(lists, cost, sector):
    ever = sorted({c for v in lists.values() for c in v})
    close = G["C"][ever]
    M = PB.member_mask(lists, G["cal"], ever)
    f = sat_feats_pool(close, M, sector)
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    r0 = E.simulate(close, f, cfg)
    f2 = dict(f); f2["base_ret"] = r0["ret"]
    rr = E.simulate(close, f2, replace(cfg, rule=E.VolTarget(0.15, 60, 40, every=10)))
    return dict(ret=np.asarray(rr["ret"]), turn=np.asarray(rr["turn"]), expo=np.asarray(rr["expo"]), lo=f["start"])


def starts():
    cal = G["cal"]
    me = pd.Series(np.arange(len(cal)), index=cal)
    ms = me[(cal >= START_FIRST) & (cal <= START_LAST)]
    return ms.groupby(ms.index.to_period("M")).min().to_list()


def calmar(r):
    r = np.asarray(r)
    if len(r) < 60:
        return np.nan
    eq = np.cumprod(1 + r)
    cagr = eq[-1] ** (252 / len(r)) - 1 if eq[-1] > 0 else -1.0
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return cagr / abs(dd) if dd < 0 else np.nan


def roll_seg(kind, sat, core, cost):
    """kind: sat（卫星单独，自 lo0 连续运行，从各起点切片）/ combo（C3 60% + 卫星 40%，各起点重新起步、月末再平衡）。"""
    cal = G["cal"]
    out = {}
    for s in starts():
        if kind == "sat":
            r = sat["ret"][s:] if sat["lo"] <= s else None
        else:
            r = RC.mix(core, sat, WC, s, cost)["ret"][s:] if sat["lo"] <= s else None
        out[("start", s)] = calmar(r) if r is not None else np.nan
    for g, (a, b) in SEGS.items():
        lo = int(cal.searchsorted(pd.Timestamp(a)))
        hi = int(cal.searchsorted(pd.Timestamp(b), side="right"))
        if sat["lo"] > lo:
            out[("seg", g)] = np.nan
            continue
        r = sat["ret"][lo:hi] if kind == "sat" else RC.mix(core, sat, WC, lo, cost)["ret"][lo:hi]
        out[("seg", g)] = calmar(r)
    return out


def full_stats(r, lo):
    m = E.perf(np.asarray(r)[lo:])
    return {k: float(m[k]) for k in ("cagr", "mdd", "calmar")}


def task_var(args):
    var, cost = args
    setup()
    lists = lists_of(var)
    core = K.core_run(G["lo0"], cost, mult=K.erp_mult()[0])
    rows, info = [], {}
    s0 = starts()[0]
    for sname, sector in (("S4", True), ("S2+VT15", False)):
        sat = sat_pool(lists, cost, sector)
        kinds = [("sat", sname)] + ([("combo", "C3 60%+S4")] if sname == "S4" else [])
        for kind, label in kinds:
            ev = roll_seg(kind, sat, core, cost)
            r = sat["ret"] if kind == "sat" else RC.mix(core, sat, WC, s0, cost)["ret"]
            fs = full_stats(r, s0)
            for (k, key), v in ev.items():
                rows.append(dict(variant=var, strat=label, cost_bp=int(round(cost * 1e4)), kind=k, key=str(key), calmar=v))
            rows.append(dict(variant=var, strat=label, cost_bp=int(round(cost * 1e4)), kind="full", key="2014-06起",
                             calmar=fs["calmar"], cagr=fs["cagr"], mdd=fs["mdd"],
                             turn=float(np.mean(sat["turn"][s0:]) * 252), sat_lo=str(G["cal"][sat["lo"]].date())))
    sz = [len(v) for d, v in lists.items() if d >= pd.Timestamp("2014-05-30")]
    info = dict(variant=var, size_med=float(np.median(sz)), size_min=int(min(sz)), size_max=int(max(sz)),
                n_ever=len({c for v in lists.values() for c in v}))
    return rows, info


def task_null(args):
    universe, seed, cost = args
    setup()
    base_sizes = {d: 40 for d in G["me"]}
    lists = random_lists(seed, base_sizes, universe)
    core = K.core_run(G["lo0"], cost, mult=K.erp_mult()[0])
    sat = sat_pool(lists, cost, True)
    rows = []
    for kind, label in (("sat", "S4"), ("combo", "C3 60%+S4")):
        ev = roll_seg(kind, sat, core, cost)
        for (k, key), v in ev.items():
            rows.append(dict(universe=universe, seed=seed, strat=label, cost_bp=int(round(cost * 1e4)), kind=k, key=str(key), calmar=v))
    return rows


def main(mode):
    RUN.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    setup()
    K.core_returns(); K.erp_mult()       # 父进程先建好底仓缓存，fork 后子进程共用（避免并发拷贝 162411 文件的竞态）
    if mode == "check":
        setup()
        bl = lists_of("BASE")
        D = RC.data()
        same = all(bl[d] == D["lists"][d] for d in G["me"] if d in D["lists"])
        a = sat_pool(bl, 0.0005, True)
        b = K.sat_run(SDATE, 0.0005, sector=True)
        print("lists identical:", same, "| S4 max abs diff:", float(np.max(np.abs(a["ret"] - b["ret"]))), "lo", a["lo"], b["lo"],
              "| t", round(time.time() - t0))
        for v in VARIANTS:
            L = lists_of(v)
            sz = [len(x) for d, x in L.items() if d >= pd.Timestamp("2014-05-30")]
            print(v, "size med/min/max", np.median(sz), min(sz), max(sz), "ever", len({c for x in L.values() for c in x}), flush=True)
        return
    if mode == "run":
        tasks = [(v, c) for c in COSTS for v in VARIANTS]
        rows, infos = [], []
        with Pool(8) as p:
            for r, inf in p.imap_unordered(task_var, tasks):
                rows += r
                infos.append(inf)
                print("done", inf["variant"], len(rows), round(time.time() - t0), flush=True)
        pd.DataFrame(rows).to_csv(RUN / "results_long.csv.gz", index=False)
        pd.DataFrame(infos).drop_duplicates("variant").to_csv(RUN / "pool_sizes.csv", index=False)
        return
    if mode == "null":
        tasks = [(u, s, 0.0005) for u in ("elig", "top80") for s in range(30000, 30000 + N_NULL)]
        rows = []
        with Pool(8) as p:
            for k, r in enumerate(p.imap_unordered(task_null, tasks)):
                rows += r
                if k % 20 == 0:
                    print("null", k, len(tasks), round(time.time() - t0), flush=True)
        pd.DataFrame(rows).to_csv(RUN / "null_long.csv.gz", index=False)


if __name__ == "__main__":
    main(sys.argv[1])
