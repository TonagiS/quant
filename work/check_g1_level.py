"""检查：定稿策略里的 G1 层是否因 hyst(level=1.0) 变成了空操作。

A 现状 policy()（hyst 第三参=1.0 → reduce 到 1.0 → g1 恒为 1）
B 修正版（level=0：mom20<0 清仓、>+1% 复仓）
C B + volcap/taper 全保留
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_final import TAPER, policy  # noqa: E402
from single_asset_harness import TRAIN_END, factors, hyst, load_universe, run_pos, stats  # noqa: E402


def g1_fixed(F):
    bull = ((F["close"] > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)
    g = hyst(F["mom20"] < 0.0, F["mom20"] > 0.01, 0.0)      # level=0
    return pd.Series(np.where(g.to_numpy() > 0, 1.0, np.where(bull.to_numpy(), 0.5, 0.0)),
                     index=F["close"].index)


def main():
    uni = load_universe()
    rows = []
    for code, (grp, df) in uni.items():
        key = "ETF" if grp == "ETF" else "长样本"
        if len(df) < 400:
            continue
        F = factors(df)
        _, T = policy(F)
        volcap = (0.15 / F["vol60"]).clip(0.4, 1.0).fillna(1.0)
        taper = T.map(TAPER).astype(float)
        base_now = (volcap * taper).clip(0, 1)
        base_fix = (g1_fixed(F) * volcap * taper).clip(0, 1)
        te = (df["date"] > pd.Timestamp(TRAIN_END[key])).to_numpy()
        tr = ~te
        for nm, pos in {"A 现状": base_now, "B G1修正": base_fix}.items():
            net, p = run_pos(pos, F["ret"])
            a, b = stats(net), stats(F["ret"].fillna(0))
            rows.append(dict(group=key, code=code, arm=nm, cagr=a["cagr"], mdd=a["mdd"],
                             calmar=a["calmar"], d=a["calmar"] - b["calmar"],
                             d_tr=stats(net[tr])["calmar"] - stats(F["ret"].fillna(0)[tr])["calmar"],
                             d_te=stats(net[te])["calmar"] - stats(F["ret"].fillna(0)[te])["calmar"],
                             pos=float(np.nanmean(p))))
    r = pd.DataFrame(rows)
    out = Path(r"E:\Code\quant\runs\2026-11-16_g1check")
    out.mkdir(parents=True, exist_ok=True)
    r.round(4).to_csv(out / "g1check.csv", index=False, encoding="utf-8-sig")
    print(r.groupby(["group", "arm"]).agg(d=("d", "median"), d_tr=("d_tr", "median"),
                                          d_te=("d_te", "median"), pos=("pos", "median"),
                                          calmar=("calmar", "median")).round(3).to_string())


if __name__ == "__main__":
    main()
