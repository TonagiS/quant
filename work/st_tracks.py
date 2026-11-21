#!/usr/bin/env python3
"""struct_tracks（2026-11-21）：最简三层、多模型等权持有、成本感知再平衡；
对应层新要素组合，以及按要素作用设计的单要素赛道。规则见
runs/2026-11-21_struct_tracks/FREEZE.md。总收益面板 v2。全部为样本内研究；
时间切分只是伪样本外。用法：python3 work/st_tracks.py check | run | post"""
from __future__ import annotations

import inspect
import itertools
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import c3x_combo as X  # noqa: E402
import fr_rebuild as FR  # noqa: E402

PV, K, RC, E = X.PV, X.K, X.RC, X.E
RUN = PV.ROOT / "runs" / "2026-11-21_struct_tracks"
CACHE = PV.ROOT / "data" / "cache_struct_tracks"
CACHE_FR = PV.ROOT / "data" / "cache_full_rebuild"
COSTS = (0.0005, 0.0020)
SPLIT = {"A": (("2014-06-01", "2019-12-31"), ("2020-01-01", "2026-11-13")),
         "B": (("2020-01-01", "2026-11-13"), ("2014-06-01", "2019-12-31"))}
PASS_SHARE, PASS_MED, PASS_SEG = 0.70, 0.05, 3
N_RND = 20
N_MIX = 50
NEW_POOLS = ["TOG20", "SHR4", "SECROT", "XBCM"]
ALL_POOLS = ["BASE", "P12_HYST60"] + NEW_POOLS
G = {}


def configs():
    """45 个预先指定配置。字段：name, pool, sig, cap, size, reb, reb_mode, dead_k, turn_cap, family。"""
    rows = []

    def add(name, pool, sig, cap, size, family, reb=10, reb_mode="rolling", dead_k=None, turn_cap=None):
        rows.append(dict(name=name, pool=pool, sig=sig, cap=cap, size=size, family=family,
                         reb=reb, reb_mode=reb_mode, dead_k=dead_k, turn_cap=turn_cap))

    add("MIN3_P12", "P12_HYST60", "DONCH", "CAP2", "VT10", "structure")
    add("MIN3_BASE", "BASE", "DONCH", "CAP2", "VT10", "structure")
    add("S4", "BASE", "S2", "CAP2", "VT15", "structure")

    for p in NEW_POOLS:
        add(f"POOL_{p}", p, "DONCH", "CAP2", "VT10", "pool")
    add("POOL_P12_S2VT15", "P12_HYST60", "S2", "CAP2", "VT15", "pool_sens")
    for p in NEW_POOLS:
        add(f"POOL_{p}_S2VT15", p, "S2", "CAP2", "VT15", "pool_sens")

    for s in ("S2", "PB_BO", "VOL_BO", "BB_SQ", "RS_PERS", "TREND_STAB"):
        add(f"SIG_{s}", "P12_HYST60", s, "CAP2", "VT10", "signal")

    add("CAP_NOCAP", "P12_HYST60", "DONCH", "NOCAP", "VT10", "cap")
    add("CAP_SECW40", "P12_HYST60", "DONCH", "SECW40", "VT10", "cap")
    add("CAP_CORRCAP", "P12_HYST60", "DONCH", "CORRCAP", "VT10", "cap")

    for z in ("EQ", "INVVOL", "VT15", "DSVOL10", "CVAR95_15", "K358"):
        add(f"SIZE_{z}", "P12_HYST60", "DONCH", "CAP2", z, "size")

    add("STK_TOG_VOLBO", "TOG20", "VOL_BO", "CAP2", "VT10", "stack")
    add("STK_SECROT_RPERS", "SECROT", "RS_PERS", "CAP2", "VT10", "stack")
    add("STK_PB_SECW", "P12_HYST60", "PB_BO", "SECW40", "VT10", "stack")
    add("STK_CORR_DSVOL", "P12_HYST60", "DONCH", "CORRCAP", "DSVOL10", "stack")
    add("STK_BB_K358", "P12_HYST60", "BB_SQ", "CAP2", "K358", "stack")
    add("STK_SHR_STAB", "SHR4", "TREND_STAB", "CAP2", "VT10", "stack")

    for base, pool, sig, cap, size in (
        ("MIN3", "P12_HYST60", "DONCH", "CAP2", "VT10"),
        ("S4", "BASE", "S2", "CAP2", "VT15"),
    ):
        add(f"COST_{base}_DAILY", pool, sig, cap, size, "cost", reb=1, reb_mode="rolling")
        add(f"COST_{base}_DEAD2", pool, sig, cap, size, "cost", dead_k=2.0)
        add(f"COST_{base}_DEAD4", pool, sig, cap, size, "cost", dead_k=4.0)
        add(f"COST_{base}_SETONLY", pool, sig, cap, size, "cost", reb_mode="rolling_change")
        add(f"COST_{base}_TURNCAP40", pool, sig, cap, size, "cost", turn_cap=0.40)
    return rows


def rnd_configs():
    return [dict(name=f"RND_{i:02d}", pool="P12_HYST60", sig="RND", cap="CAP2", size="VT10",
                 family="rnd", reb=10, reb_mode="rolling", dead_k=None, turn_cap=None, seed=i)
            for i in range(N_RND)]


class DownsideVolTarget(E.Rule):
    """下行波动目标：只用未叠加仓控的负日收益算下行偏差（相对 0），再按目标年化缩放。"""
    requires_base = True

    def __init__(self, target=0.10, window=60, min_periods=40, min_down=15, cap=1.0, every=10, name=None):
        self.target, self.window, self.min_periods = target, window, min_periods
        self.min_down, self.cap, self.every = min_down, cap, every
        self.name = name or f"下行波动目标{target:.0%}"

    def scale(self, j, ctx):
        src = ctx.get("base_rets")
        if src is None:
            raise ValueError("DownsideVolTarget 需要 base_rets")
        lo = max(int(ctx.get("start", 0)), j - self.window + 1)
        r = np.asarray(src[lo:j + 1], float)
        r = r[np.isfinite(r)]
        if len(r) < max(self.min_periods, 20):
            return 1.0
        down = r[r < 0]
        if len(down) < self.min_down:
            sd = float(np.std(r, ddof=1)) * np.sqrt(252)
        else:
            sd = float(np.sqrt(np.mean(down * down))) * np.sqrt(252)
        if not np.isfinite(sd) or sd <= 1e-8:
            return 1.0
        return float(np.clip(self.target / sd, 0.0, self.cap))


class CVaRTarget(E.Rule):
    """60 日 95% 历史 CVaR（低于 5% 分位的均值 x sqrt(252) 年化），目标 15%。"""
    requires_base = True

    def __init__(self, target=0.15, window=60, min_periods=40, min_tail=3, cap=1.0, every=10, name=None):
        self.target, self.window, self.min_periods = target, window, min_periods
        self.min_tail, self.cap, self.every = min_tail, cap, every
        self.name = name or f"CVaR95目标{target:.0%}"

    def scale(self, j, ctx):
        src = ctx.get("base_rets")
        if src is None:
            raise ValueError("CVaRTarget 需要 base_rets")
        lo = max(int(ctx.get("start", 0)), j - self.window + 1)
        r = np.asarray(src[lo:j + 1], float)
        r = r[np.isfinite(r)]
        if len(r) < max(self.min_periods, 20):
            return 1.0
        q = float(np.quantile(r, 0.05))
        tail = r[r <= q]
        if len(tail) < self.min_tail:
            return 1.0
        cvar = float(-tail.mean()) * np.sqrt(252)
        if not np.isfinite(cvar) or cvar <= 1e-8:
            return 1.0
        return float(np.clip(self.target / cvar, 0.0, self.cap))


def _corr_pick(order, k, i, f):
    ret = f["ret"].to_numpy()
    lo = max(0, i - 59)
    thr = float(f["corr_thr"])
    sel = []
    for c in order:
        if len(sel) >= k:
            break
        ok = True
        a = ret[lo:i + 1, c]
        for s in sel:
            b = ret[lo:i + 1, s]
            m = np.isfinite(a) & np.isfinite(b)
            if m.sum() < 40:
                continue
            if np.corrcoef(a[m], b[m])[0, 1] > thr:
                ok = False
                break
        if ok:
            sel.append(c)
    return sel


def _sec_wcap(base, sec_arr, cap):
    w = dict(base)
    for _ in range(20):
        sw = {}
        for c, x in w.items():
            sw[sec_arr[c]] = sw.get(sec_arr[c], 0.0) + x
        over = {s: v for s, v in sw.items() if v > cap + 1e-12}
        if not over:
            break
        released = 0.0
        for c in list(w):
            s = sec_arr[c]
            if s in over:
                nw = w[c] * cap / over[s]
                released += w[c] - nw
                w[c] = nw
        under = [c for c in w if sec_arr[c] not in over]
        usum = sum(w[c] for c in under)
        if usum > 1e-12 and released > 0:
            for c in under:
                w[c] += released * w[c] / usum
        else:
            break
    return {c: x for c, x in w.items() if x > 1e-12}


