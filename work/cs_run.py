#!/usr/bin/env python3
"""core_stress：底仓 C3 / DIV-IV（及 C3 60% + S4+CREDIT 40%）压力测试。规则见 runs/2026-11-21_core_stress/FREEZE.md。样本内。"""
from __future__ import annotations

import json
import re
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cs_core as X  # noqa: E402

RUN = X.RUN
COST = 0.0005
D_BOND, D_SHORT = None, None          # 经验久期，setup 里回归得到
EPIS = {"2013钱荒": ("2013-05-01", "2013-12-31"), "2015股灾": ("2015-06-12", "2016-02-29"),
        "2016Q4-2017债灾": ("2016-10-21", "2017-12-29"), "2018": ("2018-01-02", "2018-12-28"),
        "2020-05债市回调": ("2020-04-29", "2020-11-30"), "2022股债双杀": ("2022-01-04", "2022-12-30")}
TWEAKS = ["none", "BCAP50", "BCAP40", "SD50", "SD100", "BC50S"]
SHOCKS = [(0.01, 126), (0.01, 252), (0.02, 126), (0.02, 252)]
OVL = {"GOLD-30": {"sh518880": 0.70}, "US-30": {"sh513100": 0.70, "sh513500": 0.70},
       "CNY+10": {"sh513100": 1 / 1.10, "sh513500": 1 / 1.10},
       "CNY+10含金": {"sh513100": 1 / 1.10, "sh513500": 1 / 1.10, "sh518880": 1 / 1.10},
       "全反转": {"sh518880": 0.70, "sh513100": 0.70, "sh513500": 0.70, "rate": 0.02}}
G = {}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def durations():
    R = X.R0()
    br = pd.read_csv(X.ROOT / "data/cache_ideas/ak_bond_rate.csv", parse_dates=["日期"]).set_index("日期").sort_index()
    out = {}
    for a, col in ((X.BOND, "中国国债收益率5年"), (X.SHORT, "中国国债收益率2年")):
        y = pd.to_numeric(br[col], errors="coerce").dropna()
        nv = (1 + R[a]["2014":].fillna(0)).cumprod()
        w = nv.resample("W-FRI").last().pct_change()
        dy = y.resample("W-FRI").last().diff() / 100
        d = pd.concat([w, dy], axis=1, sort=True).dropna()
        out[a] = float(-np.polyfit(d.iloc[:, 1], d.iloc[:, 0], 1)[0])
    out["y5_last"] = float(pd.to_numeric(br["中国国债收益率5年"], errors="coerce").dropna().iloc[-1])
    out["y2_last"] = float(pd.to_numeric(br["中国国债收益率2年"], errors="coerce").dropna().iloc[-1])
    out["y_last_date"] = str(br.index[-1].date())
    return out


def setup():
    if G:
        return
    G["dur"] = durations()
    import pv2_core as C
    C.setup()
    s4c = {c: C.sat_run(c, credit=True) for c in (0.0005, 0.002)}
    cal = C.P["cal"]
    assert len(cal) == len(X.R0().index) and (cal == X.R0().index).all()
    G["s4c"] = s4c
    G["C"] = C


def combo(core_d, cost=COST):
    C = G["C"]
    sat = G["s4c"][cost]
    cd = dict(ret=core_d["ret"], turn=core_d["turn"], expo=np.ones(len(core_d["ret"])), lo=core_d["lo"])
    return C.RC.mix(cd, sat, 0.6, sat["lo"], cost)


def series_map(cost=COST):
    out = {}
    for k in ("DIV-IV", "C3"):
        out[k] = X.core(kind=k, cost=cost)
    out["C3 60%+(S4+CREDIT)"] = combo(out["C3"], cost)
    out["S4+CREDIT"] = G["s4c"][cost]
    return out


