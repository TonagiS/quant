#!/usr/bin/env python3
"""recombine 公共部分：面板、通用 S2 组件回测、底仓混合。规则见 runs/2026-11-20_recombine/FREEZE.md（已冻结）。"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "work"))
import pit_pool_backtest as PB  # noqa: E402
import tr_ens_data as TD  # noqa: E402
import tr_ens_portfolio as TP  # noqa: E402

E = PB.E
RUN = ROOT / "runs" / "2026-11-20_recombine"
CACHE = ROOT / "data" / "cache_recombine"
END = PB.END
START_W = PB.START_W
SUB_W = PB.SUB_W
CRASH = ["Y2015", "Y2018", "S2021_24"]

H_SETS = {"M60": (60,), "M3": (20, 60, 120), "M4": (20, 60, 120, 250)}
F_SETS = {"none": None, "MA60": 60, "MA120": 120}
N_SETS = (3, 5, 8)
W_SETS = {"EW": "equal", "IV": "invvol"}
R_SETS = (5, 10, 20)
O_SETS = ("none", "VT15", "VT15-TG-300")
C_SETS = (0.0, 0.7, 0.8)

BASE_KEYS = list(itertools.product(H_SETS, F_SETS, N_SETS, W_SETS, R_SETS))   # 162
COMBOS = [b + (o, c) for b in BASE_KEYS for o in O_SETS for c in C_SETS]       # 1458
INCUMBENT = ("M3", "MA60", 5, "IV", 10, "VT15", 0.0)


def combo_name(c):
    h, f, n, w, r, o, core = c
    return f"{h}|{f}|N{n}|{w}|R{r}|{o}|C{int(round(core * 100))}"


_D = {}


def data():
    if _D:
        return _D
    pk = CACHE / "base.pkl"
    if pk.exists():
        _D.update(pd.read_pickle(pk))
        return _D
    P = TD.build()
    cal_all = P["close_tr"].index
    cal = cal_all[(cal_all >= "2010-01-01") & (cal_all <= END)]
    lists, mem_df = PB.pit_members(P)
    ever = sorted(mem_df.code.unique())
    closeT = P["close_tr"].reindex(cal)[ever]
    M = PB.member_mask(lists, cal, ever)
    RT = closeT.pct_change(fill_method=None)
    h3 = pd.read_csv(ROOT / "data/cache_index_proxy/H00300.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").close.sort_index()
    h3c = h3.reindex(cal.union(h3.index)).ffill().reindex(cal)
    g300 = TP.gate_from_level(h3c).to_numpy()
    Rd, first_etf = PB.div_returns(P, cal)
    Apos = PB.div_A(Rd)
    d = dict(cal=cal, lists=lists, ever=ever, closeT=closeT, M=M, RT=RT, g300=g300, Rd=Rd, Apos=Apos,
             mem_df=mem_df)
    pd.to_pickle(d, pk)
    _D.update(d)
    return _D


def feats(close, member, start_date, horizons, ma, min_names=8):
    """同 tr_ens_portfolio.feats，趋势过滤可选 无/MA60/MA120。"""
    ret = close.pct_change(fill_method=None)
    vol20 = ret.rolling(20, min_periods=15).std()
    mom = {n: close / close.shift(n) - 1.0 for n in horizons}
    ma60 = close.rolling(60, min_periods=60).mean()
    bars = close.notna().cumsum()
    valid = (bars >= 150) & close.notna() & vol20.notna() & member
    for n in horizons:
        if n >= 120:
            valid &= mom[n].notna()
    rk = {n: mom[n].where(valid).rank(axis=1, pct=True) for n in horizons}
    rm = sum(rk[n] for n in horizons) / float(len(horizons))
    cand = valid & rm.notna()
    if ma is not None:
        mav = ma60 if ma == 60 else close.rolling(ma, min_periods=ma).mean()
        cand &= close > mav
    enough = (valid.sum(axis=1) >= min_names) & rm.notna().any(axis=1)
    enough &= close.index >= pd.Timestamp(start_date)
    start = int(np.argmax(enough.to_numpy()))
    return {"ret": ret, "vol20": vol20, "mom": mom, "ma60": ma60, "valid": valid, "rm": rm,
            "cand": cand, "start": start, "bench": (1.0 + ret.mean(axis=1).fillna(0.0)).cumprod()}


def run_base(bk, sdate, cost, overlays=O_SETS):
    """一个 S2 基础配置 → {overlay: dict(ret, turn, expo ndarray, lo)}。VT 的 base_ret 复用 'none' 的结果（与 run_with_base 等价）。"""
    D = data()
    h, f, n, w, r = bk
    fe = feats(D["closeT"], D["M"], sdate, H_SETS[h], F_SETS[f])
    cfg = E.Config(k=n, reb=r, reb_mode="rolling", cost=cost, weight_mode=W_SETS[w])
    r0 = E.simulate(D["closeT"], fe, cfg)
    out = {"none": dict(ret=r0["ret"], turn=r0["turn"], expo=r0["expo"], lo=fe["start"])}
    f2 = dict(fe)
    f2["base_ret"] = r0["ret"]
    for o in overlays:
        if o == "none":
            continue
        if o == "VT15":
            rule = E.VolTarget(0.15, 60, 40, every=r)
        else:
            rule = TP.GateVT(D["g300"], every=r)
        from dataclasses import replace
        rr = E.simulate(D["closeT"], f2, replace(cfg, rule=rule))
        out[o] = dict(ret=rr["ret"], turn=rr["turn"], expo=rr["expo"], lo=fe["start"])
    return out


_DIV = {}


def div_ret(lo, cost):
    key = (lo, cost)
    if key not in _DIV:
        D = data()
        tg, _ = PB.div_targets(D["Rd"], D["Apos"], lo, "IV")
        net, turn = PB.sim_targets(D["Rd"], tg, lo, cost)
        _DIV[key] = dict(ret=net, turn=turn, expo=np.ones(len(net)), lo=lo)
    return _DIV[key]


def mix(core, sat, wc, lo, cost):
    """底仓 wc + 卫星 1-wc，月末信号、下一交易日收盘调回（sim_targets 约定），两腿间换手扣成本。"""
    if wc <= 0:
        return sat
    D = data()
    cal = D["cal"]
    R = pd.DataFrame({"core": core["ret"], "sat": sat["ret"]}, index=cal)
    tg = {s: pd.Series({"core": wc, "sat": 1 - wc}) for s in PB.month_end_pos(cal, lo)}
    net, turn = PB.sim_targets(R, tg, lo, cost)
    # 平均敞口近似：wc*1 + (1-wc)*卫星敞口
    expo = wc + (1 - wc) * np.asarray(sat["expo"])
    return dict(ret=net, turn=turn + 0.0, expo=expo, lo=lo, turn_sleeve=turn)


def metr(ret, lo, hi_mask=None, turn=None, expo=None):
    D = data()
    cal = D["cal"]
    m = np.zeros(len(cal), bool)
    m[lo:] = True
    if hi_mask is not None:
        m &= hi_mask
    return PB.metr(np.asarray(ret)[m], None if turn is None else np.asarray(turn)[m],
                   None if expo is None else np.asarray(expo)[m])


def seg_mask(a, b):
    cal = data()["cal"]
    return (cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b))
