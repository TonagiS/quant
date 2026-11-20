#!/usr/bin/env python3
"""ideas_v1：逐个想法加到 S2+VT15 / S4 上回测，再做卫星×底仓组合。规则见 runs/2026-11-21_ideas_v1/FREEZE.md。
只 import components_core（冻结文件不改）。样本内结果。"""
from __future__ import annotations

import itertools
import sys
import time
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import components_core as K  # noqa: E402
import tr_ens_data as TD  # noqa: E402
import pit_pool_data as PD_  # noqa: E402

RC, E = K.RC, K.E
ROOT = K.ROOT
RUN = ROOT / "runs" / "2026-11-21_ideas_v1"
CI = ROOT / "data" / "cache_ideas"
SEGS = ["Y2015", "Y2018", "S2021_24", "S2023_now"]
COSTS = (0.0005, 0.001, 0.002)
G = {}

# arm → (score, vetoes, overlays)
ARMS = {
    "BASE": ("S2", (), ()),
    "CR80": ("S2", (("crowd", .80),), ()), "CR90": ("S2", (("crowd", .90),), ()), "CR95": ("S2", (("crowd", .95),), ()),
    "SH80": ("S2", (("share", .80),), ()), "SH90": ("S2", (("share", .90),), ()),
    "H52R": ("H52", (), ()), "H52B": ("H52B", (), ()),
    "H52R_F": ("H52", (), ("feb",)), "H52B_F": ("H52B", (), ("feb",)),
    "AM126": ("S2", (), ("am126",)), "AM252": ("S2", (), ("am252",)),
    "RESR": ("RES", (), ()), "RESB": ("RESB", (), ()),
    "CREDIT": ("S2", (), ("credit",)),
}
FAMILY = {"CR": ["CR80", "CR90", "CR95"], "SH": ["SH80", "SH90"], "H52": ["H52R", "H52B", "H52R_F", "H52B_F"],
          "AM": ["AM126", "AM252"], "RES": ["RESR", "RESB"], "CREDIT": ["CREDIT"]}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ 特征
def raw_close(code):
    for d in (PD_.RAW1, PD_.RAW2, TD.RAW3):
        p = d / f"{code}.csv"
        if p.exists():
            return pd.read_csv(p, parse_dates=["date"]).drop_duplicates("date").set_index("date").close.sort_index()
    return None


