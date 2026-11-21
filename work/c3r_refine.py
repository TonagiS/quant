#!/usr/bin/env python3
"""combo3_refine（2026-11-21）：combo3 最优 P12_HYST60|DONCH|CAP2|VT10 的真·分段样本外检验与细化。
规则见 runs/2026-11-21_combo3_refine/FREEZE.md。总收益面板 v2（trfix 7b3ef1d 之后）。样本内为主。
用法：python3 work/c3r_refine.py check | oos | variants | post"""
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
import c3x_combo as CX  # noqa: E402

PV, K, RC, E = CX.PV, CX.K, CX.RC, CX.E
RUN = PV.ROOT / "runs" / "2026-11-21_combo3_refine"
CACHE = PV.ROOT / "data" / "cache_combo3_refine"
COSTS = (0.0005, 0.0020)
BEST = ("P12_HYST60", "DONCH", "CAP2", "VT10")
S4_KEY = CX.S4_KEY
PASS_SHARE, PASS_MED, PASS_SEG = 0.70, 0.05, 3
OOS_SPLIT = pd.Timestamp("2020-01-01")
WF_YEARS = list(range(2017, 2027))
G = {}


# ================================================================== 第 1 部分：样本外（只用 combo3 的 768 条日收益，全部是因果计算）
def month_starts(a, b):
    cal = PV.G["cal"]
    me = pd.Series(np.arange(len(cal)), index=cal)
    ms = me[(cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(b))]
    return ms.groupby(ms.index.to_period("M")).min().to_list()


def idx(d, side="left"):
    return int(PV.G["cal"].searchsorted(pd.Timestamp(d), side=side))


def window_rank(R, a, b, min_years=3.0, segs=None):
    """combo3 选择规则限定在窗口 [a,b]：起点 = a..(b−min_years) 每月首个交易日，卡玛从起点算到 b；
    按对 S4 的中位卡玛差降序，同分按滚动中位卡玛降序（排除 S4 本身）。返回 DataFrame。"""
    hi = idx(b, "right")
    last = pd.Timestamp(b) - pd.DateOffset(months=int(round(min_years * 12)))
    st = month_starts(a, last)
    def roll(ret, lo):
        return np.array([PV.calmar(ret[s:hi]) if lo <= s else np.nan for s in st])
    s4 = R[CX.key(*S4_KEY)]
    r4 = roll(s4["ret"], s4["lo"])
    rows = []
    for k, d in R.items():
        if k == CX.key(*S4_KEY):
            continue
        rv = roll(d["ret"], d["lo"])
        dd = rv - r4
        row = dict(combo=k, n_starts=len(st), s4_share=float(np.sum(dd > 0) / len(dd)) if len(dd) else np.nan,
                   s4_med=float(np.nanmedian(dd)) if np.isfinite(dd).any() else np.nan,
                   roll_med=float(np.nanmedian(rv)) if np.isfinite(rv).any() else np.nan)
        if segs:
            npos = 0
            for g in segs:
                l0, h0 = idx(PV.SEGS[g][0]), idx(PV.SEGS[g][1], "right")
                npos += int(PV.calmar(d["ret"][l0:h0]) > PV.calmar(s4["ret"][l0:h0]))
            row["s4_seg"] = npos
        rows.append(row)
    T = pd.DataFrame(rows).sort_values(["s4_med", "roll_med"], ascending=False, na_position="last").reset_index(drop=True)
    T["rank"] = np.arange(1, len(T) + 1)
    return T


def perf_win(ret, a, b):
    m = E.perf(np.asarray(ret)[idx(a): idx(b, "right")])
    return {k: float(m[k]) for k in ("cagr", "mdd", "calmar", "sharpe")}


