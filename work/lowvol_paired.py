#!/usr/bin/env python3
"""L 臂（低波组用 A-G1）的低波日配对检验。

问题：ablation v3 用「跨市场全历史中位卡玛」一票否决 L，但那是对整段卡玛做比较，
而 L 只在"被判定为低波"的日子才换成 A-G1。这里按 L 自己的适用域重算：
  ① 只在低波日上分别合账 A / A-G1（低波日合账，按低波日数折算年化）
  ② 低波日逐日配对差分（年化差、t、胜率）
  ③ 拆出"闸动日"（G1<1，两人仓位不同）看砍仓到底砍对了没有
诊断重算，不是新假设，不占试验数。产出 runs/2026-11-14_lowvol_paired/
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

WORK = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK))
import ablation_v3_backtest as AV  # noqa: E402
import layered_v1_backtest as L  # noqa: E402
import long_history_backtest as LH  # noqa: E402
from single_asset_harness import run_pos, stats  # noqa: E402

ROOT = L.ROOT
RUN = ROOT / "runs" / "2026-11-14_ablation_v3"
OUT = ROOT / "runs" / "2026-11-14_lowvol_paired"
OUT.mkdir(parents=True, exist_ok=True)
TD = 252
MIN_LOW = 250


def log(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def block(netA, netG, pA, pG, ret, m):
    n = int(m.sum())
    sA, sG = stats(netA[m]), stats(netG[m])
    d = (netG[m] - netA[m]).fillna(0.0)
    mu, sd = float(d.mean()), float(d.std())
    t = mu / (sd / np.sqrt(n)) if sd > 0 else np.nan
    hit = float((d > 1e-12).mean())
    act = ((pG[m] - pA[m]) > 1e-9).to_numpy()
    rr = ret[m].fillna(0.0).to_numpy()
    return dict(
        n_low=n, cagr_A=sA["cagr"], cagr_G=sG["cagr"], mdd_A=sA["mdd"], mdd_G=sG["mdd"],
        calmar_A=sA["calmar"], calmar_G=sG["calmar"],
        d_calmar=(sG["calmar"] - sA["calmar"]) if (pd.notna(sA["calmar"]) and pd.notna(sG["calmar"])) else np.nan,
        ann_diff=mu * TD, t_stat=t, hit=hit,
        act_share=float(act.mean()) if n else np.nan,
        act_ret=float(rr[act].mean()) if act.any() else np.nan,
        avgpos_A=float(pA[m].mean()), avgpos_G=float(pG[m].mean()),
    )


def analyze(df, code, name, panel, ret_type, windows):
    df = df.reset_index(drop=True)
    if "date" in df.columns:
        df = df.set_index("date")
    F = L.slim_factors(df)
    pos, comps = AV.arms_positions(F)
    ret = F["ret"]
    netA, pA = run_pos(pos["A"], ret, cost=AV.COST)
    netG, pG = run_pos(pos["A-G1"], ret, cost=AV.COST)
    low = comps["lowvol"].astype(bool)
    ev = pd.Series(np.arange(len(df)) >= LH.WARMUP, index=ret.index)
    dates = ret.index
    base = dict(panel=panel, code=code, name=name, ret_type=ret_type)
    rows = []
    for w, a, b in windows:
        m = (low & ev).fillna(False)
        if a:
            m = m & ((dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b)))
        if m.sum() < MIN_LOW:
            continue
        rows.append(dict(base, window=w, **block(netA, netG, pA, pG, ret, m)))
    return rows


W_ALL = [("full", None, None), ("2019-2022", "2019-01-01", "2022-12-31"),
         ("2023-2026", "2023-01-01", "2026-11-13"), ("y2008", "2008-01-01", "2008-12-31"),
         ("y2015", "2015-01-01", "2015-12-31"), ("y2018", "2018-01-01", "2018-12-31")]
W_XM = W_ALL + [("2000-2002", "2000-01-01", "2002-12-31")]


def main():
    rows = []
    # ---- 跨市场 10 只 ----
    for panel, lst in (("xmkt", AV.XM_PRIMARY), ("xmkt_tr", AV.XM_TR)):
        for code, name, rt in lst:
            if not (AV.CM / f"{code}.csv").exists():
                continue
            df, _ = AV.load_xm(code)
            if len(df) < LH.WARMUP + LH.MIN_EVAL:
                continue
            rows += analyze(df, code, name, panel, rt, W_XM)
            log(f"xmkt {code}")
    # ---- A 股长 ETF 里过半时间低波的 59 只（L 的实际适用域）----
    lv = pd.read_csv(RUN / "lowvol_share_etf_long.csv")
    lv = lv[lv.lowshare >= 0.5]
    lh = pd.read_csv(ROOT / "runs" / "2026-11-14_long_history" / "results_long.csv")
    lh = lh[(lh.panel == "etf") & (lh.period == "full") & (lh.arm == "BH")].set_index("code")
    miss = []
    for code in lv.code:
        df, info = AV.tr_series_any(code)
        if df is None or len(df) < LH.WARMUP + LH.MIN_EVAL:
            miss.append(dict(code=code, reason=info if df is None else "too_short"))
            continue
        nm = lh.loc[code] if code in lh.index else None
        name = nm["name"] if nm is not None else code
        rows += analyze(df, code, name, "etf_long_low", "ETF总收益", W_ALL)
    log(f"etf_long_low 完成，缺失 {len(miss)}")
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "paired.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(miss).to_csv(OUT / "missing.csv", index=False, encoding="utf-8-sig")

    # ---- 汇总 ----
    def summ(g):
        return dict(n_names=len(g), med_calmar_A=g.calmar_A.median(), med_calmar_G=g.calmar_G.median(),
                    med_d_calmar=g.d_calmar.median(), pos_win=float((g.d_calmar > 0).mean()),
                    med_ann_diff=g.ann_diff.median(), med_t=g.t_stat.median(),
                    med_hit=g.hit.median(), med_act_share=g.act_share.median())
    lines = []
    for (panel, w), g in out.groupby(["panel", "window"]):
        s = summ(g)
        lines.append(dict(panel=panel, window=w, **s))
        print(f"{panel:12s} {w:10s} n={s['n_names']:3d} "
              f"卡玛A={s['med_calmar_A']:.3f} 卡玛G={s['med_calmar_G']:.3f} "
              f"Δ={s['med_d_calmar']:+.3f} 胜率={s['pos_win']:.0%} "
              f"年化差={s['med_ann_diff']*100:+.2f}pp t中位={s['med_t']:+.2f}", flush=True)
    pd.DataFrame(lines).to_csv(OUT / "summary.csv", index=False, encoding="utf-8-sig")
    log("完成")


if __name__ == "__main__":
    main()