# ------------------------------------------------------------------ (a) 历史区间
def part_episodes():
    S = series_map()
    R = X.R0()
    idx = R.index
    lo = S["C3"]["lo"]
    rows = []
    for ep, (a, b) in EPIS.items():
        for nm, d in S.items():
            rows.append(dict(episode=ep, series=nm, **X.episode(d["ret"], idx, a, b, lo)))
        for a_ in ("sh511010", "sh518880", "sh513100", "sh513500", "sh510300", "sh511360"):
            r = R[a_].fillna(0).to_numpy()
            if R[a_].first_valid_index() > pd.Timestamp(a):
                continue
            rows.append(dict(episode=ep, series=a_, **X.episode(r, idx, a, b, lo)))
    ep = pd.DataFrame(rows)
    ep.to_csv(RUN / "episodes.csv", index=False)
    # 区间内 C3 / DIV-IV 的分袖贡献
    rows = []
    for nm in ("DIV-IV", "C3"):
        d = S[nm]
        for e, (a, b) in EPIS.items():
            ia, ib = idx.searchsorted(pd.Timestamp(a)), idx.searchsorted(pd.Timestamp(b), side="right")
            con = pd.Series(d["contrib"][ia:ib].sum(0), index=d["cols"]).groupby(X.SLEEVE).sum()
            w = pd.Series(d["W"][ia:ib].mean(0), index=d["cols"]).groupby(X.SLEEVE).sum()
            rows.append(dict(series=nm, episode=e, **{f"贡献_{k}": float(v) for k, v in con.items()},
                             **{f"均权_{k}": float(v) for k, v in w.items()}))
    pd.DataFrame(rows).to_csv(RUN / "episode_sleeve_contrib.csv", index=False)
    return ep


# ------------------------------------------------------------------ (c) 收益拆分
def part_decomp():
    S = series_map()
    idx = X.R0().index
    rows = []
    for nm in ("DIV-IV", "C3"):
        d = S[nm]
        lo = d["lo"]
        con = pd.DataFrame(d["contrib"][lo:], index=idx[lo:], columns=d["cols"]).T.groupby(X.SLEEVE).sum().T
        W = pd.DataFrame(d["W"][lo:], index=idx[lo:], columns=d["cols"]).T.groupby(X.SLEEVE).sum().T
        yr = con.groupby(con.index.year).sum()
        yr["合计(毛)"] = yr.sum(axis=1)
        for y, r in yr.iterrows():
            rows.append(dict(series=nm, year=int(y), **{k: float(v) for k, v in r.items()}))
        tot = con.sum()
        rows.append(dict(series=nm, year="全期合计(算术)", **{k: float(v) for k, v in tot.items()}, **{"合计(毛)": float(tot.sum())}))
        rows.append(dict(series=nm, year="全期均权", **{k: float(v) for k, v in W.mean().items()}))
        rows.append(dict(series=nm, year="最新权重", **{k: float(v) for k, v in W.iloc[-1].items()}))
    t = pd.DataFrame(rows)
    t.to_csv(RUN / "decomposition.csv", index=False)
    return t


# ------------------------------------------------------------------ 冲击工具
def shock_R(R, i0, spec, n):
    R2 = R.copy()
    dur = G["dur"]
    i1 = min(i0 + n, len(R2))
    for a, f in spec.items():
        if a == "rate":
            for b_, D in ((X.BOND, dur[X.BOND]), (X.SHORT, dur[X.SHORT])):
                col = R2.columns.get_loc(b_)
                R2.iloc[i0:i1, col] = R2.iloc[i0:i1, col] - D * f / n
        else:
            col = R2.columns.get_loc(a)
            daily = f ** (1.0 / n)
            R2.iloc[i0:i1, col] = (1 + R2.iloc[i0:i1, col]) * daily - 1
    return R2


def win_stats(r, i0, n):
    r = np.asarray(r)
    seg = r[i0: min(i0 + n + 63, len(r))]
    eq = nav(seg)
    mdd = float((eq / np.maximum.accumulate(np.r_[1.0, eq])[1:] - 1).min())
    r12 = float(np.prod(1 + r[i0: i0 + 252]) - 1)
    return mdd, r12


def nav(x):
    return np.cumprod(1 + np.asarray(x))


def shock_starts():
    idx = X.R0().index
    out = []
    for y in range(2014, 2026):
        for m in range(1, 13):
            i = int(idx.searchsorted(pd.Timestamp(y, m, 1)))
            if i + 252 + 63 < len(idx):
                out.append(i)
    return out


KINDS = [("DIV-IV", "none"), ("C3", "none")] + [("C3", t) for t in TWEAKS[1:]]


