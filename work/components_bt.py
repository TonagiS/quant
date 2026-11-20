#!/usr/bin/env python3
"""components 回测主脚本。规则见 runs/2026-11-20_components/FREEZE.md（已冻结）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import components_core as K  # noqa: E402

RC, PB, TP = K.RC, K.PB, K.TP
COSTS = (5, 10, 15, 20)
SAT_OPTS = {"S2+VT15": {}, "S1": {"quality": True}, "S2r": {"skip": 5}, "S3": {"buffer": True}, "S4": {"sector": True}}
ALL_SAT = {"quality": True, "skip": 5, "buffer": True, "sector": True}


def dct(d):
    return dict(ret=np.asarray(d["ret"], float), turn=np.asarray(d["turn"], float), expo=np.asarray(d["expo"], float), lo=d["lo"])


def build_window(sdate, cost, adopted=None):
    D = RC.data()
    cal = D["cal"]
    A = {}
    # 卫星
    for nm, o in SAT_OPTS.items():
        A[nm] = K.sat_run(sdate, cost, keep=(nm == "S2+VT15"), **o)
    inc = A["S2+VT15"]
    lo = inc["lo"]
    # 核心
    m, _ = K.erp_mult()
    A["DIV-IV"] = K.core_run(lo, cost)
    A["C1"] = K.core_run(lo, cost, assets=K.ALL13, eq=K.EQ13)
    A["C2"] = K.core_run(lo, cost, mode="ERC")
    A["C3"] = K.core_run(lo, cost, mult=m)
    # 静态对照（核心）
    A["STATIC[C1]"] = K.core_run(lo, cost, mode="EW", assets=K.ALL13, eq=K.EQ13)
    A["STATIC[C2]"] = K.core_run(lo, cost, mode="EW")
    kc3 = float(A["C3"]["mused"].mean())
    A["STATIC[C3]"] = K.core_run(lo, cost, k_static=kc3)
    # 在位组合
    A["MIX80/20"] = RC.mix(A["DIV-IV"], inc, 0.8, lo, cost)
    # 风控
    m1 = K.r1_signal(inc["ret"], lo)
    A["R1@S2+VT15"] = K.apply_mult(inc, m1, cost)
    m1x = K.r1_signal(A["MIX80/20"]["ret"], lo)
    A["R1@MIX"] = K.apply_mult(A["MIX80/20"], m1x, cost)
    m2, avgc = K.r2_signal(inc["w_hist"], lo)
    A["R2@S2+VT15"] = K.apply_mult(inc, m2, cost)
    A["R2@MIX"] = RC.mix(A["DIV-IV"], A["R2@S2+VT15"], 0.8, lo, cost)
    # 全加
    core_all = K.core_run(lo, cost, mode="ERC", assets=K.ALL13, eq=K.EQ13, mult=m)
    sat_all0 = K.sat_run(sdate, cost, keep=True, **ALL_SAT)
    m2a, _ = K.r2_signal(sat_all0["w_hist"], sat_all0["lo"])
    sat_all = K.apply_mult(sat_all0, m2a, cost)
    mix_all0 = RC.mix(core_all, sat_all, 0.8, lo, cost)
    A["ALLIN-core"] = core_all
    A["STATIC[ALLIN-core]"] = A["STATIC[C1]"]  # 分层参考：13 只等权
    A["ALLIN-sat"] = sat_all
    A["ALL-IN"] = K.apply_mult(mix_all0, K.r1_signal(mix_all0["ret"], lo), cost)
    # 只加通过的
    if adopted is not None:
        core_kw = {}
        if "C1" in adopted: core_kw.update(assets=K.ALL13, eq=K.EQ13)
        if "C2" in adopted: core_kw.update(mode="ERC")
        if "C3" in adopted: core_kw.update(mult=m)
        sat_kw = {}
        if "S1" in adopted: sat_kw["quality"] = True
        if "S2r" in adopted: sat_kw["skip"] = 5
        if "S3" in adopted: sat_kw["buffer"] = True
        if "S4" in adopted: sat_kw["sector"] = True
        r2 = ("R2@S2+VT15" in adopted) or ("R2@MIX" in adopted)
        r1 = ("R1@S2+VT15" in adopted) or ("R1@MIX" in adopted)
        cr = K.core_run(lo, cost, **core_kw)
        sr = K.sat_run(sdate, cost, keep=r2, **sat_kw)
        if r2:
            sr = K.apply_mult(sr, K.r2_signal(sr["w_hist"], sr["lo"])[0], cost)
        mx = RC.mix(cr, sr, 0.8, lo, cost)
        if r1:
            mx = K.apply_mult(mx, K.r1_signal(mx["ret"], lo), cost)
        A["ADOPTED-ONLY"] = mx
    # 卫星静态对照：k × PIT-EW
    ew = TP.ew_run(D["RT"], D["lists"], cal, lo, cost)
    A["PIT-EW"] = dict(ret=ew["ret"].to_numpy(), turn=ew["turn"].to_numpy(), expo=ew["expo"].to_numpy(), lo=lo)
    extra = {"r1_share_S2VT": float((m1[lo:] < 1).mean()), "r1_share_MIX": float((m1x[lo:] < 1).mean()),
             "r2_share_S2VT": float((m2[lo:] < 1).mean()), "r2_avgcorr_median": float(np.nanmedian(avgc[lo:])),
             "c3_k_mean": kc3, "lo": str(cal[lo].date())}
    return A, lo, extra


SAT_ARMS = ["S1", "S2r", "S3", "S4", "R1@S2+VT15", "R2@S2+VT15", "ALLIN-sat"]
CORE_ARMS = ["C1", "C2", "C3", "ALLIN-core"]
MIX_ARMS = ["R1@MIX", "R2@MIX", "ALL-IN", "ADOPTED-ONLY"]
INC = {**{a: "S2+VT15" for a in SAT_ARMS}, **{a: "DIV-IV" for a in CORE_ARMS}, **{a: "MIX80/20" for a in MIX_ARMS}}
JUDGED = ["C1", "C2", "C3", "S1", "S2r", "S3", "S4", "R1@S2+VT15", "R1@MIX", "R2@S2+VT15", "R2@MIX", "ALL-IN", "ADOPTED-ONLY"]


def collect(A, lo, wname, bp, rows):
    cal = RC.data()["cal"]
    segs = [(wname, cal[lo], RC.END)]
    if wname == "W2013":
        segs += [(s, pd.Timestamp(a), pd.Timestamp(b)) for s, (a, b) in RC.SUB_W.items()]
    idx = np.arange(len(cal))
    for seg, a, b in segs:
        msk = RC.seg_mask(a, b)
        for nm, d in A.items():
            mm = RC.metr(d["ret"], d["lo"], msk, d["turn"], d["expo"])
            rows.append({"cost_bp": bp, "window": seg, "arm": nm, **mm})
        # 静态对照
        for arm in SAT_ARMS + MIX_ARMS:
            if arm not in A:
                continue
            d = A[arm]
            mm_ = msk & (idx >= d["lo"])
            if INC[arm] == "S2+VT15":
                k = float(d["expo"][mm_].mean())
                st = k * A["PIT-EW"]["ret"]
            else:
                base = A["MIX80/20"]
                k = float(d["expo"][mm_].mean() / base["expo"][mm_].mean())
                st = k * np.asarray(base["ret"])
            rows.append({"cost_bp": bp, "window": seg, "arm": f"STATIC[{arm}]", "k": k, **RC.metr(st, lo, msk)})
        for arm in ("S2+VT15",):
            d = A[arm]; mm_ = msk & (idx >= d["lo"])
            k = float(d["expo"][mm_].mean())
            rows.append({"cost_bp": bp, "window": seg, "arm": f"STATIC[{arm}]", "k": k, **RC.metr(k * A["PIT-EW"]["ret"], lo, msk)})


def judge(res, arms):
    g = res[res.cost_bp == 5].set_index(["window", "arm"])
    ver = {}
    for arm in arms:
        inc = INC[arm]
        c1 = {w: bool(g.loc[(w, arm), "calmar"] >= g.loc[(w, inc), "calmar"]) for w in RC.START_W}
        c2 = {w: bool(g.loc[(w, arm), "mdd"] >= g.loc[(w, inc), "mdd"] - 0.03) for w in RC.CRASH}
        c3 = {w: bool(g.loc[(w, arm), "calmar"] > g.loc[(w, f"STATIC[{arm}]"), "calmar"]) for w in RC.START_W}
        ok = sum(c1.values()) >= 2 and all(c2.values()) and sum(c3.values()) >= 2
        ver[arm] = {"incumbent": inc, "c1": c1, "c2": c2, "c3": c3,
                    "c1_ok": sum(c1.values()) >= 2, "c2_ok": all(c2.values()), "c3_ok": sum(c3.values()) >= 2, "adopt": bool(ok)}
    return ver


def main():
    D = RC.data()
    cal = D["cal"]
    rows, extras, navs = [], {}, {}
    # 第一轮：不含 ADOPTED-ONLY
    for bp in COSTS:
        for w, sdate in RC.START_W.items():
            A, lo, ex = build_window(sdate, bp / 1e4)
            collect(A, lo, w, bp, rows)
            if bp == 5:
                extras[w] = ex
                if w == "W2013":
                    navs = {nm: np.cumprod(1 + np.asarray(d["ret"])[lo:]) for nm, d in A.items()}
                    navs = pd.DataFrame(navs, index=cal[lo:])
                    _S13 = A
            print(bp, w, flush=True)
    res = pd.DataFrame(rows)
    ver = judge(res, [a for a in JUDGED if a != "ADOPTED-ONLY"] + ["ALLIN-core", "ALLIN-sat"])
    adopted = [a for a in JUDGED if a in ver and ver[a]["adopt"] and a not in ("ALL-IN",)]
    print("adopted:", adopted, flush=True)
    if adopted:
        rows2 = []
        for bp in COSTS:
            for w, sdate in RC.START_W.items():
                A, lo, _ = build_window(sdate, bp / 1e4, adopted=adopted)
                A = {k: v for k, v in A.items() if k in ("ADOPTED-ONLY", "MIX80/20", "PIT-EW", "S2+VT15")}
                collect(A, lo, w, bp, rows2)
        r2 = pd.DataFrame(rows2)
        r2 = r2[r2.arm.isin(["ADOPTED-ONLY", "STATIC[ADOPTED-ONLY]"])]
        res = pd.concat([res, r2], ignore_index=True)
        ver.update(judge(res, ["ADOPTED-ONLY"]))
    else:
        ver["ADOPTED-ONLY"] = {"note": "无通过者，ADOPTED-ONLY = MIX80/20，不另跑", "adopt": False}
    res.to_csv(K.RUN / "results.csv", index=False)
    navs.to_csv(K.RUN / "nav_W2013.csv")
    out = {"verdicts": ver, "adopted_components": adopted, "extras": extras, "first_etf_dates": K._S.get("first")}
    (K.RUN / "verdict.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    # 保存 W2013 5bp 日收益供 DSR
    np.savez_compressed(K.CC / "w2013_rets.npz", **{nm.replace("/", "_"): np.asarray(d["ret"]) for nm, d in _S13.items()},
                        lo=_S13["S2+VT15"]["lo"])
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
