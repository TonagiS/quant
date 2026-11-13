"""状态调制版单支策略：不再"按状态换规则"，而是"按状态调仓位系数"。

底仓规则 base（两个版本）
  G1 = U14 牛慢熊快：年线上行时 mom20 破位只减半，否则清仓
  G2 = mom20 半仓（更温和）
状态系数（乘在底仓上，全部固定阈值，不拟合）
  volcap(k, floor) = clip(k/vol60, floor, 1)
  ddmult(a, b)     = dd>-10% ? 1 : (dd>=-20% ? a : b)
  trendmult        = T0/T1 1.0，T2 0.7，T3 0.4，T4 0
输出 train/test 中位 d_calmar、d_fixed、平均仓位，选 train 最优的那条，test 只报。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import (SPLIT, TRAIN_END, build_rules, factors,  # noqa: E402
                                  load_universe, run_pos, stats)

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("SA_OUT", str(ROOT / "runs" / "2026-11-15_single_asset_state")))


def variants(F, R):
    c, dd, vol = F["close"], F["dd"], F["vol60"]
    bull = ((c > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)
    mom_full, mom_half = R["B_mom20清仓"](F), R["B_mom20半仓"](F)
    g1 = mom_full.where(~bull, mom_half)
    g2 = mom_half
    slope = F["ma200"].pct_change(60)
    above = c > F["ma200"]
    T = np.select([above & (slope > 0.02), above & (slope >= -0.02), above,
                   (~above) & (slope >= -0.02)], [0, 1, 2, 3], default=4)
    T = pd.Series(T, index=c.index).where(F["ma200"].notna(), 1)

    def volcap(k, floor):
        return (k / vol).clip(floor, 1.0).fillna(1.0)

    def ddmult(a, b):
        return pd.Series(np.where(dd > -0.10, 1.0, np.where(dd >= -0.20, a, b)), index=c.index)

    tm = T.map({0: 1.0, 1: 1.0, 2: 0.7, 3: 0.4, 4: 0.0}).astype(float)
    vol20 = ret20 = F["ret"].rolling(20).std() * np.sqrt(252)
    base = g1 * volcap(0.15, 0.4)
    out = {
        "G1 U14基准": g1,
        "G1×volcap.20/.5": g1 * volcap(0.20, 0.5),
        "G1×volcap.15/.4": g1 * volcap(0.15, 0.4),
        "G1×volcap.25/.6": g1 * volcap(0.25, 0.6),
        "G1×dd.7/.4": g1 * ddmult(0.7, 0.4),
        "G1×dd.5/.0": g1 * ddmult(0.5, 0.0),
        "G1×trend(T2.7,T3.4)": g1 * tm,
        "G1×volcap.20×dd.7": g1 * volcap(0.20, 0.5) * ddmult(0.7, 0.4),
        "G2 mom20半仓": g2,
        "G2×volcap.20/.5": g2 * volcap(0.20, 0.5),
        # —— 第二轮：在 volcap.15/.4 上加别的状态层 ——
        "R2 volcap.18/.45": g1 * volcap(0.18, 0.45),
        "R2 volcap.12/.35": g1 * volcap(0.12, 0.35),
        "R2 +trend(1,1,.85,.6)": base * T.map({0: 1.0, 1: 1.0, 2: 0.85, 3: 0.6, 4: 0.5}).astype(float),
        "R2 +ddm(.85,.65)": base * ddmult(0.85, 0.65),
        "R2 vol20口径": g1 * (0.15 / vol20).clip(0.4, 1.0).fillna(1.0),
        "R2 +深回撤半仓": base * pd.Series(np.where(dd < -0.25, 0.5, 1.0), index=c.index),
    }
    return {k: v.clip(0, 1) for k, v in out.items()}


def main():
    uni = load_universe()
    R = build_rules()
    recs = []
    for code, (grp, df) in uni.items():
        key = "ETF" if grp == "ETF" else "长样本"
        df = df[df["date"] >= SPLIT[key]].reset_index(drop=True)
        if len(df) < 500:
            continue
        F = factors(df)
        ret = F["ret"]
        for name, pos in variants(F, R).items():
            for split in ("train", "test"):
                tr = (df["date"] <= pd.Timestamp(TRAIN_END[key])).to_numpy()
                mask = tr if split == "train" else ~tr
                net, p = run_pos(pos, ret)
                st = stats(net[mask])
                b = stats(ret[mask].fillna(0.0))
                fx = stats(p[mask].mean() * ret[mask].fillna(0.0))
                recs.append(dict(cand=name, group=key, code=code, split=split,
                                 calmar=st["calmar"], d=st["calmar"] - b["calmar"],
                                 dfix=st["calmar"] - fx["calmar"], avg=p[mask].mean()))
    r = pd.DataFrame(recs)
    r.to_csv(OUT / "modulate_candidates.csv", index=False, encoding="utf-8-sig")
    agg = (r.groupby(["cand", "split"])
             .agg(med_d=("d", "median"), pos_rate=("d", lambda s: (s > 0).mean()),
                  med_df=("dfix", "median"), avg=("avg", "median")).round(3))
    print(agg.unstack("split").to_string())
    print("\n== 分组 test 中位 d ==")
    print(r[r.split == "test"].pivot_table(index="cand", columns="group", values="d",
                                           aggfunc="median").round(3).to_string())


if __name__ == "__main__":
    main()