def task_shock(args):
    setup()
    sname, spec, n, i0 = args
    R = X.R0()
    R2 = shock_R(R, i0, spec, n)
    rows = []
    for kind, tw in KINDS:
        d0 = base_core(kind, tw)
        d1 = X.core(R2, kind=kind, tweak=tw)
        m0, r0 = win_stats(d0["ret"], i0, n)
        m1, r1 = win_stats(d1["ret"], i0, n)
        row = dict(scenario=sname, n_days=n, start=str(R.index[i0].date()), kind=kind, tweak=tw,
                   mdd_base=m0, mdd_shock=m1, r12_base=r0, r12_shock=r1)
        rows.append(row)
        if tw == "none" and kind == "C3":
            c0, c1 = combo(d0), combo(d1)
            m0, r0 = win_stats(c0["ret"], i0, n)
            m1, r1 = win_stats(c1["ret"], i0, n)
            rows.append(dict(row, kind="C3 60%+(S4+CREDIT)", mdd_base=m0, mdd_shock=m1, r12_base=r0, r12_shock=r1))
    return rows


_BC = {}


def base_core(kind, tw, cost=COST):
    k = (kind, tw, cost)
    if k not in _BC:
        _BC[k] = X.core(kind=kind, tweak=tw, cost=cost)
    return _BC[k]


def part_shocks():
    tasks = []
    for dy, n in SHOCKS:
        nm = f"利率+{int(dy*1e4)}bp/{n}日"
        tasks += [(nm, {"rate": dy}, n, i) for i in shock_starts()]
    for nm, spec in OVL.items():
        tasks += [(nm, spec, 252, i) for i in shock_starts()]
    rows = []
    with Pool(6, initializer=setup) as p:
        for k, r in enumerate(p.imap_unordered(task_shock, tasks, chunksize=4)):
            rows += r
            if k % 200 == 0:
                log("shock", k, len(tasks))
    df = pd.DataFrame(rows)
    df.to_csv(RUN / "shocks_by_start.csv.gz", index=False)
    df["d_mdd"] = df.mdd_shock - df.mdd_base
    df["d_r12"] = df.r12_shock - df.r12_base
    s = df.groupby(["scenario", "kind", "tweak"], sort=False).agg(
        n=("start", "size"), mdd_shock_med=("mdd_shock", "median"), mdd_shock_worst=("mdd_shock", "min"),
        d_mdd_med=("d_mdd", "median"), r12_shock_med=("r12_shock", "median"), r12_shock_p10=("r12_shock", lambda x: x.quantile(0.1)),
        d_r12_med=("d_r12", "median"), worst_start=("mdd_shock", lambda x: str(df.loc[x.idxmin(), "start"]))).reset_index()
    s.to_csv(RUN / "shocks_summary.csv", index=False)
    return s


# ------------------------------------------------------------------ 低票息重放
def part_lowcarry():
    R = X.R0().copy()
    dur = G["dur"]
    lo = X.s0()
    rows = []
    adj = {}
    for a, y in ((X.BOND, dur["y5_last"]), (X.SHORT, dur["y2_last"])):
        r = R[a].iloc[lo:].fillna(0)
        cagr = float((1 + r).prod() ** (252 / len(r)) - 1)
        dd = (np.log1p(cagr) - np.log1p(y / 100)) / 252
        adj[a] = dict(hist_cagr=cagr, target=y / 100, daily_log_cut=dd)
        R.iloc[lo:, R.columns.get_loc(a)] = (1 + R[a].iloc[lo:]) * np.exp(-dd) - 1
    for kind, tw in KINDS:
        for cost in (0.0005, 0.002):
            d0 = X.core(kind=kind, tweak=tw, cost=cost)
            d1 = X.core(R, kind=kind, tweak=tw, cost=cost)
            for lab, d in (("历史", d0), ("国债只拿当前收益率", d1)):
                rows.append(dict(kind=kind, tweak=tw, cost_bp=int(cost * 1e4), replay=lab, **X.perf(d["ret"][d["lo"]:])))
            if kind == "C3" and tw == "none":
                for lab, d in (("历史", d0), ("国债只拿当前收益率", d1)):
                    c = combo(d, cost)
                    rows.append(dict(kind="C3 60%+(S4+CREDIT)", tweak="none", cost_bp=int(cost * 1e4), replay=lab, **X.perf(c["ret"][c["lo"]:])))
    t = pd.DataFrame(rows)
    t.to_csv(RUN / "lowcarry_replay.csv", index=False)
    json.dump(adj, open(RUN / "lowcarry_adj.json", "w"), ensure_ascii=False, indent=1)
    return t, adj