def setup():
    if G:
        return
    D = RC.data()
    cal, close = D["cal"], D["closeT"]
    cols = list(close.columns)
    RT = close.pct_change(fill_method=None)
    sec = K.sector_map()
    groups = {}
    for j, c in enumerate(cols):
        groups.setdefault(sec[c], []).append(j)
    G["groups"] = [np.array(v) for v in groups.values()]
    G["secv"] = np.array([sec[c] for c in cols])
    # 1 拥挤度
    P = TD.build()
    amt = P["amount"].reindex(cal)[cols]
    a20 = amt.rolling(20, min_periods=15).mean()
    G["crowd"] = a20.rolling(756, min_periods=252).rank(pct=True)
    # 2 份额（仅上交所）
    sh = pd.read_csv(CI / "sse_etf_shares_weekly.csv", parse_dates=["date"])
    W = sh.pivot_table(index="date", columns="code", values="shares_wan", aggfunc="last").sort_index()
    W = W.where(W > 0)
    chg = W / W.shift(1) - 1.0
    bad = pd.DataFrame(False, index=W.index, columns=W.columns)
    for c in W.columns:
        if c not in cols:
            continue
        rc = raw_close(c)
        if rc is None:
            continue
        rr = rc.pct_change()
        tr = P["close_tr"][c].pct_change(fill_method=None).reindex(rr.index)
        jump = ((rr - tr).abs() > 0.2)
        wk = jump.groupby(jump.index.to_period("W")).any()
        wk.index = wk.index.map(lambda p: W.index[W.index.to_period("W") == p][0] if (W.index.to_period("W") == p).any() else pd.NaT)
        wk = wk[wk.index.notna()]
        bad.loc[wk.index[wk.values], c] = True
    chg = chg.mask(bad | (chg > 2.0) | (chg < -0.67))
    spct = chg.rolling(104, min_periods=52).rank(pct=True)
    spct = spct.reindex(columns=cols)
    daily = spct.reindex(cal.union(spct.index)).ffill(limit=10).reindex(cal).shift(1)
    G["share"] = daily
    G["share_cov"] = float(np.mean([c.startswith("sh") for c in cols]))
    # 3 52 周高点
    G["h52"] = close / close.rolling(252, min_periods=200).max()
    # 6 残差动量
    h3 = pd.read_csv(ROOT / "data/cache_index_proxy/H00300.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").close.sort_index()
    rmk = h3.reindex(cal.union(h3.index)).ffill().reindex(cal).pct_change()
    Y = pd.DataFrame(np.repeat(rmk.to_numpy()[:, None], len(cols), 1), index=cal, columns=cols).where(RT.notna())
    X = RT.where(Y.notna())
    rl = dict(window=756, min_periods=252)
    cov = (X * Y).rolling(**rl).mean() - X.rolling(**rl).mean() * Y.rolling(**rl).mean()
    var = (Y * Y).rolling(**rl).mean() - Y.rolling(**rl).mean() ** 2
    beta = (cov / var).shift(1)
    e = RT - beta.mul(rmk, axis=0)
    es = e.shift(21)
    G["res"] = es.rolling(231, min_periods=150).mean() / es.rolling(231, min_periods=150).std()
    # 8 社融脉冲
    sf = pd.read_csv(CI / "social_financing.csv", dtype={"月份": str})
    sf["m"] = pd.PeriodIndex(sf["月份"], freq="M")
    s = sf.set_index("m")["社会融资规模增量"].astype(float).sort_index()
    s12 = s.rolling(12).sum()
    imp = (s12 - s12.shift(12)).dropna()
    on = (imp > 0).astype(float)
    avail = [(p + 1).to_timestamp() + pd.Timedelta(days=15) for p in on.index]   # 次月 16 日起
    pos = cal.searchsorted(pd.DatetimeIndex(avail))
    cs = pd.Series(np.nan, index=cal)
    for p, v in zip(pos, on.values):
        if p < len(cal):
            cs.iloc[p] = v
    G["credit"] = cs.ffill().fillna(1.0).to_numpy()
    G["credit_info"] = dict(first=str(cal[pos[0]].date()), last_data=str(on.index[-1]), on_share=float(on.mean()))
    G["feb"] = np.array([1.0 if (i + 2 >= len(cal) or cal[i + 2].month != 2) else 0.0 for i in range(len(cal))])
    bond = D["Rd"]["sh511010"].reindex(cal).fillna(0.0)
    G["bond_nav"] = (1 + bond).cumprod().to_numpy()
    G["me"] = np.r_[cal[1:].month != cal[:-1].month, True]
    G["ma_ok"] = (close > close.rolling(60, min_periods=60).mean())
    G["base_f"] = {w: K.sat_feats(sd) for w, sd in RC.START_W.items()}
    G["masks"] = {s: np.asarray(RC.seg_mask(*RC.SUB_W[s])) for s in SEGS}


def cap2(score, allowed):
    """与 components_core.sat_feats(sector=True) 逐行逻辑一致（含排序实现），每板块最多 2 只。"""
    cv = allowed.copy()
    sv = G["secv"]
    for i in range(len(cv)):
        idx = np.flatnonzero(cv[i] & ~np.isnan(score[i]))
        if len(idx) <= 2:
            continue
        cnt = {}
        for j in idx[np.argsort(-score[i, idx])]:
            cnt[sv[j]] = cnt.get(sv[j], 0) + 1
            if cnt[sv[j]] > 2:
                cv[i, j] = False
    return cv


def feats(win, sector, score, vetoes):
    b = G["base_f"][win]
    valid = b["valid"]
    rm = b["rm"]
    rk = lambda X: X.where(valid).rank(axis=1, pct=True)  # noqa: E731
    if score == "S2":
        sc = rm
    elif score == "H52":
        sc = rk(G["h52"])
    elif score == "H52B":
        sc = (rm + rk(G["h52"])) / 2
    elif score == "RES":
        sc = rk(G["res"])
    elif score == "RESB":
        sc = (rm + rk(G["res"])) / 2
    cand = valid & sc.notna() & G["ma_ok"]
    for kind, thr in vetoes:
        X = G[kind]
        cand &= ~(X >= thr).fillna(False)
    cv = cand.to_numpy()
    if sector:
        cv = cap2(sc.where(valid).to_numpy(), cv)
    f = dict(b)
    f["rm"] = sc.where(valid)
    f["cand"] = pd.DataFrame(cv, index=valid.index, columns=valid.columns)
    return f


