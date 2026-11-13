"""两级决策：先判趋势（T 态）→ 再判策略；族内再用 V/D 调仓位。

层级：
  L1  T 态（5 档，数值定义见 single_asset_state）→ 从候选规则里**在训练段**挑一条：
      满仓 / G1 动量退出 / MA30清仓 / MA60清仓 / VT12 / 投票四件套
  L2  选中规则之上再乘 volcap(0.15, 0.4) —— 统一的风险归一，不再按状态拟合
对照：
  A  定稿策略（G1 × volcap × 趋势衰减）
  B  纯 U14（年线上行减半否则清仓）
  C  L1 只在 T 态之间换规则（本次主实验）
  D  C + L2 关闭（看 volcap 的贡献）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_final import policy  # noqa: E402
from single_asset_harness import (SPLIT, TD, TRAIN_END, build_rules, factors, hyst,  # noqa: E402
                                  load_universe, run_pos, stats)

ROOT = Path(__file__).resolve().parents[1]
TAG = os.environ.get("SA_TAG", "base")
OUT = ROOT / "runs" / f"2026-11-16_twolevel_{TAG}"
OUT.mkdir(parents=True, exist_ok=True)
MIN_DAYS = 300
CANDS = ["满仓", "G1", "A_MA30清仓", "A_MA60清仓", "C_VT12"]


def states(F):
    c, ma200 = F["close"], F["ma200"]
    slope = ma200.pct_change(60)
    above = c > ma200
    T = np.select([above & (slope > 0.02), above & (slope >= -0.02), above,
                   (~above) & (slope >= -0.02)], [0, 1, 2, 3], default=4)
    return pd.Series(T, index=c.index).where(ma200.notna(), 1)


def cand_pos(F, R, name):
    if name == "满仓":
        return pd.Series(1.0, index=F["close"].index)
    if name == "G1":
        bull = ((F["close"] > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)
        g = hyst(F["mom20"] < 0.0, F["mom20"] > 0.01, 1.0)
        return pd.Series(np.where(g.to_numpy() > 0, 1.0, np.where(bull.to_numpy(), 0.5, 0.0)),
                         index=F["close"].index)
    return R[name](F)


def main():
    uni = load_universe()
    R = build_rules()
    rows, picks = [], []
    for code, (grp, df) in uni.items():
        key = "ETF" if grp == "ETF" else "长样本"
        if len(df) < 400:
            continue
        F = factors(df)
        T = states(F)
        ret = F["ret"]
        te = (df["date"] > pd.Timestamp(TRAIN_END[key])).to_numpy()
        tr = ~te

        # ---- L1：按 T 态在训练段挑规则（日均收益最大）
        choice = {}
        for s in range(5):
            m = (T == s).to_numpy() & tr
            if m.sum() < MIN_DAYS:
                choice[s] = "满仓"
                continue
            best, bestmu = "满仓", -9e9
            for nm in CANDS:
                p = cand_pos(F, R, nm)
                mu = float(np.nanmean(p.to_numpy()[m] * ret.to_numpy()[m]))
                if mu > bestmu:
                    best, bestmu = nm, mu
            choice[s] = best
        picks.append(dict(code=code, group=key, **{f"T{s}": choice[s] for s in range(5)}))

        pos_l1 = pd.Series(np.nan, index=F["close"].index)
        for s in range(5):
            pos_l1[T == s] = cand_pos(F, R, choice[s])[T == s]
        pos_l1 = pos_l1.ffill().fillna(1.0)
        volcap = (0.15 / F["vol60"]).clip(0.4, 1.0).fillna(1.0)

        base, _ = policy(F)
        g_bull = ((F["close"] > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)
        g = hyst(F["mom20"] < 0.0, F["mom20"] > 0.01, 1.0)
        u14 = pd.Series(np.where(g.to_numpy() > 0, 1.0, np.where(g_bull.to_numpy(), 0.5, 0.0)),
                        index=F["close"].index)

        arms = {
            "A 定稿": base,
            "B U14": u14,
            "C 两级(L1×volcap)": (pos_l1 * volcap).clip(0, 1),
            "D 两级(仅L1)": pos_l1.clip(0, 1),
        }
        for nm, pos in arms.items():
            net, p = run_pos(pos, ret)
            a = stats(net)
            b = stats(ret.fillna(0.0))
            net_tr, net_te = net[tr], net[te]
            b_tr, b_te = stats(ret.fillna(0.0)[tr]), stats(ret.fillna(0.0)[te])
            rows.append(dict(group=key, code=code, arm=nm, cagr=a["cagr"], mdd=a["mdd"],
                             calmar=a["calmar"], d=a["calmar"] - b["calmar"],
                             d_tr=stats(net_tr)["calmar"] - b_tr["calmar"],
                             d_te=stats(net_te)["calmar"] - b_te["calmar"],
                             avg_pos=float(np.nanmean(p))))

    r = pd.DataFrame(rows)
    r.round(4).to_csv(OUT / "twolevel_results.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(picks).to_csv(OUT / "twolevel_picks.csv", index=False, encoding="utf-8-sig")
    print("== 中位 d_calmar ==")
    print(r.groupby("arm").agg(n=("code", "size"), d=("d", "median"), d_tr=("d_tr", "median"),
                               d_te=("d_te", "median"), pos=("avg_pos", "median"),
                               calmar=("calmar", "median")).round(3).to_string())
    print("\n== 按组（全样本 d）==")
    print(r.pivot_table(index="arm", columns="group", values="d", aggfunc="median").round(3).to_string())
    print("\n== T 态选规则频次（训练段）==")
    pk = pd.DataFrame(picks)
    for s in range(5):
        print(f"T{s}: " + ", ".join(f"{k} {v}" for k, v in pk[f'T{s}'].value_counts().items()))


if __name__ == "__main__":
    main()