# ------------------------------------------------------------------ (b) 股债正相关 bootstrap
BLOCK, NPATH, PLEN, WARM = 20, 300, 756, 80


def corr_labels():
    R = X.R0()
    c = R["sh510300"].rolling(120, min_periods=100).corr(R[X.BOND])
    return c


def task_boot(args):
    setup()
    regime, seed = args
    R = X.R0()
    c = G.setdefault("corr", corr_labels())
    idx = R.index
    a = idx.searchsorted(pd.Timestamp("2014-01-01"))
    cand = np.arange(a, len(idx) - BLOCK)
    lab = c.to_numpy()[cand]
    if regime == "正相关":
        cand = cand[lab > 0]
    elif regime == "负相关":
        cand = cand[lab <= 0]
    rng = np.random.default_rng(seed)
    nb = (PLEN + WARM) // BLOCK + 1
    picks = rng.choice(cand, size=nb)
    rows_i = np.concatenate([np.arange(p, p + BLOCK) for p in picks])[: PLEN + WARM]
    Rs = R.iloc[rows_i].copy()
    Rs["sh588000"] = np.nan        # 科创50 2020 才有，合成路径里不用
    Rs.index = pd.bdate_range("2030-01-01", periods=len(Rs))
    m_last = float(X.K.erp_mult()[0].iloc[-1])
    mult = pd.Series(m_last, index=Rs.index)
    out = []
    for kind, tw in KINDS:
        d = X.core(Rs, kind=kind, tweak=tw, start=WARM, mult=mult if kind == "C3" else None)
        out.append(dict(regime=regime, seed=seed, kind=kind, tweak=tw, **X.perf(d["ret"][WARM:])))
    return out


def part_boot():
    tasks = [(rg, 10000 * k + s) for k, rg in enumerate(["全部", "正相关", "负相关"]) for s in range(NPATH)]
    rows = []
    with Pool(6, initializer=setup) as p:
        for k, r in enumerate(p.imap_unordered(task_boot, tasks, chunksize=4)):
            rows += r
    df = pd.DataFrame(rows)
    df.to_csv(RUN / "bootstrap_paths.csv.gz", index=False)
    s = df.groupby(["regime", "kind", "tweak"], sort=False).agg(
        cagr_med=("cagr", "median"), cagr_p5=("cagr", lambda x: x.quantile(0.05)), mdd_med=("mdd", "median"),
        mdd_p5=("mdd", lambda x: x.quantile(0.05)), calmar_med=("calmar", "median"), p_loss=("cagr", lambda x: (x < 0).mean())).reset_index()
    s.to_csv(RUN / "bootstrap_summary.csv", index=False)
    c = corr_labels()
    R = X.R0()
    a = R.index.searchsorted(pd.Timestamp("2014-01-01"))
    info = dict(pos_share=float((c.iloc[a:] > 0).mean()), corr_last=float(c.iloc[-1]), corr_last_date=str(R.index[-1].date()))
    # 历史上正相关日子里底仓的年化（直接切片，非 bootstrap）
    S = series_map()
    lab = (c > 0).to_numpy()
    for nm in ("DIV-IV", "C3", "C3 60%+(S4+CREDIT)"):
        r = np.asarray(S[nm]["ret"])
        for k, m in (("正相关日", lab), ("负相关日", ~lab)):
            mm = m.copy(); mm[:a] = False
            rr = r[mm]
            info[f"{nm}_{k}_年化"] = float(np.prod(1 + rr) ** (252 / len(rr)) - 1)
    json.dump(info, open(RUN / "bootstrap_info.json", "w"), ensure_ascii=False, indent=1)
    return s, info