def oos_block(R, broad, sel, a, b, tag, bp):
    """sel: IS 排名表；在 [a,b] 上评估。"""
    rows = []
    allc = {k: perf_win(d["ret"], a, b)["calmar"] for k, d in R.items()}
    order = sorted(allc, key=lambda k: -np.nan_to_num(allc[k], nan=-9))
    oos_rank = {k: i + 1 for i, k in enumerate(order)}
    for i, k in enumerate(list(sel.combo.iloc[:5])):
        m = perf_win(R[k]["ret"], a, b)
        rows.append(dict(split=tag, cost_bp=bp, role=f"IS_rank{i+1}", combo=k, **m, oos_calmar_rank=oos_rank[k], n=len(R)))
    top5 = np.mean([R[k]["ret"] for k in sel.combo.iloc[:5]], axis=0)
    rows.append(dict(split=tag, cost_bp=bp, role="IS_top5_EW", combo="top5 等权（日再平衡近似）", **perf_win(top5, a, b), n=len(R)))
    k = CX.key(*BEST)
    rows.append(dict(split=tag, cost_bp=bp, role="combo3_fullsample_best", combo=k, **perf_win(R[k]["ret"], a, b),
                     oos_calmar_rank=oos_rank[k], is_rank=int(sel.loc[sel.combo == k, "rank"].iloc[0]), n=len(R)))
    k4 = CX.key(*S4_KEY)
    rows.append(dict(split=tag, cost_bp=bp, role="S4", combo=k4, **perf_win(R[k4]["ret"], a, b), oos_calmar_rank=oos_rank[k4], n=len(R)))
    rows.append(dict(split=tag, cost_bp=bp, role="BROAD_VT15", combo="宽基+MA60+VT15", **perf_win(broad, a, b)))
    # OOS 内的滚动对 S4（同规则，起点限定在 OOS 窗口）
    so = window_rank(R, a, b, 3.0 if (pd.Timestamp(b) - pd.Timestamp(a)).days > 365 * 4.5 else 2.0)
    for r in rows:
        if r["combo"] in set(so.combo):
            x = so[so.combo == r["combo"]].iloc[0]
            r.update(oos_roll_s4_med=x.s4_med, oos_roll_s4_share=x.s4_share, oos_roll_rank=int(x["rank"]))
    return rows


