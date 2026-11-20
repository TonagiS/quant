#!/usr/bin/env python3
"""第 1 部分（S2 减法消融）+ 第 2 部分（底仓+卫星固定比例）。规则见 FREEZE.md。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recombine_core as C  # noqa: E402

P1 = {"INCUMBENT(S2+VT15)": ("M3", "MA60", 5, "IV", 10, "VT15"),
      "ABL-noMA60": ("M3", "none", 5, "IV", 10, "VT15"),
      "ABL-EW": ("M3", "MA60", 5, "EW", 10, "VT15"),
      "ABL-M60": ("M60", "MA60", 5, "IV", 10, "VT15"),
      "REF-PIT-S2": ("M3", "MA60", 5, "IV", 10, "none")}
JUDGE1 = ["ABL-noMA60", "ABL-EW", "ABL-M60"]
MIXES = {"MIX80/20": 0.8, "MIX70/30": 0.7, "MIX60/40": 0.6}
COSTS = (5, 10, 15, 20)


def main():
    D = C.data()
    cal = D["cal"]
    rows, corr_rows = [], []
    navs = {}
    for bp in COSTS:
        cost = bp / 1e4
        for w, sdate in C.START_W.items():
            cache = {}
            arms = {}
            for nm, k in P1.items():
                bk, o = k[:5], k[5]
                if bk not in cache:
                    cache[bk] = C.run_base(bk, sdate, cost, overlays=("none", "VT15"))
                arms[nm] = cache[bk][o]
            inc = arms["INCUMBENT(S2+VT15)"]
            lo = inc["lo"]
            core = C.div_ret(lo, cost)
            arms["DIV-IV"] = core
            for nm, wc in MIXES.items():
                arms[nm] = C.mix(core, inc, wc, lo, cost)
            ew = C.TP.ew_run(D["RT"], D["lists"], cal, lo, cost)
            arms["PIT-EW"] = dict(ret=ew["ret"].to_numpy(), turn=ew["turn"].to_numpy(), expo=ew["expo"].to_numpy(), lo=lo)
            segs = [(w, cal[lo], C.END)]
            if w == "W2013":
                segs += [(s, pd.Timestamp(a), pd.Timestamp(b)) for s, (a, b) in C.SUB_W.items()]
                if bp == 5:
                    navs = {nm: pd.Series(np.cumprod(1 + np.asarray(d["ret"])[lo:]), index=cal[lo:]) for nm, d in arms.items()}
            for seg, a, b in segs:
                m = C.seg_mask(a, b)
                for nm, d in arms.items():
                    mm = C.metr(d["ret"], d["lo"], m, d["turn"], d["expo"])
                    rows.append({"cost_bp": bp, "window": seg, "arm": nm, "lo": str(cal[d["lo"]].date()), **mm})
                    if nm in P1 and nm != "REF-PIT-S2" or nm in MIXES:
                        k = float(np.asarray(d["expo"])[m & (np.arange(len(cal)) >= d["lo"])].mean())
                        st = k * np.asarray(arms["PIT-EW"]["ret"])
                        rows.append({"cost_bp": bp, "window": seg, "arm": f"STATIC[{nm}]", "k": k,
                                     **C.metr(st, lo, m)})
            if bp == 5:
                mw = np.arange(len(cal)) >= lo
                rc, rs = np.asarray(core["ret"])[mw], np.asarray(inc["ret"])[mw]
                me = pd.DataFrame({"c": rc, "s": rs}, index=cal[mw])
                mon = (1 + me).resample("ME").prod() - 1
                corr_rows.append({"window": w, "corr_daily": float(np.corrcoef(rc, rs)[0, 1]),
                                  "corr_monthly": float(mon.corr().iloc[0, 1])})
                # 2021-24 段内相关
                if w == "W2013":
                    m2 = C.seg_mask("2021-02-18", "2024-02-02")
                    corr_rows.append({"window": "S2021_24", "corr_daily": float(np.corrcoef(np.asarray(core["ret"])[m2], np.asarray(inc["ret"])[m2])[0, 1])})
            print(bp, w, "done", flush=True)
    res = pd.DataFrame(rows)
    res.to_csv(C.RUN / "part12_results.csv", index=False)
    pd.DataFrame(corr_rows).to_csv(C.RUN / "part2_corr.csv", index=False)
    pd.DataFrame(navs).to_csv(C.RUN / "part12_nav_W2013.csv")

    g = res[res.cost_bp == 5].set_index(["window", "arm"])
    ver = {"part1": {}, "part2": {}}
    inc = "INCUMBENT(S2+VT15)"
    for arm in JUDGE1:
        c1 = {w: bool(g.loc[(w, arm), "calmar"] >= g.loc[(w, inc), "calmar"]) for w in C.START_W}
        tie = all(g.loc[(w, arm), "calmar"] >= g.loc[(w, inc), "calmar"] - 0.02 for w in C.START_W)
        c2 = {w: bool(g.loc[(w, arm), "mdd"] >= g.loc[(w, inc), "mdd"] - 0.03) for w in C.CRASH}
        c3 = {w: bool(g.loc[(w, arm), "calmar"] > g.loc[(w, f"STATIC[{arm}]"), "calmar"]) for w in C.START_W}
        ok1, ok2, ok3 = sum(c1.values()) >= 2, all(c2.values()), sum(c3.values()) >= 2
        verdict = "可替代" if ok1 and ok2 and ok3 else ("打平，倾向更简单" if tie and ok2 and ok3 else "不采用")
        ver["part1"][arm] = {"c1": c1, "c1_ok": ok1, "tie_within_0.02_all3": bool(tie), "c2": c2, "c2_ok": ok2,
                             "c3": c3, "c3_ok": ok3, "verdict": verdict,
                             "calmar": {w: round(float(g.loc[(w, arm), "calmar"]), 4) for w in C.START_W},
                             "calmar_inc": {w: round(float(g.loc[(w, inc), "calmar"]), 4) for w in C.START_W}}
    for arm in MIXES:
        a1 = {w: bool(g.loc[(w, arm), "calmar"] >= g.loc[(w, inc), "calmar"]) for w in C.START_W}
        a2 = {w: bool(g.loc[(w, arm), "mdd"] >= g.loc[(w, inc), "mdd"] - 0.03) for w in C.CRASH}
        b1 = {w: bool(g.loc[(w, arm), "calmar"] >= g.loc[(w, "DIV-IV"), "calmar"]) for w in C.START_W}
        ver["part2"][arm] = {"a_vs_sat_calmar": a1, "a_crash": a2,
                             "a_ok": bool(sum(a1.values()) >= 2 and all(a2.values())),
                             "b_vs_div_calmar": b1, "b_ok": bool(sum(b1.values()) >= 2)}
    (C.RUN / "verdict_part12.json").write_text(json.dumps(ver, ensure_ascii=False, indent=2))
    with pd.option_context("display.width", 250, "display.max_rows", 900):
        print(res[res.cost_bp == 5][["window", "arm", "cagr", "vol", "mdd", "calmar", "avg_expo", "turn_two_way"]].round(3).to_string())
        print(res[(res.cost_bp > 5) & res.window.isin(list(C.START_W)) & ~res.arm.str.startswith("STATIC")][["cost_bp", "window", "arm", "cagr", "mdd", "calmar"]].round(3).to_string())
    print(pd.DataFrame(corr_rows))
    print(json.dumps(ver, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