# ------------------------------------------------------------------ (d) 底仓改动：滚动起点
def part_tweaks():
    C = G["C"]
    rows = []
    for cost in (0.0005, 0.002):
        base = X.core(kind="C3", cost=cost)
        rb = C.roll_cal(base["ret"]); sb = C.seg_cal(base["ret"])
        cb = combo(base, cost); rcb = C.roll_cal(cb["ret"]); scb = C.seg_cal(cb["ret"])
        for kind, tw in KINDS:
            d = X.core(kind=kind, tweak=tw, cost=cost)
            g = C.gate1(d["ret"], base["ret"], rb, sb)
            lo = C.starts()[0]
            W = pd.DataFrame(d["W"][lo:], columns=d["cols"]).T.groupby(X.SLEEVE).sum().T.mean()
            rows.append(dict(arm=f"{kind}{'' if tw == 'none' else '+' + tw}", base="C3", cost_bp=int(cost * 1e4),
                             **C.full(d["ret"]), **g, **{f"均权_{k}": float(v) for k, v in W.items()}))
            if kind == "C3":
                c2 = combo(d, cost)
                g2 = C.gate1(c2["ret"], cb["ret"], rcb, scb)
                rows.append(dict(arm=f"C3{'' if tw == 'none' else '+' + tw} 60%+(S4+CREDIT)", base="C3 60%+(S4+CREDIT)",
                                 cost_bp=int(cost * 1e4), **C.full(c2["ret"]), **g2))
    t = pd.DataFrame(rows)
    t.to_csv(RUN / "tweaks_rolling.csv", index=False)
    return t


# ------------------------------------------------------------------ 前瞻
def part_forward():
    dur = G["dur"]
    pe = pd.read_csv(X.ROOT / "data/cache_components/000300.csv", parse_dates=["date"]).dropna(subset=["pe"]).iloc[-1]
    t = open(X.CS / "multpl_sp500_ey.html", encoding="utf-8", errors="ignore").read()
    m = re.findall(r"<td>([^<]+)</td>\s*<td>\s*(?:&#x2002;)?\s*([0-9.]+)%", t)
    us_date, us_ey = m[0][0], float(m[0][1]) / 100
    E = {"国债": dur["y5_last"] / 100, "短债": dur["y2_last"] / 100, "A股": 1 / float(pe.pe), "美股": us_ey, "黄金": 0.0}
    rows = []
    idx = X.R0().index
    for nm in ("DIV-IV", "C3"):
        for tw in ("none", "BCAP50", "SD50"):
            if nm == "DIV-IV" and tw != "none":
                continue
            d = X.core(kind=nm, tweak=tw)
            w = pd.Series(d["W"][-1], index=d["cols"]).groupby(X.SLEEVE).sum()
            wl = pd.Series(d["W"][d["lo"]:].mean(0), index=d["cols"]).groupby(X.SLEEVE).sum()
            er = float(sum(w.get(k, 0) * v for k, v in E.items()))
            erl = float(sum(wl.get(k, 0) * v for k, v in E.items()))
            rows.append(dict(series=nm, tweak=tw, exp_ret_latest_w=er, exp_ret_avg_w=erl,
                             **{f"w_{k}": float(v) for k, v in w.items()}))
    hist = {}
    R = X.R0()
    lo = X.s0()
    for a in ("sh511010", "sh511360", "sh518880", "sh513100", "sh513500", "sh510300", "sh510500"):
        r = R[a].iloc[lo:].dropna()
        hist[a] = float((1 + r).prod() ** (252 / len(r)) - 1)
    info = dict(E=E, pe300=float(pe.pe), pe300_date=str(pe.date.date()), us_ey_date=us_date, y_date=dur["y_last_date"],
                hist_cagr_2013=hist, dur=dur)
    pd.DataFrame(rows).to_csv(RUN / "forward_expected.csv", index=False)
    json.dump(info, open(RUN / "forward_info.json", "w"), ensure_ascii=False, indent=1)
    return pd.DataFrame(rows), info


if __name__ == "__main__":
    setup()
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    parts = sys.argv[1:] or ["episodes", "decomp", "lowcarry", "tweaks", "forward", "shocks", "boot"]
    json.dump(G["dur"], open(RUN / "durations.json", "w"), ensure_ascii=False, indent=1)
    for p_ in parts:
        log("part", p_)
        out = {"episodes": part_episodes, "decomp": part_decomp, "lowcarry": part_lowcarry, "tweaks": part_tweaks,
               "forward": part_forward, "shocks": part_shocks, "boot": part_boot}[p_]()
        if isinstance(out, tuple):
            for o in out:
                print(o if not isinstance(o, pd.DataFrame) else o.round(4).to_string())
        else:
            print(out.round(4).to_string())
