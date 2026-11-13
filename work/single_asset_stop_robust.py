"""A6 + A7：单支层的两件收尾实验。

A6  −8% 止损叠加在定稿策略上：dd60 = close/max(close,60)-1，<=-8% 清仓，回到 -4% 复仓
A7  均线通道稳健性：MA60/MA120 × 半仓/清仓 × buffer(0 / 2%) × 成本(5/10/20bp)
组：全部 / ETF / 长样本；输出中位 d_calmar（vs 满仓同段）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_final import policy  # noqa: E402
from single_asset_harness import SPLIT, TD, TRAIN_END, factors, hyst, load_universe, run_pos, stats  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TAG = os.environ.get("SA_TAG", "base")
OUT = ROOT / "runs" / f"2026-11-16_stop_robust_{TAG}"
OUT.mkdir(parents=True, exist_ok=True)


def ma_arm(F, n, mode, buffer):
    c, ma = F["close"], F[f"ma{n}"]
    if buffer:
        gate = hyst(c < ma * (1 - buffer), c > ma * (1 + buffer), 0.0)
    else:
        gate = (c > ma).astype(float)
    gate[ma.isna()] = np.nan
    gate = gate.ffill().fillna(1.0)
    return gate if mode == "清仓" else 0.5 + 0.5 * gate


def main():
    uni = load_universe()
    rows = []
    for code, (grp, df) in uni.items():
        key = "ETF" if grp == "ETF" else "长样本"
        if len(df) < 400:
            continue
        F = factors(df)
        base, T = policy(F)
        c = F["close"]
        ret = F["ret"]

        # A6
        dd60 = (c / c.rolling(60, min_periods=20).max() - 1)
        stop = hyst(dd60 <= -0.08, dd60 >= -0.04, 0.0)
        arms = {"定稿": base, "定稿+8%止损": base * stop,
                "定稿+8%止损(半仓)": base * (0.5 + 0.5 * stop)}

        # A7
        for n in (60, 120):
            for mode in ("清仓", "半仓"):
                for buf in (0.0, 0.02):
                    arms[f"MA{n}{mode}buf{int(buf * 100)}"] = ma_arm(F, n, mode, buf)

        te = (df["date"] > pd.Timestamp(TRAIN_END[key])).to_numpy()
        tr = ~te
        for nm, pos in arms.items():
            for cost in ((0.0005,) if not nm.startswith("MA") else (0.0005, 0.0010, 0.0020)):
                net, p = run_pos(pos, ret, cost=cost)
                a, b = stats(net), stats(ret.fillna(0.0))
                rows.append(dict(group=key, code=code, arm=nm, cost=cost,
                                 calmar=a["calmar"], d=a["calmar"] - b["calmar"],
                                 d_tr=stats(net[tr])["calmar"] - stats(ret.fillna(0.0)[tr])["calmar"],
                                 d_te=stats(net[te])["calmar"] - stats(ret.fillna(0.0)[te])["calmar"],
                                 avg_pos=float(np.nanmean(p))))
    r = pd.DataFrame(rows)
    r.round(4).to_csv(OUT / "stop_robust.csv", index=False, encoding="utf-8-sig")

    a6 = r[~r["arm"].str.startswith("MA")]
    print("== A6 止损（中位 d_calmar）==")
    print(a6.groupby(["group", "arm"]).agg(d=("d", "median"), d_tr=("d_tr", "median"),
                                           d_te=("d_te", "median"), pos=("avg_pos", "median")).round(3).to_string())
    a7 = r[r["arm"].str.startswith("MA")]
    print("\n== A7 均线通道：按成本看中位 d_calmar ==")
    p = a7.pivot_table(index="arm", columns="cost", values="d", aggfunc="median").round(3)
    print(p.to_string())
    print("\n== A7 分组（成本 5bp）==")
    q = a7[a7["cost"] == 0.0005].pivot_table(index="arm", columns="group", values="d", aggfunc="median").round(3)
    print(q.to_string())


if __name__ == "__main__":
    main()
