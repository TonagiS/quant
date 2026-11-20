#!/usr/bin/env python3
"""分析师盈利预测修正 × ETF 轮动回测（2026-11-21 analyst_revision）。
口径与 components 一致：PIT 池（月末成交额前40）、总收益、信号滞后 1 日、单边 5/10/20bp、只做多。
修正信号来自 work/ar_signal.py（周五计算、只用发布日 < 当日的研报，再 ffill 到交易日并整体再滞后 1 个交易日）。
用户授权本轮允许调参、不考虑过拟合；结果仅作探索。"""
from __future__ import annotations
import json, sys
from dataclasses import replace
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import components_core as K  # noqa: E402
import ar_signal as S  # noqa: E402

RC, PB, E = K.RC, K.PB, K.E
ROOT = K.ROOT
RUN = ROOT / "runs" / "2026-11-21_analyst_revision"
WINDOWS = {"W2017H2": "2017-07-03", "W2019": "2019-01-02", "W2021": "2021-01-04"}
SEGS = {"Y2018": ("2018-01-01", "2018-12-31"), "S2021_24": ("2021-02-18", "2024-02-02"),
        "S2024_now": ("2024-01-01", "2026-11-13")}
_C = {}


def etf_sig(W, key):
    k = (W, key)
    if k in _C:
        return _C[k]
    if "rv" not in _C:
        R = S.load_reports()
        _C["rv"] = S.report_revisions(R)
        D = RC.data()
        _C["H"] = S.load_holdings(D["ever"])
    pk = S.CA / f"sig_stock_W{W}.pkl"
    if pk.exists():
        st = pd.read_pickle(pk)
    else:
        st = S.stock_signal(_C["rv"], W=W)
        pd.to_pickle(st, pk)
    es = S.etf_signal(st, _C["H"], key=key)
    D = RC.data()
    cal = D["cal"]
    es = es.reindex(columns=D["ever"])
    daily = es.reindex(cal.union(es.index)).ffill(limit=10).reindex(cal).shift(1)  # 周五算 → 下一交易日起可用
    _C[k] = daily
    return daily


def sector_cap(rm, cand, cap=2):
    sec = K.sector_map()
    sv = np.array([sec[c] for c in rm.columns])
    rmv, cv = rm.to_numpy(), cand.to_numpy().copy()
    for i in range(len(cv)):
        idx = np.flatnonzero(cv[i] & ~np.isnan(rmv[i]))
        if len(idx) <= cap:
            continue
        cnt = {}
        for j in idx[np.argsort(-rmv[i, idx])]:
            cnt[sv[j]] = cnt.get(sv[j], 0) + 1
            if cnt[sv[j]] > cap:
                cv[i, j] = False
    return pd.DataFrame(cv, index=rm.index, columns=rm.columns)


def run_f(f, cost, vt=True, reb=10):
    D = RC.data()
    cfg = E.Config(k=5, reb=reb, reb_mode="rolling", cost=cost)
    r0 = E.simulate(D["closeT"], f, cfg)
    if vt:
        f2 = dict(f); f2["base_ret"] = r0["ret"]
        rr = E.simulate(D["closeT"], f2, replace(cfg, rule=E.VolTarget(0.15, 60, 40, every=10)))
    else:
        rr = r0
    return dict(ret=np.asarray(rr["ret"]), turn=np.asarray(rr["turn"]), expo=np.asarray(rr["expo"]), lo=f["start"])


def arm(sdate, cost, kind, W=90, key="diff", ma=True, sector=False, vt=True, reb=10, lam=1.0, thr=0.0):
    """kind:
       base   : S2 / S4（sector=True）原样
       rev    : 只按 ETF 修正信号排名（无信号的 ETF 不入选），ma=是否要求 close>MA60
       filter : S2/S4 候选里剔除 修正信号 < thr 的（无信号的不剔除）
       blend  : 得分 = (3×动量均分 + lam×修正百分位)/(3+lam)，无信号记 0.5
    """
    f = K.sat_feats(sdate, 0, False, False)
    valid, rm0 = f["valid"], f["rm"]
    close = RC.data()["closeT"]
    above = close > f["ma60"]
    if kind != "base":
        sig = etf_sig(W, key).reindex(index=close.index, columns=close.columns)
        rk = sig.where(valid).rank(axis=1, pct=True)
    if kind == "base":
        rm, cand = rm0, f["cand"]
    elif kind == "rev":
        rm = rk
        cand = valid & rk.notna() & (above if ma else True)
    elif kind == "filter":
        rm = rm0
        cand = f["cand"] & ~(sig < thr)
    elif kind == "blend":
        rm = (3 * rm0 + lam * rk.fillna(0.5)) / (3 + lam)
        rm = rm.where(rm0.notna())
        cand = valid & rm.notna() & (above if ma else True)
    if sector:
        cand = sector_cap(rm, cand)
    f = dict(f, rm=rm, cand=cand)
    st = pd.Timestamp(sdate)
    enough = (valid.sum(axis=1) >= 8) & rm.notna().any(axis=1) & (close.index >= st)
    f["start"] = int(np.argmax(enough.to_numpy()))
    return run_f(f, cost, vt=vt, reb=reb)


def metrics(d, a=None, b=None):
    cal = RC.data()["cal"]
    msk = None if a is None else RC.seg_mask(a, b)
    return RC.metr(d["ret"], d["lo"], msk, d["turn"], d["expo"])
