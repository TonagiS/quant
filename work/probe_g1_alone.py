# -*- coding: utf-8 -*-
"""G1 单独 vs 加波动帽 vs 定稿：同一套 universe 过滤 + 同一 train/test 切分。

臂：G1（只动量闸）、G1xvolcap、A（定稿 G1×volcap×taper）、满仓
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_final import TAPER, policy  # noqa: E402
from single_asset_harness import SPLIT, TD, TRAIN_END, factors, load_universe, run_pos, stats  # noqa: E402

uni = load_universe()
rows = []
for code, (grp, df) in uni.items():
    key = "ETF" if grp == "ETF" else "长样本"
    df = df[df["date"] >= SPLIT[key]].reset_index(drop=True)
    if len(df) < 500:
        continue
    F = factors(df)
    c = F["close"]
    bull = ((c > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)
    g = pd.Series(np.nan, index=c.index)
    g[F["mom20"] < 0.0] = 0.0
    g[F["mom20"] > 0.01] = 1.0
    g = g.ffill().fillna(1.0)
    g1 = pd.Series(np.where(g.to_numpy() > 0, 1.0, np.where(bull.to_numpy(), 0.5, 0.0)), index=c.index)
    volcap = (0.15 / F["vol60"]).clip(0.4, 1.0).fillna(1.0)
    A, _ = policy(F)
    arms = {"G1": g1, "G1xvolcap": g1 * volcap, "A_定稿": A, "满仓": pd.Series(1.0, index=c.index)}
    ret = F["ret"]
    tr = (df["date"] <= pd.Timestamp(TRAIN_END[key])).to_numpy()
    for nm, pos in arms.items():
        pos = pos.clip(0, 1)
        net, p = run_pos(pos, ret)
        for split, mask in (("train", tr), ("test", ~tr), ("all", np.ones(len(df), bool))):
            s, b = stats(net[mask]), stats(ret[mask].fillna(0.0))
            fx = stats(p[mask].mean() * ret[mask].fillna(0.0))
            rows.append(dict(key=key, code=code, arm=nm, split=split, cagr=s["cagr"], mdd=s["mdd"],
                             calmar=s["calmar"], avg=float(p[mask].mean()),
                             d=s["calmar"] - b["calmar"], dfix=s["calmar"] - fx["calmar"],
                             turn=float(p[mask].diff().abs().sum() / (mask.sum() / TD))))

R = pd.DataFrame(rows)
for split in ("train", "test", "all"):
    print(f"\n===== {split} =====")
    sub = R[R.split == split]
    out = sub.groupby(["key", "arm"]).agg(
        n=("code", "nunique"), 均仓=("avg", "median"), 年化=("cagr", "median"),
        回撤=("mdd", "median"), 卡玛=("calmar", "median"), d_卡玛=("d", "median"),
        为正=("d", lambda s: (s > 0).mean()), d_同仓=("dfix", "median"), 换手=("turn", "median"))
    print(out.round(3).to_string())
R.to_csv(r"E:\Code\quant\work\g1_alone.csv", index=False, encoding="utf-8-sig")