def _cost_aware(new_w, old_w, f, cfg):
    k = f.get("deadband_k")
    cap = f.get("turn_cap")
    if k:
        thr = float(k) * 2.0 * cfg.cost
        out = {}
        for c in set(new_w) | set(old_w):
            a, b = old_w.get(c, 0.0), new_w.get(c, 0.0)
            if abs(b - a) < thr:
                if a > 1e-12:
                    out[c] = a
            else:
                if b > 1e-12:
                    out[c] = b
        new_w = out
    if cap:
        l1 = sum(abs(new_w.get(c, 0.0) - old_w.get(c, 0.0)) for c in set(new_w) | set(old_w))
        if l1 > cap + 1e-12:
            sc = cap / l1
            out = {}
            for c in set(new_w) | set(old_w):
                a, b = old_w.get(c, 0.0), new_w.get(c, 0.0)
                x = a + (b - a) * sc
                if x > 1e-12:
                    out[c] = x
            new_w = out
    return new_w


def _build_simulate_st():
    src = inspect.getsource(E.simulate)
    reps = [
        ("def simulate(close: pd.DataFrame, f: dict, cfg: Config, keep_weights: bool = False) -> dict:",
         "def simulate_st(close: pd.DataFrame, f: dict, cfg: Config, keep_weights: bool = False) -> dict:"),
        ("                    target = order[: cfg.k]\n",
         "                    kk = int(f['k_arr'][i]) if 'k_arr' in f else cfg.k\n"
         "                    if f.get('corr_thr'):\n"
         "                        target = _corr_pick(order, kk, i, f)\n"
         "                    else:\n"
         "                        target = order[: kk]\n"),
        ("            base = _scheme_weights(cfg.weight_mode, names, i, close, f, cfg.cap, vrow)\n"
         "            apply({c: x * s_new for c, x in base.items()})\n",
         "            base = _scheme_weights(cfg.weight_mode, names, i, close, f, cfg.cap, vrow)\n"
         "            if f.get('sec_wcap'):\n"
         "                base = _sec_wcap(base, f['sec_arr'], f['sec_wcap'])\n"
         "            new_w = {c: x * s_new for c, x in base.items()}\n"
         "            new_w = _cost_aware(new_w, w, f, cfg)\n"
         "            apply(new_w)\n"),
    ]
    for a, b in reps:
        if src.count(a) != 1:
            raise RuntimeError(f"simulate_st patch not unique: {a[:80]!r} count={src.count(a)}")
        src = src.replace(a, b)
    ns = dict(E.__dict__)
    ns["_corr_pick"] = _corr_pick
    ns["_sec_wcap"] = _sec_wcap
    ns["_cost_aware"] = _cost_aware
    exec(compile(src, "st_tracks_simulate_st", "exec"), ns)
    return ns["simulate_st"]


simulate_st = _build_simulate_st()


def need_patch(spec):
    return bool(spec.get("dead_k") or spec.get("turn_cap") or spec.get("size") == "K358"
                or spec.get("cap") in ("SECW40", "CORRCAP") or spec.get("reb_mode") == "rolling_change")


def _top80(d):
    return list(PV.ranked(d).index[:80])


def _share_chg(d, codes):
    """份额相对 4 周前的变化率。只用日期 <= d-5 自然日的最新一期，避免周度公布滞后前视。
    无数据（深市等）记为 NaN。"""
    sh = PV.G["shares"]
    cut = d - pd.Timedelta(days=5)
    i = sh.index.searchsorted(cut, side="right") - 1
    if i < 0:
        return pd.Series({c: np.nan for c in codes})
    jcut = sh.index[i] - pd.Timedelta(days=24)
    j = sh.index.searchsorted(jcut, side="right") - 1
    if j < 0 or j >= i:
        return pd.Series({c: np.nan for c in codes})
    a, b = sh.iloc[i], sh.iloc[j]
    out = {}
    for c in codes:
        x, y = a.get(c, np.nan) if len(a) else np.nan, b.get(c, np.nan) if len(b) else np.nan
        out[c] = x / y - 1.0 if np.isfinite(x) and np.isfinite(y) and y > 0 else np.nan
    return pd.Series(out)


def pool_on_new(var, d, prev):
    s80 = _top80(d)
    if var == "TOG20":
        g = (PV.G["a20"].loc[d] / PV.G["a60"].loc[d]).reindex(s80)
        return list(g.dropna().sort_values(ascending=False).index[:40])
    if var == "SHR4":
        chg = _share_chg(d, s80)
        if int(chg.notna().sum()) < 10:
            return list(PV.ranked(d).index[:40])
        med = float(chg.dropna().median())
        sc = chg.fillna(med)
        return list(sc.sort_values(ascending=False).index[:40])
    if var == "SECROT":
        i = PV.G["cal"].get_loc(d)
        lo = max(0, i - 19)
        R = PV.G["R"].iloc[lo:i + 1]
        sec_ret = {}
        members = {}
        for c in s80:
            s = PV.G["sec"].get(c, "")
            members.setdefault(s, []).append(c)
        for s, cs in members.items():
            if len(cs) < 2:
                continue
            rr = R[cs].mean(axis=1)
            if int(rr.notna().sum()) < 15:
                continue
            sec_ret[s] = float((1 + rr.fillna(0)).prod() - 1)
        if not sec_ret:
            return list(PV.ranked(d).index[:40])
        top_sec = list(pd.Series(sec_ret).sort_values(ascending=False).index[:8])
        buckets = {s: [c for c in PV.ranked(d).index if c in set(members.get(s, []))] for s in top_sec}
        out, ptr = [], {s: 0 for s in top_sec}
        while len(out) < 40:
            progressed = False
            for s in top_sec:
                k = ptr[s]
                if k < len(buckets[s]):
                    out.append(buckets[s][k])
                    ptr[s] = k + 1
                    progressed = True
                    if len(out) >= 40:
                        break
            if not progressed:
                break
        if len(out) < 40:
            rest = [c for c in PV.ranked(d).index if c not in out]
            out += rest[:40 - len(out)]
        return out[:40]
    if var == "XBCM":
        s_all = PV.ranked(d)
        xb = [c for c in s_all.index if PV.G["cat"].get(c) == "跨境"][:8]
        cm = [c for c in s_all.index if PV.G["cat"].get(c) == "商品"][:4]
        rest_n = 40 - len(xb) - len(cm)
        used = set(xb) | set(cm)
        rest = [c for c in s_all.index if c not in used and PV.G["cat"].get(c) not in ("跨境", "商品")][:rest_n]
        return (rest + xb + cm)[:40]
    raise KeyError(var)


def lists_of_new(var):
    out, prev = {}, None
    for d in PV.G["me"]:
        prev = pool_on_new(var, d, prev)
        out[d] = prev
    return out


def _signal_new(close, valid, sig, amount, ma60, ret):
    rk = lambda X: X.where(valid).rank(axis=1, pct=True)
    if sig == "PB_BO":
        hi60 = close.rolling(60, min_periods=60).max()
        is_hi = close >= hi60 * 0.9999
        bo = close.where(is_hi)
        recent_bo = bo.shift(1).rolling(10, min_periods=1).max()
        ma20 = close.rolling(20, min_periods=20).mean()
        cand = (recent_bo.notna() & (close >= recent_bo * 0.97) & (close < recent_bo)
                & (close > ma20) & (close > close.shift(1)))
        lo60 = close.rolling(60, min_periods=60).min()
        hi120 = close.rolling(120, min_periods=120).max()
        lo120 = close.rolling(120, min_periods=120).min()
        pos = (rk((close - lo60) / (hi60 - lo60).where(hi60 > lo60))
               + rk((close - lo120) / (hi120 - lo120).where(hi120 > lo120))) / 2.0
        return pos, cand
    if sig == "VOL_BO":
        hi60 = close.rolling(60, min_periods=60).max()
        lo60 = close.rolling(60, min_periods=60).min()
        hi120 = close.rolling(120, min_periods=120).max()
        lo120 = close.rolling(120, min_periods=120).min()
        pos = (rk((close - lo60) / (hi60 - lo60).where(hi60 > lo60))
               + rk((close - lo120) / (hi120 - lo120).where(hi120 > lo120))) / 2.0
        cand = close > (hi60 + lo60) / 2
        if amount is not None:
            a20 = amount.rolling(20, min_periods=15).mean()
            boom = (amount >= a20 * 1.5).rolling(5, min_periods=1).max()
            cand = cand & (boom > 0)
        return pos, cand
    if sig == "BB_SQ":
        ma20 = close.rolling(20, min_periods=20).mean()
        sd20 = close.rolling(20, min_periods=20).std()
        mid = ma20.replace(0, np.nan)
        bw = (4.0 * sd20) / mid
        q20 = bw.rolling(120, min_periods=80).quantile(0.20)
        upper = ma20 + 2.0 * sd20
        squeeze = bw <= q20
        brk = close > upper
        cand = squeeze & brk
        rm = rk((close - upper) / mid)
        return rm, cand
    if sig == "RS_PERS":
        mom60 = close / close.shift(60) - 1.0
        pct = rk(mom60)
        in_top = (pct >= 0.70).astype(float)
        n15 = in_top.rolling(20, min_periods=15).sum()
        cand = (close > ma60) & (n15 >= 15)
        rm = rk(n15) * 0.5 + pct * 0.5
        return rm, cand
    if sig == "TREND_STAB":
        mom60 = close / close.shift(60) - 1.0
        up = (ret > 0).astype(float).where(ret.notna())
        up_ratio = up.rolling(60, min_periods=40).mean()
        roll_dd = close / close.rolling(60, min_periods=40).max() - 1.0
        path_dd = roll_dd.rolling(60, min_periods=40).min().abs()
        score = up_ratio * (1.0 + mom60) / (1.0 + path_dd)
        cand = (close > ma60) & (mom60 > 0) & (up_ratio >= 0.55)
        return rk(score), cand
    raise KeyError(sig)