def oos(t0):
    rows, sel_out, wf_rows = [], [], []
    for cost in COSTS:
        bp = int(round(cost * 1e4))
        R = CX.load_cells(bp)
        broad = np.load(CX.CACHE / f"ref_{bp}.npz")["broad"]
        # A) 2013-19 选 → 2020-26 测
        selA = window_rank(R, "2014-06-01", "2019-12-31", 3.0, segs=["2014-16", "2017-19"])
        selA.assign(cost_bp=bp, split="IS2014-19").head(30).to_csv(RUN / f"is_rank_2014_19_{bp}bp.csv", index=False)
        rows += oos_block(R, broad, selA, "2020-01-01", "2026-11-13", "A:IS2014-19→OOS2020-26", bp)
        # B) 反向：2020-26 选 → 2014-19 测
        selB = window_rank(R, "2020-01-01", "2026-11-13", 3.0, segs=["2020-22", "2023-26"])
        selB.assign(cost_bp=bp, split="IS2020-26").head(30).to_csv(RUN / f"is_rank_2020_26_{bp}bp.csv", index=False)
        rows += oos_block(R, broad, selB, "2014-06-03", "2019-12-31", "B:IS2020-26→OOS2014-19", bp)
        sel_out.append(dict(cost_bp=bp, A_top5=list(selA.combo.iloc[:5]), B_top5=list(selB.combo.iloc[:5]),
                            best_rank_in_A=int(selA.loc[selA.combo == CX.key(*BEST), "rank"].iloc[0]),
                            best_rank_in_B=int(selB.loc[selB.combo == CX.key(*BEST), "rank"].iloc[0])))
        # C) 逐年扩展窗口重选：Y 年持有用 2014-06..(Y-1)-12-31 选出的第 1 名（起点至少 2 年）
        cal = PV.G["cal"]
        chain = np.zeros(len(cal)); prev = None; picks = []
        for Y in WF_YEARS:
            sel = window_rank(R, "2014-06-01", f"{Y-1}-12-31", 2.0)
            w = sel.combo.iloc[0]
            l0, h0 = idx(f"{Y}-01-01"), idx(f"{Y}-12-31", "right")
            chain[l0:h0] = R[w]["ret"][l0:h0]
            if prev is not None and w != prev:            # 换策略：按卫星敞口全换一次扣双边成本
                chain[l0] -= cost * (float(R[prev]["expo"][l0 - 1]) + float(R[w]["expo"][l0]))
            picks.append(dict(cost_bp=bp, year=Y, pick=w, n_starts=int(sel.n_starts.iloc[0]),
                              best_rank=int(sel.loc[sel.combo == CX.key(*BEST), "rank"].iloc[0]),
                              pick_year_ret=float(np.prod(1 + chain[l0:h0]) - 1),
                              s4_year_ret=float(np.prod(1 + R[CX.key(*S4_KEY)]["ret"][l0:h0]) - 1)))
            prev = w
        np.save(CACHE / f"wf_chain_{bp}.npy", chain)
        a, b = "2017-01-01", "2026-11-13"
        for nm, r in {"WF_expanding": chain, "S4": R[CX.key(*S4_KEY)]["ret"], "BROAD_VT15": broad,
                      "combo3_fullsample_best(样本内)": R[CX.key(*BEST)]["ret"]}.items():
            wf_rows.append(dict(cost_bp=bp, arm=nm, **perf_win(r, a, b)))
        pd.DataFrame(picks).to_csv(RUN / f"wf_picks_{bp}bp.csv", index=False)
        print("oos", bp, round(time.time() - t0), flush=True)
    pd.DataFrame(rows).to_csv(RUN / "oos_split.csv", index=False)
    pd.DataFrame(wf_rows).to_csv(RUN / "oos_walkforward.csv", index=False)
    json.dump(sel_out, open(RUN / "oos_selection.json", "w"), ensure_ascii=False, indent=1)


# ================================================================== 第 2–6 部分：变体
def setup():
    if G:
        return
    CX.setup_parent()
    lists = CX.G["lists"]["P12_HYST60"]
    ever = sorted({c for v in lists.values() for c in v})
    G["close"] = PV.G["C"][ever]
    G["M"] = CX.G["M"]["P12_HYST60"]
    h3 = pd.read_csv(PV.ROOT / "data/cache_index_proxy/H00300.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").close.sort_index()
    cal = PV.G["cal"]
    h3c = h3.reindex(cal.union(h3.index)).ffill().reindex(cal)
    G["below200"] = (h3c < h3c.rolling(200, min_periods=200).mean()).to_numpy()


def apply_cap(close, rm, cand):
    rmv, cv = rm.to_numpy(), cand.to_numpy().copy()
    sv = np.array([PV.G["sec"].get(c, "") for c in close.columns])
    for i in range(len(cv)):
        ix = np.flatnonzero(cv[i] & ~np.isnan(rmv[i]))
        if len(ix) <= 2:
            continue
        cnt = {}
        for j in ix[np.argsort(-rmv[i, ix])]:
            cnt[sv[j]] = cnt.get(sv[j], 0) + 1
            if cnt[sv[j]] > 2:
                cv[i, j] = False
    return pd.DataFrame(cv, index=close.index, columns=close.columns)


def chan_rank(close, valid, n):
    hi = close.rolling(n, min_periods=n).max(); lo = close.rolling(n, min_periods=n).min()
    return ((close - lo) / (hi - lo).where(hi > lo)).where(valid).rank(axis=1, pct=True)


def topk_mask(rm, cand, k=5):
    r = rm.where(cand)
    return r.rank(axis=1, ascending=False, method="first") <= k