def overlay_mult(kind, ret, lo):
    T = len(ret)
    if kind == "feb":
        return G["feb"]
    if kind == "credit":
        return G["credit"]
    L = int(kind[2:])
    nav = np.cumprod(1 + np.nan_to_num(ret))
    bn = G["bond_nav"]
    m = np.ones(T)
    cur = 1.0
    for t in range(T):
        if G["me"][t] and t - L >= lo and t - 252 >= 0:
            cur = 1.0 if nav[t] / nav[t - L] - 1 >= bn[t] / bn[t - 252] - 1 else 0.0
        m[t] = cur
    return m


def sat(win, sector, score, vetoes, overlays, cost):
    D = RC.data()
    f = feats(win, sector, score, vetoes)
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    r0 = E.simulate(D["closeT"], f, cfg)
    f2 = dict(f); f2["base_ret"] = r0["ret"]
    rr = E.simulate(D["closeT"], f2, replace(cfg, rule=E.VolTarget(0.15, 60, 40, every=10)))
    d = dict(ret=np.asarray(rr["ret"]), turn=np.asarray(rr["turn"]), expo=np.asarray(rr["expo"]), lo=f["start"])
    if overlays:
        m = np.ones(len(d["ret"]))
        for o in overlays:
            m = m * overlay_mult(o, d["ret"], d["lo"])
        d = K.apply_mult(d, m, cost)
    return d


def perf_rows(d, win, extra):
    rows = []
    m = RC.metr(d["ret"], d["lo"], None, d["turn"], d["expo"])
    rows.append({**extra, "window": win, **{k: m[k] for k in ("cagr", "mdd", "calmar", "sharpe", "vol", "turn_two_way", "avg_expo")}})
    if win == "W2013":
        for s in SEGS:
            m = RC.metr(d["ret"], d["lo"], G["masks"][s])
            rows.append({**extra, "window": s, **{k: m[k] for k in ("cagr", "mdd", "calmar", "sharpe", "vol")}})
    return rows


def sat_spec(base, arms):
    """组合若干 arm 成一个卫星规格。"""
    score, vet, ov = "S2", [], []
    for a in arms:
        s, v, o = ARMS[a]
        if s != "S2":
            score = s
        vet += list(v); ov += list(o)
    return dict(sector=(base == "S4"), score=score, vetoes=tuple(vet), overlays=tuple(dict.fromkeys(ov)))


def task_single(args):
    setup()
    base, arm, cost, win = args
    sp = sat_spec(base, [arm])
    d = sat(win, cost=cost, **sp)
    return perf_rows(d, win, {"base": base, "arm": arm, "cost_bp": int(round(cost * 1e4))})


# ------------------------------------------------------------------ 组合
def core(name, lo, cost):
    if name == "DIV-IV":
        return K.core_run(lo, cost)
    return K.core_run(lo, cost, mult=K.erp_mult()[0])


def task_sat(args):
    setup()
    key, arms, cost, win = args
    sp = sat_spec("S4", arms)
    d = sat(win, cost=cost, **sp)
    return key, cost, win, d


def main_single():
    t0 = time.time()
    tasks = [(b, a, c, w) for b in ("S2", "S4") for a in ARMS for c in COSTS for w in RC.START_W]
    rows = []
    with Pool(6, initializer=setup) as p:
        for k, r in enumerate(p.imap_unordered(task_single, tasks)):
            rows += r
            if k % 40 == 0:
                log(k, len(tasks), round(time.time() - t0))
    df = pd.DataFrame(rows)
    df.to_csv(RUN / "single_ideas.csv", index=False)
    return df


def choose_helpful(df):
    x = df[(df.base == "S4") & (df.cost_bp == 5) & df.window.isin(list(RC.START_W))]
    cal = x.pivot_table(index="arm", columns="window", values="calmar")
    b = cal.loc["BASE"]
    chosen, notes = [], {}
    for fam, arms in FAMILY.items():
        sub = cal.loc[arms]
        better = (sub.gt(b, axis=1)).sum(axis=1)
        best = sub["W2013"].idxmax()
        ok = sub.loc[best, "W2013"] > b["W2013"] and better[best] >= 2
        notes[fam] = dict(best=best, w2013=float(sub.loc[best, "W2013"]), base=float(b["W2013"]), wins=int(better[best]), used=bool(ok))
        if ok:
            chosen.append(best)
    return chosen, notes


