# -*- coding: utf-8 -*-
"""消融：动量选股 / 逆波动权重 / VT15 择时，各拆一层（2026-11-20）。

严格按 runs/2026-11-20_ablation/PREREG.md 跑，跑完不改判据。
七个臂唯一差异是"选股 / 权重 / 敞口"中的一层，其余（reb=10、rolling、delay=1、5bp）全同。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = r"E:\Code\quant"
OUT = os.path.join(ROOT, "runs", "2026-11-20_ablation")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "runs", "2026-11-20_portfolio_multiwindow"))

import bt  # noqa: E402  复用同一套数据加载 / 清洗 / 指标
from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, features, load_panel, monthly,
    run_with_base, slice_metrics, stationary_bootstrap_ci,
)

COST = 0.0005
BIG_K = 10_000          # "全池"时取前 k 名 = 全取
ARMS = {
    "A 等权满仓":   dict(univ="all", wmode="equal",  rule=lambda: Fixed(1.0)),
    "B 等权+VT":    dict(univ="all", wmode="equal",  rule=lambda: VolTarget(0.15, 60, every=1)),
    "C 等权65%":    dict(univ="all", wmode="equal",  rule=lambda: Fixed(0.65)),
    "D S2 满仓":    dict(univ="mom", wmode="invvol", rule=lambda: Fixed(1.0)),
    "E VT15":       dict(univ="mom", wmode="invvol", rule=lambda: VolTarget(0.15, 60, every=1)),
    "F 动量等权":   dict(univ="mom", wmode="equal",  rule=lambda: Fixed(1.0)),
    "G 动量等权+VT": dict(univ="mom", wmode="equal", rule=lambda: VolTarget(0.15, 60, every=1)),
}


def cfg_for(arm: dict, n_names: int) -> Config:
    k = BIG_K if arm["univ"] == "all" else 5
    return Config(k=k, reb=10, reb_mode="rolling", weight_mode=arm["wmode"],
                  buy_cost=COST, sell_cost=COST, min_names=8, rule=arm["rule"]())


def f_for(f: dict, univ: str) -> dict:
    """全池臂：候选 = 全部有效标的（去掉 MA60 过滤，那是选股层的事）。"""
    if univ == "mom":
        return f
    f2 = dict(f)
    f2["cand"] = f["valid"] & f["rm"].notna()
    return f2


def drift_ret(close: pd.DataFrame, lo: int) -> np.ndarray:
    """买入后不再平衡的等权：各标的净值按起点归一后取平均。零成本。"""
    c = close.iloc[lo:].to_numpy(float)
    base = np.nan_to_num(c[0], nan=1.0)
    base[base <= 0] = 1.0
    nav = np.nanmean(c / base, axis=1)
    r = np.zeros(len(nav))
    r[1:] = nav[1:] / nav[:-1] - 1.0
    return r


def run_pool(tag: str, close: pd.DataFrame, start_date: str) -> tuple[list, dict]:
    f = features(close, min_names=8)
    lo = max(int(close.index.searchsorted(pd.Timestamp(start_date))), int(f["start"]))
    hi = len(close)
    dates = close.index
    print(f"[{tag}] {close.shape[1]} 只，{dates[lo].date()} → {dates[-1].date()}，"
          f"{(hi - lo) / 252:.2f} 年", flush=True)

    rows, rets = [], {}
    for name, arm in ARMS.items():
        res = run_with_base(close, f_for(f, arm["univ"]), cfg_for(arm, close.shape[1]))
        m = slice_metrics(res, lo, hi)
        rets[name] = res["ret"][lo:hi]
        rows.append(dict(池=tag, 臂=name, 年化=m["cagr"], 回撤=m["mdd"], 卡玛=m["calmar"],
                         夏普=m["sharpe"], 均仓=m["avg_expo"],
                         双边换手=m["turn_two_way"], 成本拖累=m["cost_drag"]))
        print("  %-14s 年化 %6.2f%%  回撤 %7.2f%%  卡玛 %5.3f  夏普 %5.2f  均仓 %5.1f%%"
              % (name, m["cagr"] * 100, m["mdd"] * 100, m["calmar"], m["sharpe"],
                 m["avg_expo"] * 100), flush=True)

    be = np.nan_to_num(f["ret"].mean(axis=1).fillna(0.0).to_numpy())[lo:hi]
    bd = drift_ret(close, lo)
    for nm, r in (("H 无策略(每日再平衡,零成本)", be), ("I 无策略(买入后漂移,零成本)", bd)):
        m = bt.stats(r, dates[lo:hi])
        rows.append(dict(池=tag, 臂=nm, 年化=m["cagr"], 回撤=m["mdd"], 卡玛=m["calmar"],
                         夏普=m["sharpe"], 均仓=1.0, 双边换手=np.nan, 成本拖累=0.0))
        print("  %-14s 年化 %6.2f%%  回撤 %7.2f%%  卡玛 %5.3f"
              % (nm[:12], m["cagr"] * 100, m["mdd"] * 100, m["calmar"]), flush=True)
    return rows, rets, dates[lo:hi]


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    pools = []
    etf = bt.load_etf_panel()
    etf, bad = bt.clean(etf)
    pools.append(("宽ETF池", etf, "2013-01-04"))
    print(f"[宽ETF池] 清洗后 {etf.shape[1]} 只，剔除 {len(bad)}：{','.join(bad[:5])}", flush=True)
    ext = load_panel("ext")
    pools.append(("实盘ext池", ext, "2019-08-13"))

    all_rows, per_pool_rets = [], {}
    for tag, close, sd in pools:
        rows, rets, dts = run_pool(tag, close, sd)
        all_rows += rows
        per_pool_rets[tag] = (rets, dts)

    # ---- 判据差值（用表里的数直接算，避免二次模拟） ----
    df = pd.DataFrame(all_rows)
    df.to_csv(os.path.join(OUT, "ablation.csv"), index=False, encoding="utf-8-sig")
    piv = {tag: g.set_index("臂") for tag, g in df.groupby("池")}
    diff_rows = []
    spec = [("B 等权+VT", "A 等权满仓", "AB1"),
            ("B 等权+VT", "C 等权65%", "AB2"),
            ("D S2 满仓", "A 等权满仓", "AB3"),
            ("F 动量等权", "D S2 满仓", "AB4a 逆波动 vs 等权(满仓)"),
            ("G 动量等权+VT", "E VT15", "AB4b 逆波动 vs 等权(VT)"),
            ("E VT15", "B 等权+VT", "AB5"),
            ("I 无策略(买入后漂移,零成本)", "H 无策略(每日再平衡,零成本)", "诊断 再平衡红利")]
    for tag, p in piv.items():
        for a, b, label in spec:
            if a not in p.index or b not in p.index:
                continue
            diff_rows.append(dict(池=tag, 判据=label, A=a, B=b,
                                  d年化=p.loc[a, "年化"] - p.loc[b, "年化"],
                                  d回撤=p.loc[a, "回撤"] - p.loc[b, "回撤"],
                                  d卡玛=p.loc[a, "卡玛"] - p.loc[b, "卡玛"]))
    dd = pd.DataFrame(diff_rows)
    dd.to_csv(os.path.join(OUT, "diffs.csv"), index=False, encoding="utf-8-sig")

    # ---- 关键差值 bootstrap（月度 stationary） ----
    boot_rows = []
    for tag, (rets, dates) in per_pool_rets.items():
        lo_dates = dates
        for a, b in (("B 等权+VT", "A 等权满仓"), ("B 等权+VT", "C 等权65%"),
                     ("D S2 满仓", "A 等权满仓"), ("F 动量等权", "D S2 满仓")):
            ma, mb = monthly(rets[a], lo_dates, 0), monthly(rets[b], lo_dates, 0)
            ci = stationary_bootstrap_ci(ma, mb, n_boot=2000, p=1 / 6, seed=7)
            boot_rows.append({"池": tag, "A": a, "B": b,
                              "d夏普中位": ci["sharpe_med"], "d夏普5%": ci["sharpe_lo"],
                              "d夏普95%": ci["sharpe_hi"],
                              "d年化中位": ci["cagr_med"], "d年化5%": ci["cagr_lo"],
                              "d年化95%": ci["cagr_hi"]})
            print(f"[bootstrap] {tag} {a} − {b}：ΔSharpe {ci['sharpe_med']:+.3f} "
                  f"[{ci['sharpe_lo']:+.3f},{ci['sharpe_hi']:+.3f}]  ΔCAGR {ci['cagr_med']:+.2%} "
                  f"[{ci['cagr_lo']:+.2%},{ci['cagr_hi']:+.2%}]", flush=True)
    pd.DataFrame(boot_rows).to_csv(os.path.join(OUT, "bootstrap.csv"), index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 250)
    print("\n=== 消融表 ===")
    print(df.to_string(index=False, float_format=lambda x: "%.4f" % x))
    print("\n=== 差值（判据） ===")
    print(dd.to_string(index=False, float_format=lambda x: "%.4f" % x))


if __name__ == "__main__":
    main()