def feats_sig(sig):
    """sig ∈ DONCH / DONCH_AVG / BLEND3 / VOTE2；全部用 P12_HYST60 池与 CAP2。"""
    close, M = G["close"], G["M"]
    if sig == "DONCH":
        return CX.feats(close, M, "DONCH", "CAP2")
    base = CX.feats(close, M, "DONCH", "NOCAP")
    valid = base["valid"]
    if sig == "DONCH_AVG":      # 4 组窗口（40/80、60/120、90/180、120/250）的通道位置排名，各组先平均再在 4 组间平均；缺窗口的组跳过
        pairs = [(40, 80), (60, 120), (90, 180), (120, 250)]
        rks = {n: chan_rank(close, valid, n) for n in {x for p in pairs for x in p}}
        ps = [(rks[a] + rks[b]) / 2.0 for a, b in pairs]
        rm = pd.concat(ps).groupby(level=0).mean().reindex(close.index)   # 跳过 NaN 的组
        rm = rm.where(valid)
        cand = valid & rm.notna() & base["cand"]          # 候选条件不变：收盘 > 60 日通道中线
    else:
        fs = {s: CX.feats(close, M, s, "NOCAP") for s in ("DONCH", "RAM", "S2")}
        rks = [fs[s]["rm"].rank(axis=1, pct=True) for s in fs]
        blend = sum(rks) / 3.0
        above = close > base["ma60"]
        if sig == "BLEND3":     # 三个分数各自截面百分位的均值；候选 = 三个信号自身候选条件过半（S2/RAM 同为收盘>MA60 → 等价于收盘>MA60）
            rm, cand = blend.where(valid), valid & blend.notna() & above
        elif sig == "VOTE2":    # 每个信号在自身候选+板块上限下的前 5；被 ≥2 个信号选中的才可入选，按 BLEND3 分数排序；不足 5 只留现金
            votes = 0
            for s in fs:
                c2 = apply_cap(close, fs[s]["rm"], fs[s]["cand"])
                votes = votes + topk_mask(fs[s]["rm"], c2).astype(int)
            rm, cand = blend.where(valid), valid & blend.notna() & (votes >= 2)
        else:
            raise KeyError(sig)
    f = dict(base)
    f["rm"] = rm
    f["cand"] = apply_cap(close, rm, cand.fillna(False))
    return f


class RegimeVT(E.Rule):
    """VT10 只在沪深300（H00300 全收益代理）收盘 < MA200 时启用，否则仓位 1。"""
    requires_base = True

    def __init__(self, below, every=10):
        self.vt = E.VolTarget(0.10, 60, 40, every=every)
        self.below, self.every = below, every
        self.name = "VT10_REGIME200"

    def scale(self, j, ctx):
        return self.vt.scale(j, ctx) if bool(self.below[j]) else 1.0


