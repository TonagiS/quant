#!/usr/bin/env python3
"""PIT 规则池 S2/VT15 + 不选股分散臂回测。规则见 runs/2026-11-20_pit_pool_diversified/PREREG.md（已冻结）。"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "position")); sys.path.insert(0, str(ROOT / "indicators" / "code"))
sys.path.insert(0, str(ROOT / "work"))
sys.modules.setdefault("akshare", types.ModuleType("akshare"))
import engine as E  # noqa: E402
import pit_pool_data as D  # noqa: E402
import layered_v1_backtest as L  # noqa: E402

RUN = ROOT / "runs" / "2026-11-20_pit_pool_diversified"
END = pd.Timestamp("2026-11-13")
COST = 0.0005
TOPN, MIN_BARS, AMT_WIN, AMT_MIN = 40, 250, 60, 40
START_W = {"W2013": "2013-01-04", "W2017": "2017-01-03", "W2019": "2019-01-02"}
SUB_W = {"Y2015": ("2015-01-01", "2015-12-31"), "Y2018": ("2018-01-01", "2018-12-31"),
         "S2021_24": ("2021-02-18", "2024-02-02"), "S2023_now": ("2023-01-01", "2026-11-13")}
DIV = [  # code, label, proxy(kind, path, col), equity?
    ("sh510300", "沪深300", ("cs", "data/cache_index_proxy/H00300.csv", "close"), True),
    ("sh510500", "中证500", ("cs", "data/cache_index_proxy/H00905.csv", "close"), True),
    ("sz159915", "创业板", ("cs", "data/cache_div_tech/sz399606.csv", "close"), True),
    ("sh588000", "科创50", ("cs", "data/cache_div_tech/000688.csv", "close"), True),
    ("sh511010", "国债", ("cs", "data/cache_div_tech/H11001.csv", "close"), False),
    ("sh518880", "黄金", None, False),
    ("sh513100", "纳指", ("us", "data/cache_cross_market/QQQ.csv", "adjclose"), True),
    ("sh513500", "标普", ("us", "data/cache_cross_market/SP500TR.csv", "adjclose"), True),
]
BOND = "sh511010"


def log(m):
    print(m, flush=True)


# ------------------------------------------------------------------ PIT 池
def pit_members(P):
    cat = P["cat"]
    C, A = P["close_tr"], P["amount"]
    elig = [c for c in C.columns if cat.get(c) not in ("货币", "债券")]
    C, A = C[elig], A.reindex(columns=elig)
    A = A.reindex(C.index)
    bars = C.notna().cumsum()
    a60 = A.rolling(AMT_WIN, min_periods=AMT_MIN).mean()
    idx = C.index
    me = idx.to_series().groupby(idx.to_period("M")).max()
    me = me[(me >= pd.Timestamp("2011-01-01")) & (me <= END)]
    rows, lists = [], {}
    for d in me:
        ok = (bars.loc[d] >= MIN_BARS) & C.loc[d].notna()
        s = a60.loc[d][ok].dropna().sort_values(ascending=False)
        top = list(s.index[:TOPN])
        lists[d] = top
        for r, c in enumerate(top, 1):
            rows.append({"month_end": d.date(), "rank": r, "code": c, "avg_amount_60d": float(s[c]),
                         "n_eligible": int(ok.sum())})
    return lists, pd.DataFrame(rows)


def member_mask(lists, index, cols):
    """成员资格：月末 d 收盘确定，自 d 之后的下一个交易日起生效。"""
    M = pd.DataFrame(False, index=index, columns=cols)
    mes = sorted(lists)
    for k, d in enumerate(mes):
        lo = index.searchsorted(d, side="right")
        hi = index.searchsorted(mes[k + 1], side="right") if k + 1 < len(mes) else len(index)
        cs = [c for c in lists[d] if c in M.columns]
        M.iloc[lo:hi, [M.columns.get_loc(c) for c in cs]] = True
    return M


def features_pit(close, member, start_date, min_names=8):
    """与 engine.features 相同，只把 valid 与'当日在池'相与；start 取窗口起点。"""
    ret = close.pct_change(fill_method=None)
    vol20 = ret.rolling(20, min_periods=15).std()
    mom = {n: close / close.shift(n) - 1.0 for n in (20, 60, 120)}
    ma60 = close.rolling(60, min_periods=60).mean()
    bars = close.notna().cumsum()
    valid = (bars >= 150) & close.notna() & mom[120].notna() & vol20.notna() & member
    rk = {n: mom[n].where(valid).rank(axis=1, pct=True) for n in (20, 60, 120)}
    rm = (rk[20] + rk[60] + rk[120]) / 3.0
    cand = valid & rm.notna() & (close > ma60)
    enough = (valid.sum(axis=1) >= min_names) & rm.notna().any(axis=1)
    enough &= close.index >= pd.Timestamp(start_date)
    start = int(np.argmax(enough.to_numpy()))
    return {"ret": ret, "vol20": vol20, "mom": mom, "ma60": ma60, "valid": valid, "rm": rm,
            "cand": cand, "start": start, "bench": (1.0 + ret.mean(axis=1).fillna(0.0)).cumprod()}


def pit_engine(close, f, arm, cost=COST, every=None):
    if arm == "PIT-S2":
        cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
        return E.simulate(close, f, cfg)
    reb = 10 if arm == "PIT-VT15" else 5
    ev = reb if every is None else every
    cfg = E.Config(k=5, reb=reb, reb_mode="rolling", cost=cost,
                   rule=E.VolTarget(0.15, 60, 40, every=ev))
    return E.run_with_base(close, f, cfg)


# ------------------------------------------------------------------ 目标权重模拟器
def sim_targets(R, targets, s0, cost=COST):
    """R: 日收益 DataFrame；targets: {信号日位置 s: Series 权重}；s 收盘出信号，s+1 收盘成交，s+2 起计收益。
    返回 (净收益 ndarray, 换手 ndarray, 权益敞口路径 None)。"""
    Rv = np.nan_to_num(R.to_numpy(), nan=0.0)
    T, N = Rv.shape
    col = {c: k for k, c in enumerate(R.columns)}
    ex = {}
    for s, w in targets.items():
        if s + 1 < T:
            v = np.zeros(N)
            for c, x in w.items():
                v[col[c]] = x
            ex[s + 1] = v
    w = np.zeros(N)
    net, turn = np.zeros(T), np.zeros(T)
    for t in range(s0, T):
        g = float(w @ Rv[t])
        if w.sum() > 0:
            w = w * (1 + Rv[t]) / (1 + g)
        c = 0.0
        if t in ex:
            tv = ex[t]
            tr = float(np.abs(tv - w).sum())
            c = cost * tr
            turn[t] = tr
            w = tv.copy()
        net[t] = g - c
    return net, turn


def month_end_pos(index, s0):
    me = index.to_series().groupby(index.to_period("M")).max()
    pos = [index.get_loc(d) for d in me if index.get_loc(d) >= s0]
    return [s0] + [p for p in pos if p > s0]


# ------------------------------------------------------------------ 分散臂
def div_returns(P, cal):
    out, first_etf = {}, {}
    for code, lab, prox, eq in DIV:
        c = P["close_tr"][code].dropna()
        r_etf = c.pct_change().iloc[1:]
        r_etf = r_etf.iloc[20:]          # 上市后第 21 个交易日起
        r = pd.Series(np.nan, index=cal)
        if prox is not None:
            kind, path, colname = prox
            d = pd.read_csv(ROOT / path, parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
            px = d[colname].astype(float)
            px = px[px > 0]
            pr = px.reindex(cal.union(px.index)).ffill().reindex(cal)
            pr = pr[(pr.index >= px.index.min()) & (pr.index <= px.index.max())]
            r.loc[pr.index] = pr.pct_change()
        r = r.where(~r.index.isin(r_etf.index), np.nan)
        r.loc[r_etf.index.intersection(cal)] = r_etf.reindex(r_etf.index.intersection(cal))
        # 代理在 ETF 起用日之后的值不再使用
        if len(r_etf):
            r[(r.index >= r_etf.index[0]) & ~r.index.isin(r_etf.index)] = 0.0
        out[code] = r
        first_etf[code] = str(r_etf.index[0].date()) if len(r_etf) else None
    R = pd.DataFrame(out)
    return R, first_etf


def div_A(R):
    pos = {}
    for code, lab, prox, eq in DIV:
        if not eq:
            continue
        r = R[code]
        fv = r.first_valid_index()
        rr = r.loc[fv:].fillna(0.0)
        close = (1 + rr).cumprod()
        F = L.slim_factors(pd.DataFrame({"close": close, "amount": np.nan}))
        p = L.policy_baseline(F)[0]
        pos[code] = p.reindex(R.index)
    return pd.DataFrame(pos)


def div_targets(R, Apos, s0, mode, k_static=None):
    """mode: IV / EW / IV-A / IV-static(k)。返回 targets, eq_ratio(每信号日 权益实际/权益预算)."""
    nobs = R.notna().cumsum()
    vol60 = R.rolling(60, min_periods=60).std()
    me = set(month_end_pos(R.index, s0))
    T = len(R)
    targets, ratio = {}, {}
    W = None
    prev = None
    eqc = [c for c, _, _, e in DIV if e]
    for s in range(s0, T):
        if s in me:
            avail = [c for c in R.columns if nobs.iat[s, R.columns.get_loc(c)] >= 60 and np.isfinite(vol60.iat[s, R.columns.get_loc(c)])]
            if mode == "EW":
                W = pd.Series(1.0 / len(avail), index=avail)
            else:
                iv = 1.0 / vol60.iloc[s][avail].clip(lower=1e-6)
                W = iv / iv.sum()
        if W is None:
            continue
        if mode in ("IV", "EW"):
            if s in me:
                targets[s] = W.copy()
            continue
        w = W.copy()
        eqb = [c for c in w.index if c in eqc]
        budget = float(w[eqb].sum())
        if mode == "IV-A":
            p = Apos.iloc[s][eqb].fillna(1.0).clip(0, 1)
        else:
            p = pd.Series(k_static, index=eqb)
        rel = float((w[eqb] * p).sum())
        ratio[s] = rel / budget if budget > 0 else np.nan
        w[eqb] = w[eqb] * p
        w[BOND] = w.get(BOND, 0.0) + (budget - rel)
        if prev is None or (w.reindex(prev.index.union(w.index), fill_value=0) -
                            prev.reindex(prev.index.union(w.index), fill_value=0)).abs().max() > 1e-9 or s in me:
            if mode == "IV-A" or s in me:
                targets[s] = w
                prev = w
    return targets, pd.Series(ratio)


# ------------------------------------------------------------------ 指标
def metr(r, turn=None, expo=None):
    m = E.perf(np.asarray(r, float))
    n = len(r)
    m["turn_two_way"] = float(np.sum(turn) / (n / E.TD)) if turn is not None else np.nan
    m["avg_expo"] = float(np.mean(expo)) if expo is not None else np.nan
    return m


def main():
    P = D.build()
    cal_all = P["close_tr"].index
    cal = cal_all[(cal_all >= "2010-01-01") & (cal_all <= END)]
    lists, mem_df = pit_members(P)
    mem_df.to_csv(RUN / "pit_pool_members.csv", index=False)
    ever = sorted(mem_df.code.unique())
    log(f"PIT ever members {len(ever)}")
    closeT = P["close_tr"].reindex(cal)[ever]
    closeN = P["close_naive"].reindex(cal)[ever]
    M = member_mask(lists, cal, ever)

    # PIT-EW 收益（总收益）
    R_pit = closeT.pct_change(fill_method=None)

    # 分散臂收益
    Rd, first_etf = div_returns(P, cal)
    Apos = div_A(Rd)
    # 宽 92 等权、沪深300全收益
    fh = sorted(p.stem for p in D.FH.glob("*.csv"))
    r92 = P["close_tr"].reindex(cal)[fh].pct_change(fill_method=None).mean(axis=1).fillna(0.0)
    h3 = pd.read_csv(ROOT / "data/cache_index_proxy/H00300.csv", parse_dates=["date"]).set_index("date").close
    r300 = h3.reindex(cal).pct_change()
    r300_end = h3.index.max()

    series = {}   # (window, arm) -> dict(ret Series, turn, expo)
    rows = []
    cost_rows = []
    sens_rows = []

    def window_run(wname, sdate, cost=COST, which="main"):
        out = {}
        s_eng = None
        for arm in ("PIT-S2", "PIT-VT15", "PIT-VT15-5d"):
            if which == "naive":
                f = features_pit(closeN, M, sdate)
                res = pit_engine(closeN, f, arm, cost)
            else:
                f = features_pit(closeT, M, sdate)
                res = pit_engine(closeT, f, arm, cost)
            lo = f["start"]
            s_eng = lo
            out[arm] = dict(ret=pd.Series(res["ret"], index=cal), turn=pd.Series(res["turn"], index=cal),
                            expo=pd.Series(res["expo"], index=cal), lo=lo)
            if which == "main" and arm == "PIT-VT15" and cost == COST:
                f2 = features_pit(closeT, M, sdate)
                r1 = pit_engine(closeT, f2, arm, cost, every=1)
                out["PIT-VT15-every1"] = dict(ret=pd.Series(r1["ret"], index=cal), turn=pd.Series(r1["turn"], index=cal),
                                              expo=pd.Series(r1["expo"], index=cal), lo=lo)
        lo = s_eng
        # PIT-EW：s0 用当期成员（上个月末名单），之后每月末用当日名单
        Rsrc = closeN.pct_change(fill_method=None) if which == "naive" else R_pit
        tg = {}
        for s in month_end_pos(cal, lo):
            d = cal[s]
            mes = [k for k in lists if k <= d]
            names = [c for c in lists[max(mes)] if np.isfinite(Rsrc.iat[s, Rsrc.columns.get_loc(c)]) or True]
            tg[s] = pd.Series(1.0 / len(names), index=names)
        net, turn = sim_targets(Rsrc, tg, lo, cost)
        out["PIT-EW"] = dict(ret=pd.Series(net, index=cal), turn=pd.Series(turn, index=cal),
                             expo=pd.Series(1.0, index=cal), lo=lo)
        return out, lo

    def div_run(lo, cost=COST):
        out = {}
        for mode, nm in (("IV", "DIV-IV"), ("EW", "DIV-EW"), ("IV-A", "DIV-IV-A")):
            tg, ratio = div_targets(Rd, Apos, lo, mode)
            net, turn = sim_targets(Rd, tg, lo, cost)
            out[nm] = dict(ret=pd.Series(net, index=cal), turn=pd.Series(turn, index=cal),
                           expo=pd.Series(1.0, index=cal), lo=lo, ratio=ratio)
        return out

    def static_div(lo, k, cost=COST):
        tg, _ = div_targets(Rd, Apos, lo, "IV-static", k_static=k)
        net, turn = sim_targets(Rd, tg, lo, cost)
        return pd.Series(net, index=cal), pd.Series(turn, index=cal)

    def eval_slice(wname, out, a, b, rows_out, extra=None):
        mask = (cal >= a) & (cal <= b)
        ew = out["PIT-EW"]["ret"][mask]
        for arm, d in out.items():
            r = d["ret"][mask]
            m = metr(r.to_numpy(), d["turn"][mask].to_numpy(), d["expo"][mask].to_numpy())
            rows_out.append({"window": wname, "from": str(cal[mask][0].date()), "to": str(cal[mask][-1].date()),
                             "arm": arm, **m})
            if arm.startswith("PIT-") and arm != "PIT-EW":
                k = float(d["expo"][mask].mean())
                ms = metr((k * ew).to_numpy())
                rows_out.append({"window": wname, "from": str(cal[mask][0].date()), "to": str(cal[mask][-1].date()),
                                 "arm": f"STATIC[{arm}]", "k": k, **ms})
            if arm == "DIV-IV-A":
                rat = d["ratio"]
                rat = rat[(rat.index >= np.searchsorted(cal, pd.Timestamp(a))) & (rat.index <= np.searchsorted(cal, pd.Timestamp(b), side="right") - 1)]
                k = float(rat.mean())
                rs, ts = static_div(out["DIV-IV-A"]["lo"], k)
                ms = metr(rs[mask].to_numpy(), ts[mask].to_numpy())
                rows_out.append({"window": wname, "from": str(cal[mask][0].date()), "to": str(cal[mask][-1].date()),
                                 "arm": "STATIC[DIV-IV-A]", "k": k, **ms})
        m92 = metr(r92[mask].to_numpy())
        rows_out.append({"window": wname, "from": str(cal[mask][0].date()), "to": str(cal[mask][-1].date()),
                         "arm": "B92-EW", **m92})
        m3 = (cal >= a) & (cal <= min(pd.Timestamp(b), r300_end))
        rows_out.append({"window": wname, "from": str(cal[m3][0].date()), "to": str(cal[m3][-1].date()),
                         "arm": "CSI300TR", **metr(r300[m3].fillna(0.0).to_numpy())})

    w2013 = None
    for wname, sdate in START_W.items():
        out, lo = window_run(wname, sdate)
        out.update(div_run(lo))
        log(f"{wname}: engine start {cal[lo].date()}")
        eval_slice(wname, out, cal[lo], END, rows)
        if wname == "W2013":
            w2013 = out
            for sw, (a, b) in SUB_W.items():
                eval_slice(sw, out, pd.Timestamp(a), pd.Timestamp(b), rows)
            navs = pd.DataFrame({k: (1 + v["ret"][lo:]).cumprod() for k, v in out.items()})
            navs["B92-EW"] = (1 + r92[lo:]).cumprod()
            navs.to_csv(RUN / "nav_W2013.csv")
        # 成本敏感性
        for bp in (5, 10, 15, 20, 25):
            c = bp / 1e4
            oc, loc_ = window_run(wname, sdate, cost=c, which="cost")
            for arm in ("PIT-VT15", "PIT-EW"):
                r = oc[arm]["ret"][loc_:]
                cost_rows.append({"window": wname, "cost_bp": bp, "arm": arm,
                                  **metr(r.to_numpy(), oc[arm]["turn"][loc_:].to_numpy(), oc[arm]["expo"][loc_:].to_numpy())})
        # qfq 直接 pct_change 敏感性
        on, lon = window_run(wname, sdate, which="naive")
        for arm in ("PIT-S2", "PIT-VT15", "PIT-VT15-5d", "PIT-EW"):
            r = on[arm]["ret"][lon:]
            sens_rows.append({"window": wname, "variant": "qfq_naive", "arm": arm, **metr(r.to_numpy())})
        r = out["PIT-VT15-every1"]["ret"][lo:]
        sens_rows.append({"window": wname, "variant": "VT_every1", "arm": "PIT-VT15", **metr(r.to_numpy())})

    res = pd.DataFrame(rows)
    res.to_csv(RUN / "results_windows.csv", index=False)
    pd.DataFrame(cost_rows).to_csv(RUN / "cost_sensitivity.csv", index=False)
    pd.DataFrame(sens_rows).to_csv(RUN / "sensitivity.csv", index=False)

    # 判定
    ver = {"S2_windows": {}, "DIV_windows": {}, "DIV_secondary": {}}
    for w in START_W:
        g = res[res.window == w].set_index("arm")
        vt, ew, st = g.loc["PIT-VT15"], g.loc["PIT-EW"], g.loc["STATIC[PIT-VT15]"]
        c1 = bool(vt.calmar > ew.calmar); c2 = bool(vt.calmar > st.calmar); c3 = bool(vt.cagr >= ew.cagr - 0.02)
        ver["S2_windows"][w] = {"calmar_vt>ew": c1, "calmar_vt>static": c2, "cagr_not_below_ew_2pp": c3,
                                "pass": c1 and c2 and c3,
                                "vt": [round(vt.cagr, 4), round(vt.mdd, 4), round(vt.calmar, 3)],
                                "ew": [round(ew.cagr, 4), round(ew.mdd, 4), round(ew.calmar, 3)],
                                "static": [round(st.cagr, 4), round(st.mdd, 4), round(st.calmar, 3), round(st.k, 3)]}
        ver["DIV_windows"][w] = {"pass": bool(g.loc["DIV-IV"].calmar >= vt.calmar),
                                 "div_iv": [round(g.loc["DIV-IV"].cagr, 4), round(g.loc["DIV-IV"].mdd, 4), round(g.loc["DIV-IV"].calmar, 3)]}
        ver["DIV_secondary"][w] = {a: bool(g.loc[a].calmar >= vt.calmar) for a in ("DIV-EW", "DIV-IV-A")}
    ns = sum(v["pass"] for v in ver["S2_windows"].values())
    nd = sum(v["pass"] for v in ver["DIV_windows"].values())
    ver["S2_momentum_selection"] = "kept" if ns >= 2 else "not_kept"
    ver["S2_pass_count"] = ns
    ver["DIV_IV_core"] = "recommended_as_core" if nd >= 2 else "not_recommended"
    ver["DIV_pass_count"] = nd
    ver["div_etf_first_used"] = first_etf
    ver["engine_starts"] = {w: str(res[(res.window == w) & (res.arm == "PIT-VT15")]["from"].iloc[0]) for w in START_W}
    (RUN / "verdict.json").write_text(json.dumps(ver, ensure_ascii=False, indent=2))
    with pd.option_context("display.width", 250, "display.max_rows", 500):
        print(res[["window", "from", "arm", "cagr", "mdd", "calmar", "vol", "avg_expo", "turn_two_way"]].round(3).to_string())
        print(pd.DataFrame(cost_rows)[["window", "cost_bp", "arm", "cagr", "mdd", "calmar"]].round(3).to_string())
        print(pd.DataFrame(sens_rows)[["window", "variant", "arm", "cagr", "mdd", "calmar"]].round(3).to_string())
    print(json.dumps(ver, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
