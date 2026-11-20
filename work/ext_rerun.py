#!/usr/bin/env python3
"""ext_rerun：slowdecline 的各臂在原 ext 固定池（事后挑选）上重跑，并与 PIT 池同臂对照。规则见 runs/2026-11-21_ext_rerun/FREEZE.md。"""
from __future__ import annotations

import sys
import types
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "work")); sys.path.insert(0, str(ROOT / "indicators" / "code"))
import slowdecline as S  # noqa: E402

S.setup()
RC, I, K, E, G, X, SE = S.RC, S.I, S.K, S.E, S.G, S.X, S.SE
sys.modules.setdefault("akshare", types.ModuleType("akshare"))
from pools import get_pool  # noqa: E402
from macd_structure import ETF_POOL  # noqa: E402
from fullmkt_single_asset import sector_tag  # noqa: E402
import tr_ens_data as TD  # noqa: E402

RUN = ROOT / "runs" / "2026-11-21_ext_rerun"
D = RC.data()
cal = D["cal"]
P = TD.build()
EXT = list(get_pool("ext", ETF_POOL).keys())
uni = pd.read_csv(ROOT / "data/etf_universe.csv").set_index("code")["name"].to_dict()


def feats(close, member, sdate):
    """与 components_core.sat_feats(sector=False) 同公式，close/member 可换。"""
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
    enough = (valid.sum(axis=1) >= 8) & rm.notna().any(axis=1) & (close.index >= pd.Timestamp(sdate))
    start = int(np.argmax(enough.to_numpy()))
    return {"ret": ret, "vol20": vol20, "mom": mom, "ma60": ma60, "valid": valid, "rm": rm, "cand": cand,
            "start": start, "bench": (1.0 + ret.mean(axis=1).fillna(0.0)).cumprod()}


def cap2(score, allowed, secv):
    cv = allowed.copy()
    for i in range(len(cv)):
        idx = np.flatnonzero(cv[i] & ~np.isnan(score[i]))
        if len(idx) <= 2:
            continue
        cnt = {}
        for j in idx[np.argsort(-score[i, idx])]:
            cnt[secv[j]] = cnt.get(secv[j], 0) + 1
            if cnt[secv[j]] > 2:
                cv[i, j] = False
    return cv


SPEC = {  # 名称: (sector, vt, credit, arms)
    "S2": (False, False, False, ()), "S2+VT15": (False, True, False, ()), "S4": (True, True, False, ()),
    "S2+VT15+CREDIT": (False, True, True, ()), "S4+CREDIT": (True, True, True, ()),
    "S4+CREDIT+STOP12": (True, True, True, ("TS12",)), "S4+CREDIT+CNY": (True, True, True, ("CNY_1P",)),
    "S4+CREDIT+OFF03+STOP12": (True, True, True, ("OFF03", "TS12")),
}


def run_sat(close, f, secv, name, cost):
    sector, vt, credit, arms = SPEC[name]
    spec = S.merge_arms(list(arms))
    f = dict(f)
    if sector:
        cv = cap2(f["rm"].where(f["valid"]).to_numpy(), f["cand"].to_numpy(), secv)
        f["cand"] = pd.DataFrame(cv, index=close.index, columns=close.columns)
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    if spec["stop"] is not None:
        cfg = replace(cfg, stop=spec["stop"], stop_mode="trail")
    sim = SE.simulate if spec["stop"] is not None else E.simulate
    r0 = sim(close, f, cfg)
    if vt:
        f2 = dict(f); f2["base_ret"] = r0["ret"]
        rr = sim(close, f2, replace(cfg, rule=E.VolTarget(0.15, 60, 40, every=10)))
    else:
        rr = r0
    d = dict(ret=np.asarray(rr["ret"]), turn=np.asarray(rr["turn"]), expo=np.asarray(rr["expo"]), lo=f["start"])
    m = np.ones(len(d["ret"]))
    if credit:
        m = m * G["credit"]
    if spec["gate"]:
        m = m * S.gate_mult(spec["gate"], len(m))
    if credit or spec["gate"]:
        d = K.apply_mult(d, m, cost)
    return d