def replay(close, f, w_hist, cost, lo, band=0.0, trail=None, dch=None):
    """按引擎的 w_hist（每日收盘后权重）重放：port_i = Σ w_{i-1}·r_i − cost·Σ|Δw|。
    band：执行日只对"调整幅度 < band 的加减仓"不交易（新进与清仓照做）。
    trail：持仓自买入后最高收盘回撤 ≥ trail 当日收盘卖出；dch：收盘 < 前 dch 日最低收盘当日收盘卖出。
    止损卖出后只在下一个执行日（引擎换仓日）按目标重新买入；跌停/停牌日卖不出，顺延。"""
    ret = f["ret"].to_numpy(); r0 = np.nan_to_num(ret, nan=0.0)
    C = close.to_numpy()
    T = len(w_hist)
    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_dn = ret <= -lim * 0.98
    lowN = close.shift(1).rolling(dch, min_periods=dch).min().to_numpy() if dch else None
    w = {}; hi = {}
    port = np.zeros(T); turn = np.zeros(T); expo = np.zeros(T)
    for i in range(T):
        gross = sum(x * r0[i, c] for c, x in w.items())
        tgt = w_hist[i]
        changed = (i > 0 and tgt != w_hist[i - 1]) or (i == 0 and tgt)
        new = dict(w)
        if changed:
            new = {}
            for c in set(w) | set(tgt):
                a, b = w.get(c, 0.0), tgt.get(c, 0.0)
                if a > 0 and b > 0 and abs(b - a) < band:
                    new[c] = a
                elif b > 0:
                    new[c] = b
            for c in new:
                if c not in w:
                    hi[c] = C[i, c]
        # 止损（收盘判断收盘卖）
        for c in list(new):
            px = C[i, c]
            if not np.isfinite(px):
                continue
            hi[c] = max(hi.get(c, px), px)
            hit = (trail is not None and px <= hi[c] * (1 - trail)) or (dch is not None and np.isfinite(lowN[i, c]) and px < lowN[i, c])
            if hit and not lim_dn[i, c] and not (changed and c in tgt and c not in w):
                new.pop(c)
        tv = sum(abs(new.get(c, 0.0) - w.get(c, 0.0)) for c in set(w) | set(new))
        port[i] = gross - cost * tv
        turn[i] = tv
        w = new
        expo[i] = sum(w.values())
        for c in list(hi):
            if c not in w:
                hi.pop(c)
    return dict(ret=port, turn=turn, expo=expo, lo=lo)


VARIANTS = ["BEST", "DONCH_AVG", "BLEND3", "VOTE2", "BUF8", "BUF10", "BAND2", "BAND5", "REB15", "REB20",
            "VT8", "VT12", "VT10_REGIME200", "TRAIL12", "DCH20_EXIT"]
CATEGORY = {"DONCH_AVG": "signal_avg", "BLEND3": "signal_blend", "VOTE2": "signal_blend",
            "BUF8": "turnover", "BUF10": "turnover", "BAND2": "turnover", "BAND5": "turnover", "REB15": "turnover", "REB20": "turnover",
            "VT8": "vt", "VT12": "vt", "VT10_REGIME200": "vt", "TRAIL12": "stop", "DCH20_EXIT": "stop"}


def run_variant(name, cost, spec=None):
    """spec 可组合：dict(sig=, buf=, band=, reb=, vt=, trail=, dch=)。"""
    sp = dict(sig="DONCH", buf=None, band=0.0, reb=10, vt="VT10", trail=None, dch=None)
    sp.update(spec or VSPEC[name])
    close = G["close"]
    f = feats_sig(sp["sig"])
    lo = f["start"]
    cfg = E.Config(k=5, reb=sp["reb"], reb_mode="rolling", cost=cost, buffer_n=sp["buf"])
    r0 = E.simulate(close, f, cfg)
    f2 = dict(f); f2["base_ret"] = r0["ret"]
    ev = sp["reb"]
    if sp["vt"] == "VT10_REGIME200":
        rule = RegimeVT(G["below200"], every=ev)
    else:
        rule = E.VolTarget(int(sp["vt"][2:]) / 100, 60, 40, every=ev)
    keep = bool(sp["band"] or sp["trail"] or sp["dch"])
    rr = E.simulate(close, f2, replace(cfg, rule=rule), keep_weights=keep)
    if keep:
        return replay(close, f, rr["w_hist"], cost, lo, band=sp["band"], trail=sp["trail"], dch=sp["dch"])
    return CX.pack(rr, lo)


VSPEC = {"BEST": {}, "DONCH_AVG": dict(sig="DONCH_AVG"), "BLEND3": dict(sig="BLEND3"), "VOTE2": dict(sig="VOTE2"),
         "BUF8": dict(buf=8), "BUF10": dict(buf=10), "BAND2": dict(band=0.02), "BAND5": dict(band=0.05),
         "REB15": dict(reb=15), "REB20": dict(reb=20), "VT8": dict(vt="VT8"), "VT12": dict(vt="VT12"),
         "VT10_REGIME200": dict(vt="VT10_REGIME200"), "TRAIL12": dict(trail=0.12), "DCH20_EXIT": dict(dch=20)}


