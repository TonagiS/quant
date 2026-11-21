#!/usr/bin/env python3
"""year_starts（2026-11-21）：11 个固定入场日空仓起步，九条已有臂跑到 2026-11-13。
规则见 runs/2026-11-21_year_starts/FREEZE.md。样本内。
用法：python3 work/year_starts.py check | run"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ideas_v1 as I  # noqa: E402
import slowdecline_engine as SE  # noqa: E402
import c3x_combo as X  # noqa: E402
import pv_pools as PV  # noqa: E402

K, RC, E = I.K, I.RC, I.E
ROOT = PV.ROOT
RUN = ROOT / "runs" / "2026-11-21_year_starts"
CACHE_FR = ROOT / "data" / "cache_full_rebuild"
CACHE_C3 = ROOT / "data" / "cache_combo3"
COSTS = (0.0005, 0.0020)
END = pd.Timestamp("2026-11-13")
# 冻结名单：2014-06-03 + 2015–2024 每年第一个交易日（见 FREEZE.md）
STARTS = [
    "2014-06-03", "2015-01-05", "2016-01-04", "2017-01-03",
    "2018-01-02", "2019-01-02", "2020-01-02", "2021-01-04",
    "2022-01-04", "2023-01-03", "2024-01-02",
]
ARMS = [
    "S2+VT15", "S4", "S4+CREDIT", "CHAMP", "C3",
    "C3 60% + CHAMP", "C3 60% + S4", "C3 60% + S4+CREDIT+STOP12", "DIV-IV",
]
G = {}


def setup():
    if G:
        return
    I.setup()
    X.setup_parent()
    D = RC.data()
    G["cal"] = D["cal"]
    G["close"] = D["closeT"]
    ever = sorted({c for v in X.G["lists"]["P12_HYST60"].values() for c in v})
    G["p12_close"] = PV.G["C"][ever]
    G["p12_M"] = X.G["M"]["P12_HYST60"]
    G["credit"] = I.G["credit"]
    G["rS4"], G["rCH"], G["rC3"] = {}, {}, {}
    for bp, cost in ((5, 0.0005), (20, 0.0020)):
        G["rS4"][bp] = np.load(CACHE_FR / f"ret_{bp}_NONE__BASE__NONE__S2__CAP2__VT15__NONE.npy")
        G["rCH"][bp] = np.load(CACHE_FR / f"ret_{bp}_NONE__P12_HYST60__NONE__DONCH__CAP2__VT10__NONE.npy")
        G["rC3"][bp] = np.load(CACHE_C3 / f"ref_{bp}.npz")["core"]
    G["s0"] = str(pd.Timestamp(PV.SDATE).date())
    # 截止日掩码：日历末日已是 2026-11-13；若更长则截到该日
    G["hi"] = int(np.searchsorted(G["cal"], END, side="right"))


def pack(rr, lo):
    return dict(ret=np.asarray(rr["ret"], float), turn=np.asarray(rr["turn"], float),
                expo=np.asarray(rr["expo"], float), lo=int(lo))


def sat_book(sdate, cost, sector=True, vt=15, credit=False, stop=None):
    f = K.sat_feats(sdate, sector=sector)
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    sim = E.simulate
    if stop is not None:
        cfg = replace(cfg, stop=stop, stop_mode="trail")
        sim = SE.simulate
    close = G["close"]
    r0 = sim(close, f, cfg)
    if vt:
        f2 = dict(f)
        f2["base_ret"] = r0["ret"]
        rr = sim(close, f2, replace(cfg, rule=E.VolTarget(vt / 100.0, 60, 40, every=10)))
    else:
        rr = r0
    d = pack(rr, f["start"])
    if credit:
        d = K.apply_mult(d, np.asarray(G["credit"], float), cost)
        d["lo"] = f["start"]
    return d


def champ_book(sdate, cost):
    close, M = G["p12_close"], G["p12_M"]
    f = dict(X.feats(close, M, "DONCH", "CAP2"))
    lo = max(int(f["start"]), int(close.index.searchsorted(pd.Timestamp(sdate))))
    f["start"] = lo
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    r0 = E.simulate(close, f, cfg)
    f2 = dict(f)
    f2["base_ret"] = r0["ret"]
    rr = E.simulate(close, f2, replace(cfg, rule=E.VolTarget(0.10, 60, 40, every=10)))
    return pack(rr, lo)


def books_at(sdate, cost):
    s2 = sat_book(sdate, cost, sector=False, vt=15)
    s4 = sat_book(sdate, cost, sector=True, vt=15)
    s4c = sat_book(sdate, cost, sector=True, vt=15, credit=True)
    s4cs = sat_book(sdate, cost, sector=True, vt=15, credit=True, stop=0.12)
    ch = champ_book(sdate, cost)
    lo = int(s4["lo"])  # S2/S4 同一套 feats 起点
    c3 = I.core("C3", lo, cost)
    div = I.core("DIV-IV", lo, cost)
    # 混合：卫星若更晚（理论上冠军与 S4 同日或更晚）则从其 lo 起，避免零收益垫前
    def mx(sat, name):
        lo_m = max(lo, int(sat["lo"]))
        return RC.mix(c3, sat, 0.60, lo_m, cost)

    return {
        "S2+VT15": s2,
        "S4": s4,
        "S4+CREDIT": s4c,
        "CHAMP": ch,
        "C3": c3,
        "C3 60% + CHAMP": mx(ch, "CHAMP"),
        "C3 60% + S4": mx(s4, "S4"),
        "C3 60% + S4+CREDIT+STOP12": mx(s4cs, "S4CS"),
        "DIV-IV": div,
    }


def row_of(sdate, arm, cost, d):
    cal = G["cal"]
    # 统计到截止日（含）
    hi = np.zeros(len(cal), bool)
    hi[: G["hi"]] = True
    m = RC.metr(d["ret"], d["lo"], hi, d.get("turn"), d.get("expo"))
    return {
        "start_req": sdate,
        "start": str(cal[d["lo"]].date()) if 0 <= d["lo"] < len(cal) else "",
        "lo": int(d["lo"]),
        "n_days": int((np.arange(len(cal)) >= d["lo"])[: G["hi"]].sum()),
        "arm": arm,
        "cost_bp": int(round(cost * 1e4)),
        "cagr": m["cagr"],
        "mdd": m["mdd"],
        "calmar": m["calmar"],
        "turn_two_way": m.get("turn_two_way", np.nan),
        "avg_expo": m.get("avg_expo", np.nan),
    }


def maxabs(a, b):
    n = min(len(a), len(b))
    return float(np.max(np.abs(np.asarray(a, float)[:n] - np.asarray(b, float)[:n])))


def cmd_check():
    setup()
    s0, cal = G["s0"], G["cal"]
    print("SDATE", s0, "cal", str(cal[0].date()), "->", str(cal[-1].date()), "hi", G["hi"], flush=True)
    f0 = K.sat_feats(s0)
    print("standard S4 start", f0["start"], str(cal[f0["start"]].date()), flush=True)
    ok = True
    for bp, cost in ((5, 0.0005), (20, 0.0020)):
        d_s4 = sat_book(s0, cost, sector=True, vt=15)
        d_ch = champ_book(s0, cost)
        d_c3 = I.core("C3", d_s4["lo"], cost)
        e_s4 = maxabs(d_s4["ret"], G["rS4"][bp])
        e_ch = maxabs(d_ch["ret"], G["rCH"][bp])
        e_c3 = maxabs(d_c3["ret"], G["rC3"][bp])
        # mix vs 缓存两腿
        core_c = dict(ret=np.asarray(G["rC3"][bp], float), expo=np.ones(len(cal)), lo=d_s4["lo"])
        sat_c = dict(ret=np.asarray(G["rCH"][bp], float), expo=d_ch["expo"], lo=d_ch["lo"])
        mix_c = RC.mix(core_c, sat_c, 0.60, max(d_s4["lo"], d_ch["lo"]), cost)
        mix_m = RC.mix(d_c3, d_ch, 0.60, max(d_s4["lo"], d_ch["lo"]), cost)
        e_mx = maxabs(mix_m["ret"], mix_c["ret"])
        print(f"  {bp}bp S4={e_s4:.3e} champ={e_ch:.3e} C3={e_c3:.3e} mix={e_mx:.3e} "
              f"loS4={d_s4['lo']} loCH={d_ch['lo']}", flush=True)
        if max(e_s4, e_ch, e_c3, e_mx) >= 1e-6:
            ok = False
        # 若 2014-06-03 就是标准起点，该书应与缓存同一条
        if str(cal[d_s4["lo"]].date()) == "2014-06-03":
            d14 = sat_book("2014-06-03", cost, sector=True, vt=15)
            e14 = maxabs(d14["ret"], G["rS4"][bp])
            print(f"  {bp}bp 2014-06-03 S4 vs cache {e14:.3e} lo={d14['lo']}", flush=True)
            if e14 >= 1e-6:
                ok = False
    print("CHECK", "PASS" if ok else "FAIL")
    if not ok:
        sys.exit(2)


def cmd_run():
    setup()
    RUN.mkdir(parents=True, exist_ok=True)
    rows = []
    for sdate in STARTS:
        print("window", sdate, flush=True)
        for cost in COSTS:
            bk = books_at(sdate, cost)
            for arm in ARMS:
                rows.append(row_of(sdate, arm, cost, bk[arm]))
    df = pd.DataFrame(rows)
    df.to_csv(RUN / "results.csv", index=False)
    # 11 起点中位与胜出比例
    summ = []
    for bp in (5, 20):
        x = df[df.cost_bp == bp]
        for arm in ARMS:
            g = x[x.arm == arm]
            summ.append(dict(
                cost_bp=bp, arm=arm, n=int(len(g)),
                med_cagr=float(g.cagr.median()), med_mdd=float(g.mdd.median()),
                med_calmar=float(g.calmar.median()),
                p25_calmar=float(g.calmar.quantile(0.25)),
                p75_calmar=float(g.calmar.quantile(0.75)),
            ))
    sdf = pd.DataFrame(summ)
    sdf.to_csv(RUN / "summary_median.csv", index=False)
    # 两两胜出：卡玛、年化
    wins = []
    pairs = [("CHAMP", "S4"), ("CHAMP", "S2+VT15"), ("CHAMP", "C3"),
             ("S4", "S2+VT15"), ("S4+CREDIT", "S4"),
             ("C3 60% + CHAMP", "C3 60% + S4"),
             ("C3 60% + CHAMP", "C3"),
             ("C3 60% + S4", "C3"),
             ("C3 60% + S4+CREDIT+STOP12", "C3 60% + S4"),
             ("C3", "DIV-IV"),
             ("S4", "C3"), ("CHAMP", "DIV-IV")]
    for bp in (5, 20):
        x = df[df.cost_bp == bp]
        for a, b in pairs:
            A, B = x[x.arm == a].set_index("start_req"), x[x.arm == b].set_index("start_req")
            idx = A.index.intersection(B.index)
            dc = A.loc[idx, "calmar"] - B.loc[idx, "calmar"]
            dy = A.loc[idx, "cagr"] - B.loc[idx, "cagr"]
            wins.append(dict(
                cost_bp=bp, a=a, b=b, n=int(len(idx)),
                win_calmar=float((dc > 0).mean()),
                win_cagr=float((dy > 0).mean()),
                med_dcalmar=float(dc.median()),
                med_dcagr=float(dy.median()),
            ))
    wdf = pd.DataFrame(wins)
    wdf.to_csv(RUN / "win_rates.csv", index=False)
    print(sdf.to_string(index=False), flush=True)
    print(wdf.to_string(index=False), flush=True)
    verdict = dict(
        n_rows=int(len(df)), n_starts=len(STARTS), n_arms=len(ARMS),
        starts=STARTS, end=str(END.date()),
        credit_info=dict(I.G.get("credit_info", {})),
    )
    (RUN / "verdict.json").write_text(json.dumps(verdict, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("run done rows", len(df), flush=True)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "check":
        cmd_check()
    elif cmd == "run":
        cmd_run()
    else:
        raise SystemExit(f"unknown {cmd}")


if __name__ == "__main__":
    main()