def universe(kind, sdate):
    if kind.startswith("EXT"):
        trunc = {"EXT19": "2019-01-02", "EXT17": "2017-01-01"}[kind]
        have = [c for c in EXT if c in P["close_tr"].columns]
        C = P["close_tr"].reindex(cal)[have]
        C = C.where(pd.Series(cal >= pd.Timestamp(trunc), index=cal), axis=0)
        C = C.loc[:, C.notna().sum() >= 300]
        member = pd.DataFrame(True, index=cal, columns=C.columns)
        secv = np.array([sector_tag(P["cat"].get(c, ""), uni.get(c, ""), "") for c in C.columns])
    else:
        C, member = D["closeT"], D["M"]
        sec = K.sector_map()
        secv = np.array([sec[c] for c in C.columns])
    return C, feats(C, member, sdate), secv


SEG = {"2023+": ("2023-01-01", "2026-11-13"), "21-02→24-02": ("2021-02-18", "2024-02-02")}
MIXES = [(0.5, "S4+CREDIT+STOP12"), (0.6, "S4+CREDIT+STOP12"), (0.8, "S4+CREDIT+STOP12"),
         (0.5, "S2+VT15"), (0.6, "S2+VT15"), (0.8, "S2+VT15")]


def main():
    rows = []
    _, fe19, _ = universe("EXT19", "2019-01-02")
    ext_lo_date = str(cal[fe19["start"]].date())
    _, fe17, _ = universe("EXT17", "2017-01-01")
    ext17_lo_date = str(cal[fe17["start"]].date())
    WINS = [("EXT19", "2019-01-02"), ("EXT17", "2017-01-01"), ("PIT", "2019-01-02"), ("PIT", ext_lo_date),
            ("PIT", "2017-01-03"), ("PIT", ext17_lo_date)]
    # 自检：PIT 通用 feats 与 components_core 一致
    Cp, fp, sp = universe("PIT", "2019-01-02")
    chk = run_sat(Cp, fp, sp, "S4", 0.0005)["ret"] - K.sat_run("2019-01-02", 0.0005, sector=True)["ret"]
    print("selfcheck PIT S4 W2019 maxdiff", float(np.abs(chk).max()), "| ext n19", fe19["valid"].shape[1], ext_lo_date,
          "| ext17 start", ext17_lo_date, flush=True)
    for kind, sd in WINS:
        C, f, secv = universe(kind, sd)
        lab = f"{kind}@{str(cal[f['start']].date())}"
        ncol = int(C.notna().any().sum())
        for cost in (0.0005, 0.002):
            sats = {nm: run_sat(C, f, secv, nm, cost) for nm in SPEC}
            lo = f["start"]
            core = I.core("C3", lo, cost)
            arms = dict(sats)
            arms["C3"] = core
            for wc, sn in MIXES:
                arms[f"C3 {int(wc*100)}% + {sn}"] = RC.mix(core, sats[sn], wc, lo, cost)
            for nm, d in arms.items():
                m = RC.metr(d["ret"], d["lo"], None, d["turn"], d["expo"])
                row = {"pool": kind, "window": lab, "n_names": ncol, "arm": nm, "cost_bp": int(round(cost * 1e4)),
                       "cagr": m["cagr"], "mdd": m["mdd"], "calmar": m["calmar"], "turn_two_way": m["turn_two_way"]}
                for s, (a, b) in SEG.items():
                    ms = RC.metr(d["ret"], d["lo"], np.asarray(RC.seg_mask(a, b)))
                    row[f"{s}_cagr"], row[f"{s}_mdd"] = ms["cagr"], ms["mdd"]
                rows.append(row)
        print("done", lab, ncol, flush=True)
    t = pd.DataFrame(rows)
    t.to_csv(RUN / "results.csv", index=False)
    return t


if __name__ == "__main__":
    t = main()
    pd.set_option("display.width", 250)
    for c in (5, 20):
        x = t[t.cost_bp == c]
        print(c)
        print(x.pivot_table(index="arm", columns="window", values="calmar", sort=False).round(2).to_string())