def task_var(args):
    name, cost, spec = args
    setup()
    d = run_variant(name, cost, spec)
    np.savez_compressed(CACHE / f"var_{name}_{int(round(cost*1e4))}.npz", ret=d["ret"], turn=d["turn"], expo=d["expo"], lo=np.array([d["lo"]]))
    return name, cost


def load_var(name, bp):
    z = np.load(CACHE / f"var_{name}_{bp}.npz")
    return dict(ret=z["ret"], turn=z["turn"], expo=z["expo"], lo=int(z["lo"][0]))


def evaluate(names, tag):
    s0 = PV.starts()[0]
    rows = []
    for cost in COSTS:
        bp = int(round(cost * 1e4))
        R = CX.load_cells(bp); ref = np.load(CX.CACHE / f"ref_{bp}.npz")
        core = dict(ret=ref["core"], expo=np.ones(len(ref["core"])), lo=PV.G["lo0"])
        s4 = R[CX.key(*S4_KEY)]
        best = load_var("BEST", bp)
        rv4, rvB = CX.roll_vec(s4["ret"], s4["lo"]), CX.roll_vec(best["ret"], best["lo"])
        rbr = CX.roll_vec(ref["broad"], int(ref["broad_lo"][0]))
        c4 = CX.roll_vec(s4["ret"], s4["lo"], "combo", core, cost)
        cB = CX.roll_vec(best["ret"], best["lo"], "combo", core, cost)
        for nm in names:
            d = load_var(nm, bp)
            rv = CX.roll_vec(d["ret"], d["lo"])
            m = E.perf(d["ret"][s0:])
            a, b, c = CX.cmp(rv, rvB), CX.cmp(rv, rv4), CX.cmp(rv, rbr)
            cv = CX.roll_vec(d["ret"], d["lo"], "combo", core, cost)
            mix = RC.mix(core, dict(ret=d["ret"], expo=d["expo"], lo=d["lo"]), PV.WC, s0, cost)["ret"]
            mm = E.perf(mix[s0:])
            sa, sb = CX.cmp(cv, cB), CX.cmp(cv, c4)
            rows.append(dict(set=tag, variant=nm, category=CATEGORY.get(nm, "-"), cost_bp=bp, cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"],
                             sharpe=m["sharpe"], turn=float(np.mean(d["turn"][s0:]) * 252), expo=float(np.mean(d["expo"][s0:])),
                             roll_med=float(np.nanmedian(rv[0])),
                             vsB_share=a[0], vsB_med=a[1], vsB_seg=a[2], vsB_pass=a[3],
                             vsS4_share=b[0], vsS4_med=b[1], vsS4_seg=b[2], vsS4_pass=b[3],
                             vsBR_share=c[0], vsBR_med=c[1], vsBR_seg=c[2], vsBR_pass=c[3],
                             **{f"seg_{g}": rv[1][i] for i, g in enumerate(PV.SEGS)},
                             sat_cagr=mm["cagr"], sat_mdd=mm["mdd"], sat_calmar=mm["calmar"],
                             sat_vsB_share=sa[0], sat_vsB_med=sa[1], sat_vsB_seg=sa[2], sat_vsB_pass=sa[3],
                             sat_vsS4_share=sb[0], sat_vsS4_med=sb[1], sat_vsS4_seg=sb[2], sat_vsS4_pass=sb[3]))
    return pd.DataFrame(rows)