def main_combo(df):
    setup()
    chosen, notes = choose_helpful(df)
    log("helpful", chosen, notes)
    scorers = [a for a in chosen if ARMS[a][0] != "S2"]
    others = [a for a in chosen if ARMS[a][0] == "S2"]
    sats = {}
    for sc in [None] + scorers:
        for r in range(len(others) + 1):
            for sub in itertools.combinations(others, r):
                arms = ([sc] if sc else []) + list(sub)
                sats["S4" + "".join("+" + a for a in arms)] = arms
    log("n sats", len(sats))
    tasks = [(k, a, c, w) for k, a in sats.items() for c in COSTS for w in RC.START_W]
    SD = {}
    with Pool(6, initializer=setup) as p:
        for key, cost, win, d in p.imap_unordered(task_sat, tasks):
            SD[(key, cost, win)] = d
    rows, R13 = [], {}
    for cost in COSTS:
        for win in RC.START_W:
            for cname in ("DIV-IV", "C3"):
                lo0 = SD[(next(iter(sats)), cost, win)]["lo"]
                cd = core(cname, lo0, cost)
                ex = {"combo": cname, "sat": "-", "core": cname, "wc": 1.0, "cost_bp": int(round(cost * 1e4))}
                rows += perf_rows(cd, win, ex)
                if cost == 0.0005 and win == "W2013":
                    R13[cname] = cd["ret"]
                for sk in sats:
                    sd = SD[(sk, cost, win)]
                    for wc in (0.0, 0.5, 0.6, 0.7, 0.8):
                        if wc == 0.0 and cname == "C3":
                            continue
                        md = RC.mix(cd, sd, wc, sd["lo"], cost) if wc > 0 else sd
                        nm = sk if wc == 0 else f"{cname}{int(wc*100)}/{sk}"
                        rows += perf_rows(md, win, {"combo": nm, "sat": sk, "core": cname if wc else "-", "wc": wc,
                                                    "cost_bp": int(round(cost * 1e4))})
                        if cost == 0.0005 and win == "W2013":
                            R13[nm] = md["ret"]
    cdf = pd.DataFrame(rows)
    cdf.to_csv(RUN / "combos.csv", index=False)
    np.savez_compressed(RUN / "_combo_ret_w2013_5bp.npz", names=np.array(list(R13)), R=np.array(list(R13.values())))
    pd.Series(notes).to_json(RUN / "helpful.json", force_ascii=False, indent=1)
    return cdf, R13


def walk_forward(R13, lo):
    cal = RC.data()["cal"]
    names = list(R13)
    R = np.array([R13[n] for n in names])
    out = np.zeros(R.shape[1])
    picks = []
    for y in range(2016, 2027):
        a = cal.searchsorted(pd.Timestamp(f"{y}-01-01"))
        b = cal.searchsorted(pd.Timestamp(f"{y+1}-01-01"))
        best, bv = None, -9
        for i in range(len(names)):
            r = R[i, lo:a]
            nav = np.cumprod(1 + r)
            yrs = len(r) / 244
            cagr = nav[-1] ** (1 / yrs) - 1
            mdd = (nav / np.maximum.accumulate(nav) - 1).min()
            c = cagr / abs(mdd) if mdd < 0 else 0
            if c > bv:
                bv, best = c, i
        out[a:b] = R[best, a:b]
        picks.append({"year": y, "pick": names[best], "insample_calmar": round(bv, 3)})
    s = cal.searchsorted(pd.Timestamp("2016-01-01"))
    m = RC.metr(out, s)
    return pd.DataFrame(picks), m, s


def main():
    setup()
    log("share coverage(sh share of cols)", G["share_cov"], "credit", G["credit_info"])
    df = main_single() if not (RUN / "single_ideas.csv").exists() else pd.read_csv(RUN / "single_ideas.csv")
    cdf, R13 = main_combo(df)
    lo = G["base_f"]["W2013"]["start"]
    picks, m, s = walk_forward(R13, lo)
    picks.to_csv(RUN / "walk_forward_picks.csv", index=False)
    fixed = {}
    for nm in ("C380/S4", "DIV-IV80/S4", "S4"):
        if nm in R13:
            fixed[nm] = RC.metr(R13[nm], s)
    pd.DataFrame({"WF": m, **fixed}).T.to_csv(RUN / "walk_forward_perf.csv")
    log("WF", {k: round(v, 3) for k, v in m.items() if isinstance(v, float)})


if __name__ == "__main__":
    main()