def feats_st(close, member, sig, cap, amount=None, seed=None):
    if sig in ("S2", "DONCH", "RAM", "TSM", "REG", "RIBBON"):
        f = X.feats(close, member, sig, "NOCAP")
    elif sig == "RND":
        f = X.feats(close, member, "S2", "NOCAP")
        rng = np.random.default_rng(int(seed))
        f["rm"] = pd.DataFrame(rng.random(f["rm"].shape), index=close.index, columns=close.columns).where(f["valid"])
        f["cand"] = f["valid"] & (close > f["ma60"])
    else:
        f = X.feats(close, member, "DONCH", "NOCAP")
        rm, cand = _signal_new(close, f["valid"], sig, amount, f["ma60"], f["ret"])
        f["rm"] = rm
        f["cand"] = f["valid"] & rm.notna() & cand.fillna(False)
        enough = (f["valid"].sum(axis=1) >= 8) & f["rm"].notna().any(axis=1)
        enough &= close.index >= pd.Timestamp(PV.SDATE)
        f["start"] = int(np.argmax(enough.to_numpy())) if enough.any() else len(close) - 1
    cap_kind = cap if cap in ("CAP2", "NOCAP") else "NOCAP"
    if cap_kind == "CAP2":
        rmv, cv = f["rm"].to_numpy(), f["cand"].to_numpy().copy()
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
    return f


def _rule_of(size):
    if size in ("EQ", "INVVOL"):
        return E.Fixed(1.0)
    if size in ("VT10", "K358"):
        return E.VolTarget(0.10, 60, 40, every=10)
    if size == "VT15":
        return E.VolTarget(0.15, 60, 40, every=10)
    if size == "DSVOL10":
        return DownsideVolTarget(0.10, 60, 40, 15, 1.0, 10)
    if size == "CVAR95_15":
        return CVaRTarget(0.15, 60, 40, 3, 1.0, 10)
    raise KeyError(size)


def run_spec(spec, cost, keep_w=False, sim=None):
    pool, sig, cap, size = spec["pool"], spec["sig"], spec["cap"], spec["size"]
    close, M = G["close"][pool], G["M"][pool]
    amt = G["amt"].get(pool)
    f = feats_st(close, M, sig, cap, amount=amt, seed=spec.get("seed"))
    f = dict(f)
    if size == "K358":
        n = f["cand"].sum(axis=1).to_numpy()
        f["k_arr"] = np.clip(np.rint(3 + 5 * np.minimum(n / 8.0, 1.0)), 3, 8).astype(int)
    if cap == "SECW40":
        f["sec_wcap"] = 0.40
        f["sec_arr"] = G["sv"][pool]
    if cap == "CORRCAP":
        f["corr_thr"] = 0.85
    if spec.get("dead_k"):
        f["deadband_k"] = float(spec["dead_k"])
    if spec.get("turn_cap"):
        f["turn_cap"] = float(spec["turn_cap"])
    wm = "equal" if size == "EQ" else "invvol"
    k = 8 if size == "K358" else 5
    reb = int(spec.get("reb") or 10)
    rmode = spec.get("reb_mode") or "rolling"
    tol = 2.0 if rmode == "rolling_change" else 0.05
    cfg = E.Config(k=k, reb=reb, reb_mode=rmode, reb_change_tol=tol, cost=cost, weight_mode=wm)
    fn = sim or (simulate_st if (need_patch(spec) or keep_w) else E.simulate)
    if size in ("EQ", "INVVOL"):
        rr = fn(close, f, cfg, keep_weights=keep_w)
        return dict(ret=np.asarray(rr["ret"], float), turn=np.asarray(rr["turn"], float),
                    expo=np.asarray(rr["expo"], float), cost=np.asarray(rr["cost"], float),
                    lo=f["start"], w_hist=rr.get("w_hist"), dates=close.index, close=close)
    r0 = fn(close, f, replace(cfg, rule=E.Fixed(1.0)))
    f2 = dict(f)
    f2["base_ret"] = r0["ret"]
    rr = fn(close, f2, replace(cfg, rule=_rule_of(size)), keep_weights=keep_w)
    return dict(ret=np.asarray(rr["ret"], float), turn=np.asarray(rr["turn"], float),
                expo=np.asarray(rr["expo"], float), cost=np.asarray(rr["cost"], float),
                lo=f["start"], w_hist=rr.get("w_hist"), dates=close.index, close=close)


def pack_metrics(ret, turn, expo, costv, lo):
    s0 = PV.starts()[0]
    lo_use = max(int(lo), s0)
    m = E.perf(ret[lo_use:])
    days = max(len(ret) - lo_use, 1)
    row = dict(cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"], sharpe=m["sharpe"], vol=m["vol"],
               turn=float(np.sum(turn[lo_use:]) / (days / 252.0)),
               expo=float(np.mean(expo[lo_use:])),
               cost_drag=float(np.sum(costv[lo_use:]) / (days / 252.0)))
    rv, sg = X.roll_vec(ret, lo)
    row["roll_med"] = float(np.nanmedian(rv)) if np.isfinite(rv).any() else np.nan
    for i, g in enumerate(PV.SEGS):
        row[f"seg_{g}"] = float(sg[i]) if i < len(sg) else np.nan
    row["_roll"] = rv
    row["_seg"] = sg
    for nm, (a, b) in (("P1", SPLIT["A"][0]), ("P2", SPLIT["A"][1])):
        rm, fc, cg, md = FR.seg_metrics(ret, lo, a, b)
        l0, h0 = FR.seg_idx(a, b)
        l0 = max(l0, int(lo))
        dseg = max(h0 - l0, 1)
        row.update({f"{nm}_roll_med": rm, f"{nm}_calmar": fc, f"{nm}_cagr": cg, f"{nm}_mdd": md,
                    f"{nm}_turn": float(np.sum(turn[l0:h0]) / (dseg / 252.0)),
                    f"{nm}_expo": float(np.mean(expo[l0:h0])) if h0 > l0 else np.nan,
                    f"{nm}_cost_drag": float(np.sum(costv[l0:h0]) / (dseg / 252.0)),
                    f"{nm}_vol": E.perf(ret[l0:h0])["vol"] if h0 - l0 > 20 else np.nan})
    return row


def oos_delta_ok(row, ctl, prefix_oos):
    """切分 OOS：滚动中位卡玛增量 > +0.05 且整段卡玛增量为正。"""
    d_roll = row[f"{prefix_oos}_roll_med"] - ctl[f"{prefix_oos}_roll_med"]
    d_cal = row[f"{prefix_oos}_calmar"] - ctl[f"{prefix_oos}_calmar"]
    return bool(d_roll > PASS_MED and d_cal > 0), d_roll, d_cal