def component_pass(T):
    """冻结：一般组件 = 5bp 与 20bp 都对 BEST 通过；换手类 = 20bp 对 BEST 通过且 5bp 对 BEST 中位Δ ≥ −0.05。"""
    out = {}
    for nm, g in T.groupby("variant"):
        if nm == "BEST":
            continue
        p5 = g[g.cost_bp == 5].iloc[0]; p20 = g[g.cost_bp == 20].iloc[0]
        if CATEGORY[nm] == "turnover":
            ok = bool(p20.vsB_pass) and p5.vsB_med >= -0.05
        else:
            ok = bool(p5.vsB_pass) and bool(p20.vsB_pass)
        out[nm] = ok
    return out


def refined_spec(T, passed):
    """每个类别最多取一个通过的组件：类别内按 5bp 对 BEST 中位Δ 最高（换手类按 20bp）。"""
    spec, chosen = {}, []
    for cat in ("signal_avg", "signal_blend", "turnover", "vt", "stop"):
        cands = [n for n, ok in passed.items() if ok and CATEGORY[n] == cat]
        if not cands:
            continue
        bp = 20 if cat == "turnover" else 5
        sc = {n: float(T[(T.variant == n) & (T.cost_bp == bp)].vsB_med.iloc[0]) for n in cands}
        n = max(sc, key=sc.get)
        chosen.append(n)
        spec.update(VSPEC[n])
    if "DONCH_AVG" in chosen and any(CATEGORY[c] == "signal_blend" for c in chosen):
        spec["sig"] = [VSPEC[c]["sig"] for c in chosen if CATEGORY[c] == "signal_blend"][0]   # 两个信号类都过时，用混合信号（混合本身含 DONCH）
    return spec, chosen


def main(mode):
    RUN.mkdir(parents=True, exist_ok=True); CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if mode == "check":
        setup()
        z = np.load(CX.CACHE / "cell_P12_HYST60_DONCH_CAP2_5.npz")
        d = run_variant("BEST", 0.0005)
        print("BEST 复现 combo3 缓存 max abs diff:", float(np.max(np.abs(d["ret"] - z["VT10"]))))
        close = G["close"]; f = feats_sig("DONCH")
        cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=0.0005)
        r0 = E.simulate(close, f, cfg); f2 = dict(f); f2["base_ret"] = r0["ret"]
        rr = E.simulate(close, f2, replace(cfg, rule=E.VolTarget(0.10, 60, 40, every=10)), keep_weights=True)
        rp = replay(close, f, rr["w_hist"], 0.0005, f["start"])
        print("replay(无改动) vs 引擎 max abs diff:", float(np.max(np.abs(rp["ret"] - rr["ret"]))))
        for s in ("DONCH_AVG", "BLEND3", "VOTE2"):
            fs = feats_sig(s)
            print(s, "avg cand/day", round(float(fs["cand"].sum(axis=1)[fs["start"]:].mean()), 2), "start", PV.G["cal"][fs["start"]].date())
        print("below200 share", round(float(np.nanmean(G["below200"])), 3), "t", round(time.time() - t0))
        return
    if mode == "oos":
        PV.setup()
        oos(t0)
        return
    if mode == "variants":
        setup()
        tasks = [(n, c, None) for c in COSTS for n in VARIANTS]
        with Pool(8) as pl:
            for r in pl.imap_unordered(task_var, tasks):
                print("var", r, round(time.time() - t0), flush=True)
        T = evaluate(VARIANTS, "single")
        passed = component_pass(T)
        spec, chosen = refined_spec(T, passed)
        json.dump(dict(passed=passed, chosen=chosen, spec={k: v for k, v in spec.items()}), open(RUN / "refined_spec.json", "w"), ensure_ascii=False, indent=1)
        if chosen:
            for c in COSTS:
                task_var(("REFINED", c, spec))
            T = pd.concat([T, evaluate(["REFINED"], "refined")])
        T.to_csv(RUN / "variants.csv", index=False)
        print("variants done", passed, chosen, round(time.time() - t0))
        return


if __name__ == "__main__":
    main(sys.argv[1])
