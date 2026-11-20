#!/usr/bin/env python3
"""parts_v2：新零件信号 + 叠加到 S4 / C3 60% + S4 40% + 两道关（滚动起点稳健性、同敞口对照）。
规则见 runs/2026-11-21_parts_v2/FREEZE.md。只 import 既有模块，不改冻结文件。样本内。"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slowdecline as S  # noqa: E402

I, K, RC, E, SE = S.I, S.K, S.RC, S.E, S.SE
G, X = S.G, S.X
ROOT = S.ROOT
RUN = ROOT / "runs" / "2026-11-21_parts_v2"
CP = ROOT / "data" / "cache_pv2"
CI = S.CI
WIN = "W2013"           # 回测从 2013-01-04 起，评估从 2014-01 起（预热一年）
WC = 0.6                # 组合底仓比例，固定不调
P = {}                  # 本脚本信号


def _ser_csv(path, dcol, vcol):
    d = pd.read_csv(path, parse_dates=[dcol])
    s = pd.to_numeric(d[vcol], errors="coerce")
    s.index = d[dcol]
    return s.dropna().sort_index()


def _q(s, q, win=252, mp=200):
    return s.rolling(win, min_periods=mp).quantile(q)


def setup():
    if P:
        return
    S.setup()
    D = RC.data()
    cal, close = D["cal"], D["closeT"]
    cols = list(close.columns)
    T = len(cal)
    RT = close.pct_change(fill_method=None)
    valid = G["base_f"][WIN]["valid"]
    h3 = pd.read_csv(ROOT / "data/cache_index_proxy/H00300.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").close.sort_index()
    h3 = h3.reindex(cal.union(h3.index)).ffill().reindex(cal)
    rm3 = h3.pct_change()
    on = lambda ser, fn: S.daily_on(ser, fn)  # noqa: E731  # 外部数据：对齐交易日后再滞后 1 日
    # 1 VIX（美股收盘晚于 A 股，滞后 1 日后才可用）
    vix = _ser_csv(CP / "fred_VIXCLS.csv", "observation_date", "VIXCLS")
    P["VIX80"] = on(vix, lambda s: (s.rolling(5).mean() > _q(s, 0.8)).astype(float))   # 1=偏高
    P["VIX25"] = on(vix, lambda s: (s > 25).astype(float))
    # 2 美债 10 年
    us = _ser_csv(CP / "fred_DGS10.csv", "observation_date", "DGS10")
    P["US10Y50"] = on(us, lambda s: (s - s.shift(60) > 0.50).astype(float))
    P["US10Y30H"] = on(us, lambda s: (s - s.shift(20) > 0.30).astype(float))
    # 3 中债 2 年
    br = pd.read_csv(CI / "ak_bond_rate.csv", parse_dates=["日期"]).set_index("日期").sort_index()
    cn = pd.to_numeric(br["中国国债收益率2年"], errors="coerce").dropna()
    P["CN2Y30"] = on(cn, lambda s: (s - s.shift(60) > 0.30).astype(float))
    P["CN2Y20H"] = on(cn, lambda s: (s - s.shift(20) > 0.20).astype(float))
    # 4 动态动量（Daniel-Moskowitz 熊市×高方差）
    bear = (h3 / h3.shift(504) - 1) < 0
    var126 = rm3.rolling(126, min_periods=100).var()
    hv = var126 > var126.rolling(756, min_periods=252).median()
    P["DM"] = (bear & hv).astype(float).where(h3.shift(504).notna() & var126.notna()).shift(1)
    # 5 截面离散度（池内 20 日收益截面标准差，10 日均）
    m20 = (close / close.shift(20) - 1).where(valid)
    disp = m20.std(axis=1).rolling(10, min_periods=5).mean()
    P["DISP80"] = (disp > _q(disp, 0.8)).astype(float).where(_q(disp, 0.8).notna()).shift(1)
    P["DISP20"] = (disp < _q(disp, 0.2)).astype(float).where(_q(disp, 0.2).notna()).shift(1)
    # 6 波动的波动（沪深300 20 日波动的 60 日变异系数）
    v20 = rm3.rolling(20, min_periods=15).std()
    vov = v20.rolling(60, min_periods=40).std() / v20.rolling(60, min_periods=40).mean()
    P["VOV80"] = (vov > _q(vov, 0.8)).astype(float).where(_q(vov, 0.8).notna()).shift(1)
    # 10 北向（仅信息：数据 2014-11-17 → 2024-08-16，之后恒开，不可实盘）
    nb = pd.read_csv(CP / "ak_northbound.csv", parse_dates=["日期"]).set_index("日期")["当日成交净买额"]
    nb = pd.to_numeric(nb, errors="coerce").dropna().sort_index()
    nb20 = nb.rolling(20, min_periods=15).sum()
    nbs = (nb20 < 0).astype(float).where(nb20.notna())
    nbs = nbs.reindex(cal).shift(1)          # 只在有数据的交易日取值，之后为 NaN→恒开
    P["NB20"] = nbs
    P["NB_info"] = dict(first=str(nb.index[0].date()), last=str(nb.index[-1].date()))
    for k in ["VIX80", "VIX25", "US10Y50", "US10Y30H", "CN2Y30", "CN2Y20H", "DM", "DISP80", "DISP20", "VOV80", "NB20"]:
        P[k] = pd.Series(P[k]).reindex(cal).fillna(0.0).to_numpy()   # 1=触发（减仓）；缺失=不触发
    # 7 特质波动（60 日，对沪深300 滚动 252 日 beta）
    Y = pd.DataFrame(np.repeat(rm3.to_numpy()[:, None], len(cols), 1), index=cal, columns=cols).where(RT.notna())
    Xr = RT.where(Y.notna())
    rl = dict(window=252, min_periods=120)
    cov = (Xr * Y).rolling(**rl).mean() - Xr.rolling(**rl).mean() * Y.rolling(**rl).mean()
    var = (Y * Y).rolling(**rl).mean() - Y.rolling(**rl).mean() ** 2
    beta = cov / var
    eps = RT - beta.mul(rm3, axis=0)
    ivol = eps.rolling(60, min_periods=40).std()
    P["ivol_pct"] = ivol.where(valid).rank(axis=1, pct=True)
    # 8 隔夜跳空（不复权开/收；|跳空|>11% 视为除权/异常剔除）
    op, pc = {}, {}
    for c in cols:
        for d_ in (ROOT / "data/cache_ohlc_fullhist_raw", ROOT / "data/cache_ohlc_raw_2017", ROOT / "data/cache_ohlc_raw_extra"):
            p = d_ / f"{c}.csv"
            if p.exists():
                x = pd.read_csv(p, parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
                op[c], pc[c] = x["open"], x["close"]
                break
    OP = pd.DataFrame(op).reindex(cal)[cols]
    CL = pd.DataFrame(pc).reindex(cal)[cols]
    gap = np.log(OP / CL.shift(1))
    gap = gap.where(gap.abs() <= 0.11)
    P["gap_pct"] = gap.abs().rolling(10, min_periods=8).sum().where(valid).rank(axis=1, pct=True)
    P["onm_rank"] = gap.rolling(20, min_periods=15).sum().where(valid).rank(axis=1, pct=True)
    P["cal"], P["T"] = cal, T
    P["h3"] = h3


# ------------------------------------------------------------------ 零件定义
# kind: ov（卫星乘数）、sel（选股剔除/打分）、core（底仓）
OV = {"VIX80": ("VIX80", 0.5), "VIX25": ("VIX25", 0.0), "US10Y50": ("US10Y50", 0.0), "US10Y30H": ("US10Y30H", 0.5),
      "CN2Y30": ("CN2Y30", 0.0), "CN2Y20H": ("CN2Y20H", 0.5), "DM_H": ("DM", 0.5), "DM_0": ("DM", 0.0),
      "DISP80H": ("DISP80", 0.5), "DISP20H": ("DISP20", 0.5), "VOV80H": ("VOV80", 0.5), "VOV80_0": ("VOV80", 0.0),
      "NB20H": ("NB20", 0.5)}
SEL = {"IVOL20": ("ivol", 0.8), "IVOL30": ("ivol", 0.7), "GAP20": ("gap", 0.8), "ONM": ("onm", None)}
CORE = {"CTS252": 252, "CTS126": 126}
COMP = {"VIX": ["VIX80", "VIX25"], "US10Y": ["US10Y50", "US10Y30H"], "CN2Y": ["CN2Y30", "CN2Y20H"],
        "DM": ["DM_H", "DM_0"], "DISP": ["DISP80H", "DISP20H"], "VOV": ["VOV80H", "VOV80_0"],
        "IVOL": ["IVOL20", "IVOL30"], "GAP": ["GAP20", "ONM"], "CTS": ["CTS252", "CTS126"], "NB": ["NB20H"]}
PRIMARY = {"VIX": "VIX80", "US10Y": "US10Y50", "CN2Y": "CN2Y30", "DM": "DM_H", "DISP": "DISP80H", "VOV": "VOV80H",
           "IVOL": "IVOL20", "GAP": "GAP20", "CTS": "CTS252", "NB": "NB20H"}


def ov_mult(arm):
    k, lv = OV[arm]
    return np.where(P[k] > 0.5, lv, 1.0)


def sel_mask(arm, rng=None):
    """返回 (valid 形状 bool DataFrame 允许集, 新打分或 None)。rng 给定时：每日随机剔除与真实规则相同个数的 valid 名字。"""
    b = G["base_f"][WIN]
    valid = b["valid"]
    kind, thr = SEL[arm]
    if kind == "onm":
        sc = (b["rm"] + P["onm_rank"]) / 2
        if rng is not None:   # 对照：把隔夜动量排名换成每日随机排名
            rnd = pd.DataFrame(rng.random(valid.shape), index=valid.index, columns=valid.columns).where(valid).rank(axis=1, pct=True)
            sc = (b["rm"] + rnd) / 2
        return None, sc.where(valid)
    pct = P["ivol_pct"] if kind == "ivol" else P["gap_pct"]
    excl = (pct > thr) & valid
    if rng is None:
        return ~excl, None
    V = valid.to_numpy()
    n_ex = excl.to_numpy().sum(axis=1)
    allow = np.ones(V.shape, bool)
    for i in np.flatnonzero(n_ex):
        idx = np.flatnonzero(V[i])
        allow[i, rng.choice(idx, size=n_ex[i], replace=False)] = False
    return pd.DataFrame(allow, index=valid.index, columns=valid.columns), None


def sat_run(cost, sels=(), mult=None, credit=False, rng=None):
    """S4（+可选选股零件）→ VT15 → 乘数（社融/零件）。与 slowdecline.sat 同一流程。"""
    D = RC.data()
    f = I.feats(WIN, True, "S2", ())
    if sels:
        b = G["base_f"][WIN]
        valid = b["valid"]
        allow = pd.DataFrame(True, index=valid.index, columns=valid.columns)
        sc = b["rm"]
        for a in sels:
            al, s2 = sel_mask(a, rng)
            if al is not None:
                allow &= al
            if s2 is not None:
                sc = s2
        cand = valid & sc.notna() & G["ma_ok"] & allow
        cv = I.cap2(sc.where(valid).to_numpy(), cand.to_numpy())
        f["rm"] = sc.where(valid)
        f["cand"] = pd.DataFrame(cv, index=valid.index, columns=valid.columns)
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    r0 = E.simulate(D["closeT"], f, cfg)
    f2 = dict(f); f2["base_ret"] = r0["ret"]
    rr = E.simulate(D["closeT"], f2, replace(cfg, rule=E.VolTarget(0.15, 60, 40, every=10)))
    d = dict(ret=np.asarray(rr["ret"]), turn=np.asarray(rr["turn"]), expo=np.asarray(rr["expo"]), lo=f["start"])
    m = np.ones(len(d["ret"]))
    if credit:
        m = m * G["credit"]
    if mult is not None:
        m = m * mult
    if credit or mult is not None:
        d = K.apply_mult(d, m, cost)
    return d


# ------------------------------------------------------------------ 底仓
_CORE = {}


def core_onoff(L):
    """每个权益资产：月末 L 日收益 <0 → 关（权重转国债）。返回 cal×EQ8 的 0/1 DataFrame。"""
    R = K.core_returns()[K.BASE8]
    nav = (1 + R.fillna(0)).cumprod()
    ok = R.notna().cumsum() >= L
    on = ((nav / nav.shift(L) - 1) >= 0) | ~ok
    return on[K.EQ8].astype(float)


def core_run(cost, L=None, onoff=None, const=None):
    """C3 底仓；L/onoff 给定时叠加时序动量（关掉的权益资产权重转国债）；const：每资产常数乘数（同平均敞口对照）。"""
    lo = G["base_f"][WIN]["start"]
    plain = L is None and onoff is None and const is None
    if plain and cost in _CORE:
        return _CORE[cost]
    R = K.core_returns()
    tg, _ = K.core_targets(R, lo, mult=K.erp_mult()[0])
    if L is not None and onoff is None:
        onoff = core_onoff(L)
    if onoff is not None or const is not None:
        tg2 = {}
        for s, w in tg.items():
            w = w.copy()
            for c in K.EQ8:
                if c in w.index:
                    f = float(onoff.iat[s, K.EQ8.index(c)]) if onoff is not None else float(const[K.EQ8.index(c)])
                    cut = w[c] * (1 - f)
                    w[c] -= cut
                    w[K.BOND] = w.get(K.BOND, 0.0) + cut
            tg2[s] = w
        tg = tg2
    net, turn = RC.PB.sim_targets(R[K.BASE8], tg, lo, cost)
    out = dict(ret=net, turn=turn, expo=np.ones(len(net)), lo=lo, tg=tg)
    if plain:
        _CORE[cost] = out
    return out


def combo(core, sat, cost):
    return RC.mix(core, sat, WC, sat["lo"], cost)


# ------------------------------------------------------------------ 评估
def _calmar(r):
    r = r[np.isfinite(r)]
    if len(r) < 20:
        return np.nan
    eq = np.cumprod(1 + r)
    cagr = eq[-1] ** (252 / len(r)) - 1 if eq[-1] > 0 else -1.0
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return cagr / abs(dd) if dd < 0 else np.nan


_ST = {}


def starts():
    if "s" not in _ST:
        cal = P["cal"]
        out = []
        for y in range(2014, 2024):
            for mth in range(1, 13):
                if (y, mth) > (2023, 11):
                    continue
                out.append(int(cal.searchsorted(pd.Timestamp(y, mth, 1))))
        _ST["s"] = np.array(out)
    return _ST["s"]


SEGS4 = {"2014-16": ("2014-01-01", "2016-12-31"), "2017-19": ("2017-01-01", "2019-12-31"),
         "2020-22": ("2020-01-01", "2022-12-31"), "2023-26": ("2023-01-01", "2026-11-13")}


def seg_idx():
    cal = P["cal"]
    return {k: (int(cal.searchsorted(pd.Timestamp(a))), int(cal.searchsorted(pd.Timestamp(b), side="right")))
            for k, (a, b) in SEGS4.items()}


def roll_cal(r):
    r = np.asarray(r)
    return np.array([_calmar(r[a:]) for a in starts()])


def seg_cal(r):
    r = np.asarray(r)
    return {k: _calmar(r[a:b]) for k, (a, b) in seg_idx().items()}


def gate1(r_arm, r_base, rb_roll=None, rb_seg=None):
    ra = roll_cal(r_arm)
    rb = roll_cal(r_base) if rb_roll is None else rb_roll
    d = ra - rb
    sa = seg_cal(r_arm)
    sb = seg_cal(r_base) if rb_seg is None else rb_seg
    sd = {k: sa[k] - sb[k] for k in sa}
    npos = int(sum(v > 0 for v in sd.values()))
    share = float(np.mean(d > 0))
    return dict(share_up=share, med_d=float(np.nanmedian(d)), min_d=float(np.nanmin(d)), max_d=float(np.nanmax(d)),
                n_seg_pos=npos, **{f"seg_{k}": v for k, v in sd.items()},
                pass1=bool(share >= 0.70 and npos >= 3))


def full(r):
    lo = starts()[0]
    m = E.perf(np.asarray(r)[lo:])
    return {k: m[k] for k in ("cagr", "mdd", "calmar", "sharpe", "vol")}
