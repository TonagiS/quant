#!/usr/bin/env python3
"""组合层：第 0 步口径对照 + VT15-TG / S2-ENS / S2ENS-VT15TG。规则见 runs/2026-11-20_tr_ens_trendgate/PREREG.md（已冻结）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "work"))
import pit_pool_backtest as PB  # noqa: E402  (导入 engine / pit_pool_data / layered_v1)
import tr_ens_data as TD  # noqa: E402
from single_asset_harness import run_pos  # noqa: E402

E, L = PB.E, PB.L
RUN = ROOT / "runs" / "2026-11-20_tr_ens_trendgate"
END = PB.END
COST = 0.0005
START_W = PB.START_W
SUB_W = PB.SUB_W
CRASH = ["Y2015", "Y2018", "S2021_24"]
H_BASE, H_ENS = (20, 60, 120), (20, 60, 120, 250)


def log(m):
    print(m, flush=True)


def feats(close, member, start_date, horizons=H_BASE, min_names=8):
    """engine.features 口径；valid 与'当日在池'相与；horizons 可加 250。"""
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
    cand = valid & rm.notna() & (close > ma60)
    enough = (valid.sum(axis=1) >= min_names) & rm.notna().any(axis=1)
    enough &= close.index >= pd.Timestamp(start_date)
    start = int(np.argmax(enough.to_numpy()))
    return {"ret": ret, "vol20": vol20, "mom": mom, "ma60": ma60, "valid": valid, "rm": rm,
            "cand": cand, "start": start, "bench": (1.0 + ret.mean(axis=1).fillna(0.0)).cumprod()}


class GateVT(E.VolTarget):
    """VT15；门关时敞口 = min(VT15, 0.5)。gate: bool ndarray，按日历行号。"""

    def __init__(self, gate, every=10, cap_off=0.5):
        super().__init__(0.15, 60, 40, every=every)
        self.gate, self.cap_off = gate, cap_off
        self.name = "VT15-TG"

    def scale(self, j, ctx):
        s = super().scale(j, ctx)
        if not self.gate[j]:
            s = min(s, self.cap_off)
        return s


def gate_from_level(level: pd.Series):
    ma = level.rolling(200, min_periods=200).mean()
    sl = ma / ma.shift(20) - 1
    on = (level > ma) & (sl > 0)
    on[ma.isna() | sl.isna()] = True
    return on


def gate_from_strategy(close, M, horizons, cost):
    f0 = feats(close, M, "2010-01-01", horizons)
    r = E.simulate(close, f0, E.Config(k=5, reb=10, reb_mode="rolling", cost=cost))["ret"]
    lo = f0["start"]
    nav = pd.Series(np.cumprod(1 + r[lo:]), index=close.index[lo:])
    g = pd.Series(True, index=close.index)
    g.loc[nav.index] = gate_from_level(nav)
    return g.to_numpy(), str(close.index[lo].date())


ARMS_MAIN = ["PIT-S2", "PIT-VT15", "PIT-VT15-5d", "VT15-TG", "VT15-TG-300", "S2-ENS", "S2ENS-VT15", "S2ENS-VT15TG"]
JUDGED = ["VT15-TG", "S2ENS-VT15", "S2ENS-VT15TG"]


def run_arm(close, M, sdate, arm, gates, cost=COST):
    ens = arm.startswith("S2-ENS") or arm.startswith("S2ENS")
    f = feats(close, M, sdate, H_ENS if ens else H_BASE)
    if arm in ("PIT-S2", "S2-ENS"):
        res = E.simulate(close, f, E.Config(k=5, reb=10, reb_mode="rolling", cost=cost))
    else:
        reb = 5 if arm == "PIT-VT15-5d" else 10
        if arm in ("PIT-VT15", "PIT-VT15-5d", "S2ENS-VT15"):
            rule = E.VolTarget(0.15, 60, 40, every=reb)
        elif arm == "VT15-TG":
            rule = GateVT(gates["s2"])
        elif arm == "VT15-TG-300":
            rule = GateVT(gates["300"])
        elif arm == "S2ENS-VT15TG":
            rule = GateVT(gates["ens"])
        else:
            raise KeyError(arm)
        res = E.run_with_base(close, f, E.Config(k=5, reb=reb, reb_mode="rolling", cost=cost, rule=rule))
    idx = close.index
    return dict(ret=pd.Series(res["ret"], index=idx), turn=pd.Series(res["turn"], index=idx),
                expo=pd.Series(res["expo"], index=idx), lo=f["start"])


def ew_run(R, lists, cal, lo, cost):
    tg = {}
    for s in PB.month_end_pos(cal, lo):
        d = cal[s]
        names = lists[max(k for k in lists if k <= d)]
        tg[s] = pd.Series(1.0 / len(names), index=names)
    net, turn = PB.sim_targets(R, tg, lo, cost)
    return dict(ret=pd.Series(net, index=cal), turn=pd.Series(turn, index=cal), expo=pd.Series(1.0, index=cal), lo=lo)


def div_run(Rd, Apos, cal, lo, cost=COST):
    tg, _ = PB.div_targets(Rd, Apos, lo, "IV")
    net, turn = PB.sim_targets(Rd, tg, lo, cost)
    return dict(ret=pd.Series(net, index=cal), turn=pd.Series(turn, index=cal), expo=pd.Series(1.0, index=cal), lo=lo)


def metr(d, mask):
    return PB.metr(d["ret"][mask].to_numpy(), d["turn"][mask].to_numpy(), d["expo"][mask].to_numpy())


def main():
    P = TD.build()
    info = P["info"]
    cal_all = P["close_tr"].index
    cal = cal_all[(cal_all >= "2010-01-01") & (cal_all <= END)]
    lists, mem_df = PB.pit_members(P)
    mem_df.to_csv(RUN / "pit_pool_members.csv", index=False)
    ever = sorted(mem_df.code.unique())
    n_naive_ever = int(sum(str(info.get(c, "")).startswith("qfq_naive") for c in ever))
    log(f"PIT ever {len(ever)}, naive among them {n_naive_ever}")
    closeT = P["close_tr"].reindex(cal)[ever]
    closeN = P["close_naive"].reindex(cal)[ever]
    M = PB.member_mask(lists, cal, ever)
    RT, RN = closeT.pct_change(fill_method=None), closeN.pct_change(fill_method=None)

    h3 = pd.read_csv(ROOT / "data/cache_index_proxy/H00300.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").close.sort_index()
    h3c = h3.reindex(cal.union(h3.index)).ffill().reindex(cal)
    gates = {"300": gate_from_level(h3c).to_numpy()}
    gates["s2"], g0 = gate_from_strategy(closeT, M, H_BASE, COST)
    gates["ens"], g1 = gate_from_strategy(closeT, M, H_ENS, COST)
    gdf = pd.DataFrame({"gate_s2": gates["s2"], "gate_ens": gates["ens"], "gate_300": gates["300"]}, index=cal)
    gdf.to_csv(RUN / "gate_series.csv")
    log(f"gate NAV start s2={g0} ens={g1}; off share since 2013: " +
        str(gdf.loc["2013":].mean().round(3).to_dict()))

    # A on CSI300TR (参考线)
    F3 = L.slim_factors(pd.DataFrame({"date": h3.index, "close": h3.values, "amount": np.nan}))
    p3 = L.policy_baseline(F3)[0]
    net3, ps3 = run_pos(p3, F3["ret"], cost=COST)
    a300 = dict(ret=net3.reindex(cal).fillna(0.0), turn=ps3.diff().abs().reindex(cal).fillna(0.0),
                expo=ps3.reindex(cal).fillna(0.0), lo=0)
    r300 = h3.reindex(cal).pct_change().fillna(0.0)

    # 分散臂：总收益 vs 旧口径（ETF 腿 qfq pct_change）
    Rd, first_etf = PB.div_returns(P, cal)
    Pn = dict(P)
    Pn["close_tr"] = P["close_naive"]
    Rd_n, _ = PB.div_returns(Pn, cal)
    Apos = PB.div_A(Rd)

    rows, step0, cost_rows, starts = [], [], [], {}
    w2013 = None
    for wname, sdate in START_W.items():
        out = {a: run_arm(closeT, M, sdate, a, gates) for a in ARMS_MAIN}
        lo = out["PIT-VT15"]["lo"]
        starts[wname] = {a: str(cal[d["lo"]].date()) for a, d in out.items()}
        out["PIT-EW"] = ew_run(RT, lists, cal, lo, COST)
        out["DIV-IV"] = div_run(Rd, Apos, cal, lo)
        out["A@CSI300TR"] = a300
        log(f"{wname} start {cal[lo].date()} starts={starts[wname]}")
        segs = [(wname, cal[lo], END)]
        if wname == "W2013":
            w2013 = out
            segs += [(k, pd.Timestamp(a), pd.Timestamp(b)) for k, (a, b) in SUB_W.items()]
        for seg, a, b in segs:
            mask = (cal >= a) & (cal <= b)
            ew = out["PIT-EW"]["ret"][mask]
            for arm, d in out.items():
                m = metr(d, mask)
                rows.append({"window": seg, "from": str(cal[mask][0].date()), "to": str(cal[mask][-1].date()), "arm": arm, **m})
                if arm in ARMS_MAIN:
                    k = float(d["expo"][mask].mean())
                    rows.append({"window": seg, "from": str(cal[mask][0].date()), "to": str(cal[mask][-1].date()),
                                 "arm": f"STATIC[{arm}]", "k": k, **PB.metr((k * ew).to_numpy())})
            m3 = mask & (cal <= h3.index.max())
            rows.append({"window": seg, "from": str(cal[m3][0].date()), "to": str(cal[m3][-1].date()), "arm": "CSI300TR",
                         **PB.metr(r300[m3].to_numpy())})
            # 门关占比
            for gk in ("s2", "ens", "300"):
                rows.append({"window": seg, "arm": f"GATE_OFF_SHARE[{gk}]", "avg_expo": float((~gates[gk][mask]).mean())})
        # ---- 第 0 步：旧口径 vs 总收益
        mask = (cal >= cal[lo]) & (cal <= END)
        for arm in ("PIT-S2", "PIT-VT15", "PIT-VT15-5d"):
            dn = run_arm(closeN, M, sdate, arm, gates)
            step0.append({"set": f"PIT_{wname}", "arm": arm, "basis": "qfq_naive", **metr(dn, mask)})
            step0.append({"set": f"PIT_{wname}", "arm": arm, "basis": "TR", **metr(out[arm], mask)})
        en = ew_run(RN, lists, cal, lo, COST)
        step0.append({"set": f"PIT_{wname}", "arm": "PIT-EW", "basis": "qfq_naive", **metr(en, mask)})
        step0.append({"set": f"PIT_{wname}", "arm": "PIT-EW", "basis": "TR", **metr(out["PIT-EW"], mask)})
        dn = div_run(Rd_n, Apos, cal, lo)
        step0.append({"set": f"DIV_{wname}", "arm": "DIV-IV", "basis": "qfq_naive(ETF腿)", **metr(dn, mask)})
        step0.append({"set": f"DIV_{wname}", "arm": "DIV-IV", "basis": "TR", **metr(out["DIV-IV"], mask)})
        # ---- 成本敏感性
        for bp in (5, 10, 15):
            c = bp / 1e4
            if bp == 5:
                oc = out
            else:
                gc = {"300": gates["300"]}
                gc["s2"], _ = gate_from_strategy(closeT, M, H_BASE, c)
                gc["ens"], _ = gate_from_strategy(closeT, M, H_ENS, c)
                oc = {a: run_arm(closeT, M, sdate, a, gc, cost=c) for a in ["PIT-VT15"] + JUDGED}
                oc["PIT-EW"] = ew_run(RT, lists, cal, lo, c)
            for arm in ["PIT-VT15"] + JUDGED + ["PIT-EW"]:
                cost_rows.append({"window": wname, "cost_bp": bp, "arm": arm, **metr(oc[arm], mask)})
        log(f"{wname} done")

    # ext 池（第 0 步）
    import pit_pool_repro as RP
    from pools import get_pool
    from macd_structure import ETF_POOL
    ext = list(get_pool("ext", ETF_POOL).keys())
    for kind, lab in (("close_naive", "qfq_naive"), ("close_tr", "TR")):
        C = P[kind]
        have = [c for c in ext if c in C.columns]
        c19 = C.loc["2019-01-02":END, have]
        c19 = c19.loc[:, c19.notna().sum() >= 300]
        tmp = []
        RP.run_set(c19, "ext", tmp, every_opts=("reb",))
        for r in tmp:
            step0.append({"set": f"ext{c19.shape[1]}_2019", "arm": r["arm"], "basis": lab,
                          **{k: r[k] for k in ("cagr", "vol", "sharpe", "mdd", "calmar")}, "from": r["start"]})

    res = pd.DataFrame(rows)
    res.to_csv(RUN / "results_windows.csv", index=False)
    pd.DataFrame(step0).to_csv(RUN / "step0_portfolio.csv", index=False)
    pd.DataFrame(cost_rows).to_csv(RUN / "cost_sensitivity.csv", index=False)
    if w2013 is not None:
        navs = pd.DataFrame({k: (1 + v["ret"][w2013["PIT-VT15"]["lo"]:]).cumprod() for k, v in w2013.items()})
        navs.to_csv(RUN / "nav_W2013.csv")

    # ---- 判定
    ver = {"starts": starts, "n_naive_in_pit_ever": n_naive_ever, "pit_ever": len(ever),
           "gate_nav_start": {"s2": g0, "ens": g1}, "arms": {}}
    g = res.set_index(["window", "arm"])
    for arm in JUDGED:
        c1 = {w: bool(g.loc[(w, arm), "calmar"] >= g.loc[(w, "PIT-VT15"), "calmar"]) for w in START_W}
        c2 = {w: bool(g.loc[(w, arm), "mdd"] >= g.loc[(w, "PIT-VT15"), "mdd"] - 0.03) for w in CRASH}
        c3 = {w: bool(g.loc[(w, arm), "calmar"] > g.loc[(w, f"STATIC[{arm}]"), "calmar"]) for w in START_W}
        ok1, ok2, ok3 = sum(c1.values()) >= 2, all(c2.values()), sum(c3.values()) >= 2
        ver["arms"][arm] = {"c1_calmar_ge_incumbent": c1, "c1_ok": ok1,
                            "c2_crash_mdd_within_3pp": c2, "c2_ok": ok2,
                            "c3_beats_static": c3, "c3_ok": ok3,
                            "adopt": bool(ok1 and ok2 and ok3)}
    (RUN / "verdict_portfolio.json").write_text(json.dumps(ver, ensure_ascii=False, indent=2))
    with pd.option_context("display.width", 250, "display.max_rows", 600):
        print(res[["window", "arm", "cagr", "mdd", "calmar", "avg_expo", "turn_two_way"]].round(3).to_string())
        print(pd.DataFrame(step0)[["set", "arm", "basis", "cagr", "mdd", "calmar"]].round(3).to_string())
        print(pd.DataFrame(cost_rows)[["window", "cost_bp", "arm", "cagr", "mdd", "calmar", "turn_two_way"]].round(3).to_string())
    print(json.dumps(ver, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