def pool_universe_stats(lists):
    C = PV.G["C"]
    R = PV.G["R"]
    cal = PV.G["cal"]
    me = [d for d in PV.G["me"] if d >= pd.Timestamp("2014-06-01") and d <= pd.Timestamp("2026-11-13")]
    rows = []
    prev = None
    for d in me:
        mem = [c for c in lists[d] if c in C.columns]
        if len(mem) < 5:
            continue
        i = int(cal.searchsorted(d, side="right"))
        if i + 2 >= len(cal):
            continue

        def fwd(n, names):
            if not names:
                return np.nan
            hi = min(i + 1 + n, len(cal))
            lo = i + 1
            if hi - lo < max(5, n // 2):
                return np.nan
            rr = R.reindex(columns=names).iloc[lo:hi]
            ew = rr.mean(axis=1, skipna=True)
            if ew.notna().sum() < max(5, n // 2):
                return np.nan
            return float((1 + ew.fillna(0)).prod() - 1)

        stay = [c for c in mem if prev and c in prev]
        new = [c for c in mem if not prev or c not in prev]
        turned = 1.0 - (len(set(mem) & set(prev)) / max(len(set(mem) | set(prev)), 1) if prev else 0.0)
        rows.append(dict(date=d, n=len(mem), turnover=turned if prev else np.nan,
                         fwd20=fwd(20, mem), fwd60=fwd(60, mem),
                         stay20=fwd(20, stay) if stay else np.nan,
                         new20=fwd(20, new) if new else np.nan,
                         stay60=fwd(60, stay) if stay else np.nan,
                         new60=fwd(60, new) if new else np.nan,
                         n_new=len(new)))
        prev = set(mem)
    return pd.DataFrame(rows)


def cap_stats(res, close, sec_arr, lo):
    wh = res.get("w_hist") or []
    if not wh:
        return {}
    max_sec, hhi, sec_hhi, top2, nheld = [], [], [], [], []
    sec_ret = {}
    rets = close.pct_change(fill_method=None)
    for i, w in enumerate(wh):
        if i < lo or not w:
            continue
        tw = float(sum(w.values()))
        if tw <= 1e-12:
            continue
        sw = {}
        ww = np.array(list(w.values()))
        hhi.append(float(np.sum((ww / tw) ** 2)))
        ks = sorted(w, key=lambda c: -w[c])
        top2.append(float(sum(w[c] for c in ks[:2]) / tw))
        nheld.append(len(w))
        for c, x in w.items():
            s = sec_arr[c] if c < len(sec_arr) else ""
            sw[s] = sw.get(s, 0.0) + x / tw
        max_sec.append(max(sw.values()) if sw else np.nan)
        sec_hhi.append(float(sum(v * v for v in sw.values())))
        if i + 1 < len(close):
            r = rets.iloc[i + 1]
            for c, x in w.items():
                s = sec_arr[c] if c < len(sec_arr) else ""
                rc = r.iloc[c] if c < len(r) else np.nan
                if np.isfinite(rc):
                    sec_ret.setdefault(s, []).append((i + 1, x, rc))
    worst_dd = np.nan
    if sec_ret:
        dds = []
        for s, trips in sec_ret.items():
            contrib = np.zeros(len(wh))
            for i, x, rc in trips:
                if i < len(contrib):
                    contrib[i] += x * rc
            eq = np.cumprod(1 + contrib[lo:])
            if len(eq) > 20 and np.isfinite(eq).all() and (eq.min() > 0):
                dds.append(float((eq / np.maximum.accumulate(eq) - 1).min()))
        if dds:
            worst_dd = float(min(dds))
    return dict(avg_max_sec=float(np.nanmean(max_sec)) if max_sec else np.nan,
                max_max_sec=float(np.nanmax(max_sec)) if max_sec else np.nan,
                avg_hhi=float(np.nanmean(hhi)) if hhi else np.nan,
                avg_sec_hhi=float(np.nanmean(sec_hhi)) if sec_hhi else np.nan,
                avg_top2=float(np.nanmean(top2)) if top2 else np.nan,
                avg_n=float(np.nanmean(nheld)) if nheld else np.nan,
                worst_sec_dd=worst_dd)


def avg_linkage(corr, n_clusters=4):
    n = corr.shape[0]
    if n <= n_clusters:
        return [[i] for i in range(n)]
    D = 1.0 - corr
    np.fill_diagonal(D, 0.0)
    members = {i: [i] for i in range(n)}
    active = set(range(n))
    nxt = n
    while len(active) > n_clusters:
        best = None
        ids = list(active)
        for a, b in itertools.combinations(ids, 2):
            d = float(D[np.ix_(members[a], members[b])].mean())
            if best is None or d < best[0]:
                best = (d, a, b)
        _, a, b = best
        members[nxt] = members[a] + members[b]
        active.remove(a)
        active.remove(b)
        active.add(nxt)
        nxt += 1
    return [members[i] for i in active]


def is_robust_mask(rets, r_s4, los, a, b):
    st, hi = FR.seg_starts(a, b)
    segs = []
    for g, (ga, gb) in PV.SEGS.items():
        if pd.Timestamp(ga) >= pd.Timestamp(a) and pd.Timestamp(gb) <= pd.Timestamp(b):
            segs.append((ga, gb))
    out = []
    for ret, lo in zip(rets, los):
        v = np.array([PV.calmar(ret[s:hi]) - PV.calmar(r_s4[s:hi]) if lo <= s else np.nan for s in st])
        if not np.isfinite(v).any():
            out.append(False)
            continue
        share = float(np.sum(v > 0) / len(v))
        med = float(np.nanmedian(v))
        nseg = 0
        ok_segs = True
        for ga, gb in segs:
            nseg += 1
            l0, h0 = FR.seg_idx(ga, gb)
            if lo > l0:
                ok_segs = False
                continue
            d = PV.calmar(ret[l0:h0]) - PV.calmar(r_s4[l0:h0])
            if not (d > 0):
                ok_segs = False
        out.append(bool(share >= PASS_SHARE and med > PASS_MED and (nseg == 0 or ok_segs)))
    return np.array(out, dtype=bool)


def is_relax_mask(rets, r_s4, los, a, b):
    st, hi = FR.seg_starts(a, b)
    out = []
    for ret, lo in zip(rets, los):
        v = np.array([PV.calmar(ret[s:hi]) - PV.calmar(r_s4[s:hi]) if lo <= s else np.nan for s in st])
        out.append(bool(np.isfinite(v).any() and float(np.nanmedian(v)) > 0))
    return np.array(out, dtype=bool)


def pick_ensemble(keys, rets, los, r_s4, a, b, roll_meds):
    mask = is_robust_mask(rets, r_s4, los, a, b)
    used = "robust"
    if int(mask.sum()) < 8:
        mask = is_relax_mask(rets, r_s4, los, a, b)
        used = "relax_med>0"
    idx = np.flatnonzero(mask)
    if len(idx) < 3:
        order = np.argsort(-np.asarray(roll_meds))
        idx = order[: min(30, len(order))]
        used = "top30_greedy"
    sub_keys = [keys[i] for i in idx]
    sub_rets = [rets[i] for i in idx]
    l0, h0 = FR.seg_idx(a, b)
    mat = np.column_stack([r[l0:h0] for r in sub_rets])
    cm = pd.DataFrame(mat).corr().to_numpy()
    cm = np.nan_to_num(cm, nan=0.0)
    np.fill_diagonal(cm, 1.0)
    ncl = 4 if len(sub_keys) >= 8 else max(3, min(4, len(sub_keys)))
    if used == "top30_greedy":
        order = np.argsort([-roll_meds[i] for i in idx])
        sel = []
        for j in order:
            if len(sel) >= 4:
                break
            if all(cm[j, s] <= 0.85 for s in sel):
                sel.append(j)
        if len(sel) < 3:
            sel = list(order[:3])
        chosen = [sub_keys[j] for j in sel]
        return chosen, used, [int(idx[j]) for j in sel]
    clusters = avg_linkage(cm, n_clusters=min(ncl, len(sub_keys)))
    reps = []
    for cl in clusters:
        best = max(cl, key=lambda j: roll_meds[idx[j]])
        reps.append(best)
    reps = sorted(reps, key=lambda j: -roll_meds[idx[j]])
    kept = []
    for j in reps:
        if all(cm[j, k] <= 0.85 for k in kept):
            kept.append(j)
    if len(kept) < 3:
        rest = [j for j in range(len(sub_keys)) if j not in kept]
        rest = sorted(rest, key=lambda j: -roll_meds[idx[j]])
        for j in rest:
            if len(kept) >= 3:
                break
            if all(cm[j, k] <= 0.85 for k in kept):
                kept.append(j)
        if len(kept) < 3:
            kept = reps[:3]
    kept = kept[:5]
    chosen = [sub_keys[j] for j in kept]
    return chosen, used, [int(idx[j]) for j in kept]


def eq_mix(rets, lo):
    arr = np.vstack([np.asarray(r, float) for r in rets])
    return np.nanmean(arr, axis=0), int(lo)


def setup():
    X.setup_parent()
    for p in NEW_POOLS:
        print(f"build pool {p}", flush=True)
        t0 = time.time()
        X.G["lists"][p] = lists_of_new(p)
        print(f"  {p} done {time.time()-t0:.1f}s", flush=True)
    for p in ALL_POOLS:
        ever = sorted({c for v in X.G["lists"][p].values() for c in v})
        G.setdefault("close", {})[p] = PV.G["C"][ever]
        G.setdefault("M", {})[p] = X.G["M"][p] if p in X.G["M"] else PV.PB.member_mask(
            X.G["lists"][p], PV.G["cal"], ever)
        if p not in X.G["M"]:
            X.G["M"][p] = G["M"][p]
        G.setdefault("sv", {})[p] = np.array([PV.G["sec"].get(c, "") for c in ever])
        cols = list(G["close"][p].columns)
        G.setdefault("amt", {})[p] = PV.G["A"].reindex(index=G["close"][p].index, columns=cols)
    G["ref"] = {}
    for bp in (5, 20):
        z = np.load(PV.ROOT / "data" / "cache_combo3" / f"ref_{bp}.npz")
        G["ref"][bp] = dict(broad=z["broad"], broad_lo=int(z["broad_lo"][0]), core=z["core"])
        G.setdefault("rS4", {})[bp] = np.load(CACHE_FR / f"ret_{bp}_NONE__BASE__NONE__S2__CAP2__VT15__NONE.npy")
        G.setdefault("rCH", {})[bp] = np.load(CACHE_FR / f"ret_{bp}_NONE__P12_HYST60__NONE__DONCH__CAP2__VT10__NONE.npy")
    G["cal"] = PV.G["cal"]
    G["s0"] = PV.starts()[0]


def save_book(name, bp, d):
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(CACHE / f"bk_{name}_{bp}.npz",
                        ret=d["ret"].astype(np.float32), turn=d["turn"].astype(np.float32),
                        expo=d["expo"].astype(np.float32), cost=d["cost"].astype(np.float32),
                        lo=np.array([d["lo"]]))


def load_book(name, bp):
    z = np.load(CACHE / f"bk_{name}_{bp}.npz")
    return dict(ret=z["ret"].astype(float), turn=z["turn"].astype(float),
                expo=z["expo"].astype(float), cost=z["cost"].astype(float), lo=int(z["lo"][0]))


def _pack_wh(wh):
    ptr = [0]
    idxs, vals = [], []
    for w in wh:
        for c, x in w.items():
            idxs.append(int(c))
            vals.append(float(x))
        ptr.append(len(idxs))
    return dict(idx=np.array(idxs, np.int32), val=np.array(vals, np.float32), ptr=np.array(ptr, np.int32))


def _unpack_wh(z):
    idx, val, ptr = z["idx"], z["val"], z["ptr"]
    out = []
    for i in range(len(ptr) - 1):
        sl = slice(int(ptr[i]), int(ptr[i + 1]))
        out.append({int(a): float(b) for a, b in zip(idx[sl], val[sl])})
    return out


def cmd_check():
    setup()
    spec = dict(name="MIN3_P12", pool="P12_HYST60", sig="DONCH", cap="CAP2", size="VT10",
                reb=10, reb_mode="rolling", dead_k=None, turn_cap=None)
    a = run_spec(spec, 0.0005, sim=E.simulate)
    b = run_spec(spec, 0.0005, sim=simulate_st)
    d1 = float(np.max(np.abs(a["ret"] - b["ret"])))
    d_ch = float(np.max(np.abs(a["ret"] - G["rCH"][5])))
    s4s = dict(name="S4", pool="BASE", sig="S2", cap="CAP2", size="VT15",
               reb=10, reb_mode="rolling", dead_k=None, turn_cap=None)
    c = run_spec(s4s, 0.0005, sim=E.simulate)
    d_s4 = float(np.max(np.abs(c["ret"] - G["rS4"][5])))
    print(f"simulate_st vs E.simulate maxabs={d1:.3e}")
    print(f"MIN3_P12 vs champion cache maxabs={d_ch:.3e} lo={a['lo']}")
    print(f"S4 vs S4 cache maxabs={d_s4:.3e} lo={c['lo']}")
    for p in NEW_POOLS:
        lens = [len(v) for v in X.G["lists"][p].values()]
        print(f"  pool {p}: n_months={len(lens)} mean_n={np.mean(lens):.1f} min={min(lens)} max={max(lens)}")
    ok = (d1 < 1e-10 and d_ch < 1e-10 and d_s4 < 1e-10)
    if len(a["ret"]) != len(G["rCH"][5]) or len(c["ret"]) != len(G["rS4"][5]):
        print("LENGTH MISMATCH", len(a["ret"]), len(G["rCH"][5]), len(c["ret"]), len(G["rS4"][5]))
        ok = False
    print("CHECK", "PASS" if ok else "FAIL")
    if not ok:
        sys.exit(2)


def cmd_run():
    setup()
    specs = configs() + rnd_configs()
    print(f"n_specs={len(specs)} (45+{N_RND} rnd) x {len(COSTS)} costs", flush=True)
    t0 = time.time()
    n = 0
    for spec in specs:
        for cost in COSTS:
            bp = int(round(cost * 1e4))
            path = CACHE / f"bk_{spec['name']}_{bp}.npz"
            if path.exists():
                n += 1
                continue
            d = run_spec(spec, cost, keep_w=False)
            save_book(spec["name"], bp, d)
            n += 1
            if n % 10 == 0:
                print(f"  sim {n}/{len(specs)*2} {time.time()-t0:.1f}s last={spec['name']} {bp}bp", flush=True)
    print(f"simulate done {n} in {time.time()-t0:.1f}s", flush=True)

    track_names = {
        "MIN3_P12", "SIG_S2", "SIG_PB_BO", "SIG_VOL_BO", "SIG_BB_SQ", "SIG_RS_PERS", "SIG_TREND_STAB",
        "CAP_NOCAP", "CAP_SECW40", "CAP_CORRCAP",
        "SIZE_EQ", "SIZE_INVVOL", "SIZE_VT15", "SIZE_DSVOL10", "SIZE_CVAR95_15", "SIZE_K358",
    }
    by_name = {s["name"]: s for s in configs()}
    for name in sorted(track_names):
        wp = CACHE / f"w_{name}_5.npz"
        if wp.exists():
            continue
        print(f"  weights {name}", flush=True)
        d = run_spec(by_name[name], 0.0005, keep_w=True)
        np.savez_compressed(wp, lo=np.array([d["lo"]]), n=np.array([len(d["w_hist"])]), **_pack_wh(d["w_hist"]))
    print("weights done", flush=True)
    cmd_post()


def cmd_post():
    setup()
    specs = configs()
    rows = []
    books = {}
    for spec in specs:
        for cost in COSTS:
            bp = int(round(cost * 1e4))
            d = load_book(spec["name"], bp)
            met = pack_metrics(d["ret"], d["turn"], d["expo"], d["cost"], d["lo"])
            row = dict(spec, cost_bp=bp, **{k: v for k, v in met.items() if not k.startswith("_")})
            for tag, rctl, lctl in (
                ("s4", G["rS4"][bp], d["lo"]),
                ("ch", G["rCH"][bp], d["lo"]),
                ("br", G["ref"][bp]["broad"], max(d["lo"], G["ref"][bp]["broad_lo"])),
            ):
                rv, sg = X.roll_vec(d["ret"], d["lo"])
                cv, csg = X.roll_vec(rctl, lctl)
                share, med, npos, passed = X.cmp((rv, sg), (cv, csg))
                row[f"{tag}_share"] = share
                row[f"{tag}_med"] = med
                row[f"{tag}_seg"] = npos
                row[f"{tag}_pass"] = bool(passed)
            books[(spec["name"], bp)] = d
            rows.append(row)
    ctl_rows = []
    for bp in (5, 20):
        for name in ("CHAMPION", "S4_REF", "BROAD_VT15"):
            if name == "CHAMPION" and ("MIN3_P12", bp) in books:
                d = books[("MIN3_P12", bp)]
                met = pack_metrics(d["ret"], d["turn"], d["expo"], d["cost"], d["lo"])
            elif name == "S4_REF" and ("S4", bp) in books:
                d = books[("S4", bp)]
                met = pack_metrics(d["ret"], d["turn"], d["expo"], d["cost"], d["lo"])
            else:
                ret = G["ref"][bp]["broad"]
                lo = G["ref"][bp]["broad_lo"]
                z = np.zeros_like(ret)
                met = pack_metrics(ret, z, np.ones_like(ret), z, lo)
            ctl_rows.append(dict(name=name, family="control", cost_bp=bp,
                                 **{k: v for k, v in met.items() if not k.startswith("_")}))
    df = pd.DataFrame(rows)
    dfc = pd.DataFrame(ctl_rows)

    sat_rows = []
    s0 = G["s0"]
    sat_names = set()
    for spec in specs:
        if spec["family"] in ("structure", "stack", "pool") or spec["name"].startswith("COST_MIN3"):
            sat_names.add(spec["name"])
    for spec in specs:
        if spec["name"] not in sat_names:
            continue
        for cost in COSTS:
            bp = int(round(cost * 1e4))
            d = books[(spec["name"], bp)]
            core = dict(ret=G["ref"][bp]["core"], expo=np.ones_like(G["ref"][bp]["core"]), lo=0)
            mix = RC.mix(core, dict(ret=d["ret"], expo=d["expo"], lo=d["lo"]), PV.WC, s0, cost)["ret"]
            z = np.zeros_like(mix)
            met = pack_metrics(mix, z, np.ones_like(mix), z, s0)
            sat_rows.append(dict(name=f"C3+{spec['name']}", sat=spec["name"], family="satellite",
                                 cost_bp=bp, **{k: v for k, v in met.items() if not k.startswith("_")}))
    for bp, cost in ((5, 0.0005), (20, 0.0020)):
        core = dict(ret=G["ref"][bp]["core"], expo=np.ones_like(G["ref"][bp]["core"]), lo=0)
        for nm, ret, lo in (
            ("C3+CHAMPION", G["rCH"][bp], 0),
            ("C3+S4", G["rS4"][bp], 0),
            ("C3+BROAD_VT15", G["ref"][bp]["broad"], G["ref"][bp]["broad_lo"]),
        ):
            mix = RC.mix(core, dict(ret=ret, expo=np.ones_like(ret), lo=lo), PV.WC, s0, cost)["ret"]
            z = np.zeros_like(mix)
            met = pack_metrics(mix, z, np.ones_like(mix), z, s0)
            sat_rows.append(dict(name=nm, sat=nm.split("+", 1)[1], family="satellite_ctl",
                                 cost_bp=bp, **{k: v for k, v in met.items() if not k.startswith("_")}))
    dfs = pd.DataFrame(sat_rows)

    def ctl_map(name, bp):
        sub = dfc[(dfc.name == name) & (dfc.cost_bp == bp)]
        return sub.iloc[0] if len(sub) else None

    extra = []
    for _, row in df.iterrows():
        rec = {}
        bp = int(row.cost_bp)
        for ctl_name, prefix in (("CHAMPION", "vs_ch"), ("S4_REF", "vs_s4"), ("BROAD_VT15", "vs_br")):
            c = ctl_map(ctl_name, bp)
            if c is None:
                continue
            okA, dA_r, dA_c = oos_delta_ok(row, c, "P2")
            okB, dB_r, dB_c = oos_delta_ok(row, c, "P1")
            rec[f"{prefix}_oosA_ok"] = okA
            rec[f"{prefix}_oosB_ok"] = okB
            rec[f"{prefix}_oosA_droll"] = dA_r
            rec[f"{prefix}_oosA_dcal"] = dA_c
            rec[f"{prefix}_oosB_droll"] = dB_r
            rec[f"{prefix}_oosB_dcal"] = dB_c
            full_pass = bool(row[{"vs_ch": "ch_pass", "vs_s4": "s4_pass", "vs_br": "br_pass"}[prefix]])
            rec[f"{prefix}_struct_pass"] = bool(full_pass and okA and okB)
        extra.append(rec)
    df = pd.concat([df.reset_index(drop=True), pd.DataFrame(extra)], axis=1)
    df.to_csv(RUN / "results_long.csv", index=False)
    dfc.to_csv(RUN / "controls.csv", index=False)
    dfs.to_csv(RUN / "satellite.csv", index=False)

    print("pool universe stats", flush=True)
    pu = []
    raw = {p: pool_universe_stats(X.G["lists"][p]) for p in ALL_POOLS}
    for p, tdf in raw.items():
        tdf.to_csv(RUN / f"pool_univ_{p}.csv", index=False)
        for period, a, b in (("full", "2014-06-01", "2026-11-13"),
                             ("P1", "2014-06-01", "2019-12-31"),
                             ("P2", "2020-01-01", "2026-11-13")):
            s = tdf[(tdf.date >= a) & (tdf.date <= b)]
            pu.append(dict(pool=p, period=period, n_months=int(len(s)),
                           mean_n=float(s.n.mean()) if len(s) else np.nan,
                           mean_turn=float(s.turnover.mean()) if len(s) else np.nan,
                           mean_fwd20=float(s.fwd20.mean()) if len(s) else np.nan,
                           mean_fwd60=float(s.fwd60.mean()) if len(s) else np.nan,
                           mean_new20=float(s.new20.mean()) if len(s) else np.nan,
                           mean_stay20=float(s.stay20.mean()) if len(s) else np.nan,
                           mean_new60=float(s.new60.mean()) if len(s) else np.nan,
                           mean_stay60=float(s.stay60.mean()) if len(s) else np.nan,
                           mean_n_new=float(s.n_new.mean()) if len(s) else np.nan))
    pudf = pd.DataFrame(pu)
    pudf.to_csv(RUN / "pool_track.csv", index=False)

    cov = []
    for d in PV.G["me"]:
        if d < pd.Timestamp("2014-06-01"):
            continue
        row = {"date": d}
        for p in NEW_POOLS:
            mem = X.G["lists"][p][d]
            row[f"{p}_n"] = len(mem)
            if p == "XBCM":
                row["XBCM_xb"] = sum(1 for c in mem if PV.G["cat"].get(c) == "跨境")
                row["XBCM_cm"] = sum(1 for c in mem if PV.G["cat"].get(c) == "商品")
            if p == "SHR4":
                chg = _share_chg(d, mem)
                row["SHR4_n_share"] = int(chg.notna().sum())
        cov.append(row)
    pd.DataFrame(cov).to_csv(RUN / "pool_coverage.csv", index=False)

    print("signal/cap tracks from weights", flush=True)
    sig_rows, cap_rows = [], []
    track_map = [
        ("MIN3_P12", "signal"), ("SIG_S2", "signal"),
        ("SIG_PB_BO", "signal"), ("SIG_VOL_BO", "signal"), ("SIG_BB_SQ", "signal"),
        ("SIG_RS_PERS", "signal"), ("SIG_TREND_STAB", "signal"),
        ("CAP_NOCAP", "cap"), ("MIN3_P12", "cap"), ("CAP_SECW40", "cap"), ("CAP_CORRCAP", "cap"),
    ]
    close = G["close"]["P12_HYST60"]
    for name, family in track_map:
        wp = CACHE / f"w_{name}_5.npz"
        if not wp.exists():
            continue
        z = np.load(wp)
        wh = _unpack_wh(z)
        if family == "signal":
            ents = _entries_dated(wh, close)
            for period, a, b in (("full", "2014-06-01", "2026-11-13"),
                                 ("P1", "2014-06-01", "2019-12-31"),
                                 ("P2", "2020-01-01", "2026-11-13")):
                sub = [e for e in ents if pd.Timestamp(a) <= e["date"] <= pd.Timestamp(b)]
                rec = dict(name=name, period=period, n_entries=len(sub))
                for n in (5, 10, 20, 60):
                    xs = np.array([e[n] for e in sub if e.get(n) is not None], float)
                    rec[f"n{n}_count"] = int(len(xs))
                    rec[f"n{n}_mean"] = float(np.nanmean(xs)) if len(xs) else np.nan
                    rec[f"n{n}_med"] = float(np.nanmedian(xs)) if len(xs) else np.nan
                    rec[f"n{n}_win"] = float(np.mean(xs > 0)) if len(xs) else np.nan
                sig_rows.append(rec)
        if family == "cap":
            sec_arr = G["sv"]["P12_HYST60"]
            for period, a, b in (("full", "2014-06-01", "2026-11-13"),
                                 ("P1", "2014-06-01", "2019-12-31"),
                                 ("P2", "2020-01-01", "2026-11-13")):
                l0, h0 = FR.seg_idx(a, b)
                st = cap_stats(dict(w_hist=wh[:h0]), close, sec_arr, l0)
                cap_rows.append(dict(name=name, period=period, **st))

    rnd_entry = _random_entry_bench()
    if sig_rows:
        sdf = pd.DataFrame(sig_rows)
        if rnd_entry is not None:
            sdf = sdf.merge(rnd_entry, on="period", how="left")
        sdf.to_csv(RUN / "signal_track.csv", index=False)
    if cap_rows:
        pd.DataFrame(cap_rows).to_csv(RUN / "cap_track.csv", index=False)

    rnd_perf = []
    for i in range(N_RND):
        for bp in (5, 20):
            d = load_book(f"RND_{i:02d}", bp)
            met = pack_metrics(d["ret"], d["turn"], d["expo"], d["cost"], d["lo"])
            rnd_perf.append(dict(name=f"RND_{i:02d}", cost_bp=bp,
                                 **{k: v for k, v in met.items() if not k.startswith("_")}))
    pd.DataFrame(rnd_perf).to_csv(RUN / "rnd_signal.csv", index=False)

    print("A2 ensemble", flush=True)
    ens_rows, mem_info = run_ensemble()
    pd.DataFrame(ens_rows).to_csv(RUN / "ensemble.csv", index=False)
    (RUN / "ensemble_members.json").write_text(json.dumps(mem_info, indent=2, ensure_ascii=False), encoding="utf-8")

    cost_rows = []
    for base, cal_name in (("MIN3", "MIN3_P12"), ("S4", "S4")):
        for bp in (5, 20):
            calb = books[(cal_name, bp)]
            s0b = max(calb["lo"], G["s0"])
            for mode in ("DAILY", "DEAD2", "DEAD4", "SETONLY", "TURNCAP40"):
                nm = f"COST_{base}_{mode}"
                d = books[(nm, bp)]
                lo = max(s0b, d["lo"])
                te = float(np.std(d["ret"][lo:] - calb["ret"][lo:], ddof=1) * np.sqrt(252))
                met = pack_metrics(d["ret"], d["turn"], d["expo"], d["cost"], d["lo"])
                metc = pack_metrics(calb["ret"], calb["turn"], calb["expo"], calb["cost"], calb["lo"])
                rec = dict(name=nm, base=base, mode=mode, cost_bp=bp, te_vs_cal10=te,
                           d_cost_drag=met["cost_drag"] - metc["cost_drag"],
                           d_turn=met["turn"] - metc["turn"],
                           d_calmar=met["calmar"] - metc["calmar"],
                           d_cagr=met["cagr"] - metc["cagr"],
                           d_mdd=met["mdd"] - metc["mdd"])
                rec.update({f"full_{k}": met[k] for k in ("cagr", "mdd", "calmar", "turn", "cost_drag", "expo", "vol")})
                for per, pref in (("P1", "P1_"), ("P2", "P2_")):
                    rec.update({f"{per}_{k}": met[f"{pref}{k}"] for k in ("cagr", "mdd", "calmar", "turn", "cost_drag", "expo", "vol")})
                    l0, h0 = FR.seg_idx(*(SPLIT["A"][0] if per == "P1" else SPLIT["A"][1]))
                    l0 = max(l0, lo)
                    if h0 - l0 > 20:
                        rec[f"{per}_te"] = float(np.std(d["ret"][l0:h0] - calb["ret"][l0:h0], ddof=1) * np.sqrt(252))
                cost_rows.append(rec)
    pd.DataFrame(cost_rows).to_csv(RUN / "cost_track.csv", index=False)

    effects = class_effects(df, pudf, pd.DataFrame(sig_rows) if sig_rows else pd.DataFrame(),
                            pd.DataFrame(cap_rows) if cap_rows else pd.DataFrame())
    pd.DataFrame(effects).to_csv(RUN / "layer_effects.csv", index=False)
    verdict = build_verdict(df, dfc, dfs, effects, ens_rows, mem_info)
    (RUN / "verdict.json").write_text(json.dumps(verdict, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("post done", flush=True)


def _entries_dated(wh, close):
    dates = close.index
    prev = set()
    out = []
    for i, w in enumerate(wh):
        cur = {c for c, x in w.items() if x > 1e-12}
        new = cur - prev
        prev = cur
        if not new or i >= len(dates):
            continue
        for c in new:
            if c >= close.shape[1]:
                continue
            px0 = close.iat[i, c]
            if not np.isfinite(px0) or px0 <= 0:
                continue
            rec = dict(date=dates[i], col=c)
            for n in (5, 10, 20, 60):
                j = i + n
                if j >= len(dates):
                    rec[n] = None
                    continue
                px1 = close.iat[j, c]
                rec[n] = float(px1 / px0 - 1.0) if np.isfinite(px1) and px1 > 0 else None
            out.append(rec)
    return out


def _random_entry_bench():
    """同池（P12）在再平衡日随机抽 5 只（收盘>MA60 且在池），后向 5/10/20/60 日。20 个种子。"""
    close = G["close"]["P12_HYST60"]
    M = G["M"]["P12_HYST60"]
    f = X.feats(close, M, "DONCH", "NOCAP")
    valid = f["valid"] & (close > f["ma60"])
    lo = f["start"]
    days = list(range(lo, len(close), 10))
    acc = {period: {n: [] for n in (5, 10, 20, 60)} for period in ("full", "P1", "P2")}
    dates = close.index
    for seed in range(N_RND):
        rng = np.random.default_rng(1000 + seed)
        for i in days:
            ok = np.flatnonzero(valid.to_numpy()[i])
            if len(ok) < 5:
                continue
            pick = rng.choice(ok, size=5, replace=False)
            dt = dates[i]
            if dt < pd.Timestamp("2014-06-01"):
                continue
            periods = ["full"]
            if dt <= pd.Timestamp("2019-12-31"):
                periods.append("P1")
            if dt >= pd.Timestamp("2020-01-01"):
                periods.append("P2")
            for c in pick:
                px0 = close.iat[i, int(c)]
                if not np.isfinite(px0) or px0 <= 0:
                    continue
                for n in (5, 10, 20, 60):
                    j = i + n
                    if j >= len(dates):
                        continue
                    px1 = close.iat[j, int(c)]
                    if np.isfinite(px1) and px1 > 0:
                        r = px1 / px0 - 1.0
                        for p in periods:
                            acc[p][n].append(r)
    rows = []
    for period in ("full", "P1", "P2"):
        rec = dict(period=period)
        for n in (5, 10, 20, 60):
            xs = np.array(acc[period][n], float)
            rec[f"rnd_n{n}_mean"] = float(np.nanmean(xs)) if len(xs) else np.nan
            rec[f"rnd_n{n}_win"] = float(np.mean(xs > 0)) if len(xs) else np.nan
            rec[f"rnd_n{n}_count"] = int(len(xs))
        rows.append(rec)
    return pd.DataFrame(rows)


def run_ensemble():
    gz = PV.ROOT / "runs/2026-11-21_full_rebuild/grid_results.csv.gz"
    grid = pd.read_csv(gz)
    g20 = grid[grid.cost_bp == 20].copy()
    keys = list(g20["key"])
    print(f"  load {len(keys)} ret series @20bp", flush=True)
    rets20, los = [], []
    for k in keys:
        r = np.load(CACHE_FR / f"ret_20_{k.replace('|', '__')}.npy")
        rets20.append(r)
        nz = np.flatnonzero(np.abs(r) > 1e-15)
        los.append(int(nz[0]) if len(nz) else G["s0"])
    rS4_20 = G["rS4"][20]
    mem_info = {}
    ens_rows = []

    def roll_med_is(ret, lo, a, b):
        rm, fc, cg, md = FR.seg_metrics(ret, lo, a, b)
        return rm

    for split, (is_ab, oos_ab) in SPLIT.items():
        a, b = is_ab
        print(f"  pick members split {split}", flush=True)
        rolls = [roll_med_is(r, lo, a, b) for r, lo in zip(rets20, los)]
        chosen, used, _pos = pick_ensemble(keys, rets20, los, rS4_20, a, b, rolls)
        mask_rob = is_robust_mask(rets20, rS4_20, los, a, b)
        mask_rel = is_relax_mask(rets20, rS4_20, los, a, b)
        mem_info[split] = dict(members=chosen, rule=used,
                               n_robust=int(mask_rob.sum()), n_is_pool=int(mask_rob.sum() if mask_rob.sum() >= 8 else mask_rel.sum()))
        pool_keys = [keys[i] for i in np.flatnonzero(mask_rob if mask_rob.sum() >= 8 else mask_rel)]
        if len(pool_keys) < len(chosen):
            pool_keys = keys
        for bp in (5, 20):
            mrets, mlos = [], []
            for ck in chosen:
                rr = np.load(CACHE_FR / f"ret_{bp}_{ck.replace('|', '__')}.npy")
                mrets.append(rr)
                nz = np.flatnonzero(np.abs(rr) > 1e-15)
                mlos.append(int(nz[0]) if len(nz) else G["s0"])
            mix, lo = eq_mix(mrets, max(mlos) if mlos else G["s0"])
            z = np.zeros_like(mix)
            met = pack_metrics(mix, z, np.ones_like(mix), z, lo)
            rec = dict(name=f"ENS_{split}", split=split, cost_bp=bp, n_members=len(chosen),
                       members="|".join(chosen), rule=used,
                       **{k: v for k, v in met.items() if not k.startswith("_")})
            mem_mets = [pack_metrics(r, z, np.ones_like(r), z, l) for r, l in zip(mrets, mlos)]
            rec["member_mean_calmar"] = float(np.nanmean([m["calmar"] for m in mem_mets]))
            rec["member_mean_mdd"] = float(np.nanmean([m["mdd"] for m in mem_mets]))
            rec["member_mean_cagr"] = float(np.nanmean([m["cagr"] for m in mem_mets]))
            rec["member_mean_P1_calmar"] = float(np.nanmean([m["P1_calmar"] for m in mem_mets]))
            rec["member_mean_P2_calmar"] = float(np.nanmean([m["P2_calmar"] for m in mem_mets]))
            rec["member_mean_P1_roll"] = float(np.nanmean([m["P1_roll_med"] for m in mem_mets]))
            rec["member_mean_P2_roll"] = float(np.nanmean([m["P2_roll_med"] for m in mem_mets]))
            for tag, rctl in (("ch", G["rCH"][bp]), ("s4", G["rS4"][bp]), ("br", G["ref"][bp]["broad"])):
                rv, sg = X.roll_vec(mix, lo)
                cv, csg = X.roll_vec(rctl, lo)
                share, med, npos, passed = X.cmp((rv, sg), (cv, csg))
                rec[f"{tag}_share"] = share
                rec[f"{tag}_med"] = med
                rec[f"{tag}_seg"] = npos
                rec[f"{tag}_pass"] = bool(passed)
            core = dict(ret=G["ref"][bp]["core"], expo=np.ones_like(G["ref"][bp]["core"]), lo=0)
            mixs = RC.mix(core, dict(ret=mix, expo=np.ones_like(mix), lo=lo), PV.WC, G["s0"], bp / 1e4)["ret"]
            ms = pack_metrics(mixs, z, np.ones_like(mixs), z, G["s0"])
            rec["sat_calmar"] = ms["calmar"]
            rec["sat_cagr"] = ms["cagr"]
            rec["sat_mdd"] = ms["mdd"]
            rec["sat_P1_calmar"] = ms["P1_calmar"]
            rec["sat_P2_calmar"] = ms["P2_calmar"]
            ens_rows.append(rec)
            for ck, r, l, mm in zip(chosen, mrets, mlos, mem_mets):
                ens_rows.append(dict(name=f"MEM_{split}_{ck}", split=split, cost_bp=bp,
                                     n_members=1, members=ck, rule="member",
                                     **{k: v for k, v in mm.items() if not k.startswith("_")}))
            cals_oos, rolls_oos = [], []
            oos_pref = "P2" if split == "A" else "P1"
            for s in range(N_MIX):
                rng = np.random.default_rng(2000 + s + (0 if split == "A" else 10_000) + bp)
                pick = rng.choice(pool_keys, size=len(chosen), replace=False)
                pr = [np.load(CACHE_FR / f"ret_{bp}_{kk.replace('|', '__')}.npy") for kk in pick]
                mx, _ = eq_mix(pr, lo)
                mt = pack_metrics(mx, z, np.ones_like(mx), z, lo)
                cals_oos.append(mt[f"{oos_pref}_calmar"])
                rolls_oos.append(mt[f"{oos_pref}_roll_med"])
            arr_c, arr_r = np.array(cals_oos, float), np.array(rolls_oos, float)
            ens_rows.append(dict(name=f"ENS_{split}_RNDNULL", split=split, cost_bp=bp, n_members=len(chosen),
                                 rule="random_null",
                                 oos_calmar_p10=float(np.nanpercentile(arr_c, 10)),
                                 oos_calmar_p50=float(np.nanpercentile(arr_c, 50)),
                                 oos_calmar_p90=float(np.nanpercentile(arr_c, 90)),
                                 oos_roll_p10=float(np.nanpercentile(arr_r, 10)),
                                 oos_roll_p50=float(np.nanpercentile(arr_r, 50)),
                                 oos_roll_p90=float(np.nanpercentile(arr_r, 90))))
    return ens_rows, mem_info


def class_effects(df, pudf, sigdf, capdf):
    """按类：相对最简三层主口径的平均增量，两段是否同号。"""
    out = []
    for bp in (5, 20):
        base = df[(df.name == "MIN3_P12") & (df.cost_bp == bp)]
        if base.empty:
            continue
        b = base.iloc[0]
        pools = [f"POOL_{p}" for p in NEW_POOLS]
        sub = df[(df.name.isin(pools)) & (df.cost_bp == bp)]
        if len(sub):
            d1 = float((sub["P1_calmar"] - b["P1_calmar"]).mean())
            d2 = float((sub["P2_calmar"] - b["P2_calmar"]).mean())
            out.append(_eff("pool_new_vs_min3", bp, d1, d2, "new pools mean Calmar vs min3"))
        if pudf is not None and len(pudf) and bp == 5:
            for ctl in ("BASE", "P12_HYST60"):
                for met in ("mean_fwd20", "mean_fwd60", "mean_new20"):
                    rows = {}
                    for per in ("P1", "P2"):
                        c = pudf[(pudf.pool == ctl) & (pudf.period == per)]
                        vs = [pudf[(pudf.pool == p) & (pudf.period == per)] for p in NEW_POOLS]
                        if c.empty or any(v.empty for v in vs):
                            rows[per] = np.nan
                        else:
                            rows[per] = float(np.mean([v.iloc[0][met] - c.iloc[0][met] for v in vs]))
                    out.append(_eff(f"pool_{met}_vs_{ctl}", 5, rows["P1"], rows["P2"], f"pool track {met} vs {ctl}"))
        sigs = [f"SIG_{s}" for s in ("PB_BO", "VOL_BO", "BB_SQ", "RS_PERS", "TREND_STAB")]
        sub = df[(df.name.isin(sigs)) & (df.cost_bp == bp)]
        if len(sub):
            d1 = float((sub["P1_calmar"] - b["P1_calmar"]).mean())
            d2 = float((sub["P2_calmar"] - b["P2_calmar"]).mean())
            out.append(_eff("signal_new_vs_min3_calmar", bp, d1, d2, "new signals mean Calmar vs DONCH min3"))
        if sigdf is not None and len(sigdf) and bp == 5:
            for met in ("n20_mean", "n20_win", "n60_mean"):
                rows = {}
                ctl = sigdf[(sigdf.name == "MIN3_P12")]
                for per in ("P1", "P2"):
                    c = ctl[ctl.period == per]
                    vs = sigdf[(sigdf.name.isin(sigs)) & (sigdf.period == per)]
                    if c.empty or vs.empty:
                        rows[per] = np.nan
                    else:
                        rows[per] = float(vs[met].mean() - c.iloc[0][met])
                out.append(_eff(f"signal_{met}_vs_donch", 5, rows["P1"], rows["P2"], f"signal track {met} vs DONCH"))
        caps = ["CAP_SECW40", "CAP_CORRCAP"]
        sub = df[(df.name.isin(caps)) & (df.cost_bp == bp)]
        if len(sub):
            d1 = float((sub["P1_calmar"] - b["P1_calmar"]).mean())
            d2 = float((sub["P2_calmar"] - b["P2_calmar"]).mean())
            out.append(_eff("cap_new_vs_cap2_calmar", bp, d1, d2, "new caps mean Calmar vs CAP2"))
        sizes = ["SIZE_DSVOL10", "SIZE_CVAR95_15", "SIZE_K358"]
        sub = df[(df.name.isin(sizes)) & (df.cost_bp == bp)]
        if len(sub):
            d1 = float((sub["P1_calmar"] - b["P1_calmar"]).mean())
            d2 = float((sub["P2_calmar"] - b["P2_calmar"]).mean())
            out.append(_eff("size_new_vs_vt10_calmar", bp, d1, d2, "new sizing mean Calmar vs VT10"))
            d1v = float((sub["P1_mdd"] - b["P1_mdd"]).mean())
            d2v = float((sub["P2_mdd"] - b["P2_mdd"]).mean())
            out.append(_eff("size_new_vs_vt10_mdd", bp, d1v, d2v, "new sizing mean MDD vs VT10 (positive=shallower)"))
        sub = df[(df.family == "stack") & (df.cost_bp == bp)]
        if len(sub):
            d1 = float((sub["P1_calmar"] - b["P1_calmar"]).mean())
            d2 = float((sub["P2_calmar"] - b["P2_calmar"]).mean())
            out.append(_eff("stack_vs_min3_calmar", bp, d1, d2, "pre-specified stacks mean vs min3"))
        for base, caln in (("MIN3", "MIN3_P12"), ("S4", "S4")):
            cal = df[(df.name == caln) & (df.cost_bp == bp)]
            if cal.empty:
                continue
            c0 = cal.iloc[0]
            sub = df[(df.family == "cost") & (df.name.str.startswith(f"COST_{base}_")) & (df.cost_bp == bp)]
            if sub.empty:
                continue
            d1 = float((sub["P1_calmar"] - c0["P1_calmar"]).mean())
            d2 = float((sub["P2_calmar"] - c0["P2_calmar"]).mean())
            out.append(_eff(f"cost_{base}_vs_cal10_calmar", bp, d1, d2, f"cost-aware mean vs {caln} calendar"))
    seen = set()
    uniq = []
    for r in out:
        key = (r["cls"], r["cost_bp"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def _eff(cls, bp, d1, d2, note):
    same = (np.isfinite(d1) and np.isfinite(d2) and ((d1 > 0 and d2 > 0) or (d1 < 0 and d2 < 0)))
    both_pos = bool(np.isfinite(d1) and np.isfinite(d2) and d1 > 0 and d2 > 0)
    flip = bool(np.isfinite(d1) and np.isfinite(d2) and (d1 > 0) != (d2 > 0) and d1 != 0 and d2 != 0)
    return dict(cls=cls, cost_bp=bp, d_P1=d1, d_P2=d2, same_sign=bool(same), both_pos=both_pos,
                flip=flip, useful=bool(both_pos), note=note)


def build_verdict(df, dfc, dfs, effects, ens_rows, mem_info):
    recs = {}
    for name in ("MIN3_P12", "MIN3_BASE"):
        recs[name] = {}
        for bp in (5, 20):
            row = df[(df.name == name) & (df.cost_bp == bp)].iloc[0]
            recs[name][str(bp)] = dict(
                s4_pass=bool(row.s4_pass), br_pass=bool(row.br_pass), ch_pass=bool(row.ch_pass),
                vs_s4_struct=bool(row.vs_s4_struct_pass), vs_br_struct=bool(row.vs_br_struct_pass),
                vs_ch_struct=bool(row.vs_ch_struct_pass),
                calmar=float(row.calmar), cagr=float(row.cagr), mdd=float(row.mdd),
                P1_calmar=float(row.P1_calmar), P2_calmar=float(row.P2_calmar),
            )
    beat = df[(df.vs_ch_struct_pass == True) & (df.name != "MIN3_P12")]
    replace_names = sorted(set(beat.name.unique().tolist())) if len(beat) else []
    both_cost = []
    for nm in replace_names:
        sub = df[(df.name == nm) & (df.vs_ch_struct_pass == True)]
        if set(sub.cost_bp) >= {5, 20}:
            both_cost.append(nm)
    useful = [e for e in effects if e.get("useful") and e.get("cost_bp") in (5, 20)]
    return dict(
        n_configs=len(configs()),
        n_rnd=N_RND,
        n_trials=len(configs()) * 2 + N_RND * 2,
        replace_champion=bool(both_cost),
        replace_names=both_cost,
        min3=recs,
        useful_classes=useful,
        ensemble_members=mem_info,
    )


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    RUN.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    if cmd == "check":
        cmd_check()
    elif cmd == "run":
        cmd_run()
    elif cmd == "post":
        cmd_post()
    elif cmd == "count":
        cs = configs()
        print("n_configs", len(cs))
        from collections import Counter
        print(Counter(c["family"] for c in cs))
        for c in cs:
            print(f"  {c['name']:24s} {c['pool']:12s} {c['sig']:12s} {c['cap']:8s} {c['size']:10s} {c['family']}")
    else:
        raise SystemExit(f"unknown cmd {cmd}")


if __name__ == "__main__":
    main()
