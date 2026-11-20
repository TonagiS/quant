# -*- coding: utf-8 -*-
"""滚动起点对比：策略 vs 无策略（2026-11-20）。

严格按 runs/2026-11-20_roll_starts/PREREG.md 跑。
每个月一个起点，从该日起重新起算年化与窗口内最大回撤，看"策略 − 无策略"的差值分布。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = r"E:\Code\quant"
OUT = os.path.join(ROOT, "runs", "2026-11-20_roll_starts")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "runs", "2026-11-20_portfolio_multiwindow"))

import bt  # noqa: E402
from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, features, load_panel, perf, run_with_base,
)

COST = 0.0005
MIN_BARS = 756          # 最短 3 年窗口（pre-registered）
ARMS = {
    "S2":   dict(wmode="invvol", rule=lambda: Fixed(1.0)),
    "VT15": dict(wmode="invvol", rule=lambda: VolTarget(0.15, 60, every=1)),
}


def drift_ret(close: pd.DataFrame, lo: int) -> np.ndarray:
    """买入后不再平衡的等权（零成本）：各标的从 lo 起归一后取平均。"""
    c = close.iloc[lo:].to_numpy(float)
    base = np.nan_to_num(c[0], nan=1.0)
    base[base <= 0] = 1.0
    nav = np.nanmean(c / base, axis=1)
    r = np.zeros(len(nav))
    r[1:] = nav[1:] / nav[:-1] - 1.0
    return r


def month_starts(dates, y0: int, m0: int, y1: int, min_bars: int = MIN_BARS) -> list:
    """每月第一个交易日；窗口不足 min_bars 的丢掉。"""
    grid = pd.date_range(pd.Timestamp(year=y0, month=m0, day=1),
                         pd.Timestamp(year=y1, month=12, day=1), freq="MS")
    out = []
    for d in grid:
        lo = int(dates.searchsorted(d))
        if lo < len(dates) and len(dates) - lo >= min_bars:
            out.append(lo)
    return out


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    pools = []
    etf, bad = bt.clean(bt.load_etf_panel())
    pools.append(("宽ETF池", etf, 2013, 1))
    ext = load_panel("ext")
    pools.append(("实盘ext池", ext, 2019, 9))
    print(f"[宽ETF池] {etf.shape[1]} 只（剔除 {len(bad)}），[实盘ext池] {ext.shape[1]} 只", flush=True)

    rows, summ = [], []
    for tag, close, y0, m0 in pools:
        f = features(close, min_names=8)
        runs = {}
        for name, arm in ARMS.items():
            cfg = Config(k=5, reb=10, reb_mode="rolling", weight_mode=arm["wmode"],
                         buy_cost=COST, sell_cost=COST, min_names=8, rule=arm["rule"]())
            runs[name] = run_with_base(close, f, cfg)
        bench = np.nan_to_num(f["ret"].mean(axis=1).fillna(0.0).to_numpy())
        dates = close.index
        st = [lo for lo in month_starts(dates, y0, m0, 2023) if lo >= int(f["start"])]
        print(f"[{tag}] 引擎起点 {dates[int(f['start'])].date()}，起点数 {len(st)}："
              f"{dates[st[0]].date()} … {dates[st[-1]].date()}，数据 {dates[0].date()} → {dates[-1].date()}",
              flush=True)

        for lo in st:
            hi = len(dates)
            row = dict(池=tag, 起点=str(dates[lo].date()), 年数=round((hi - lo) / 252.0, 2))
            for name, res in runs.items():
                m = perf(res["ret"][lo:hi])
                row[name + "_年化"] = m["cagr"]
                row[name + "_回撤"] = m["mdd"]
                row[name + "_卡玛"] = m["calmar"]
                row[name + "_均仓"] = float(np.mean(res["expo"][lo:hi]))
            mb = perf(bench[lo:hi])
            md = perf(drift_ret(close, lo))
            row["BH_年化"], row["BH_回撤"], row["BH_卡玛"] = mb["cagr"], mb["mdd"], mb["calmar"]
            row["BH漂移_年化"], row["BH漂移_回撤"] = md["cagr"], md["mdd"]
            rows.append(row)

        d = pd.DataFrame([r for r in rows if r["池"] == tag])
        pairs = [("S2_年化 − BH_年化", d["S2_年化"] - d["BH_年化"], ">0"),
                 ("VT15_年化 − BH_年化", d["VT15_年化"] - d["BH_年化"], ">0"),
                 ("VT15_年化 − S2_年化", d["VT15_年化"] - d["S2_年化"], ">0"),
                 ("VT15_回撤 − S2_回撤", d["VT15_回撤"] - d["S2_回撤"], ">0"),
                 ("VT15_卡玛 − S2_卡玛", d["VT15_卡玛"] - d["S2_卡玛"], ">0"),
                 ("VT15_卡玛 − BH_卡玛", d["VT15_卡玛"] - d["BH_卡玛"], ">0"),
                 ("S2_卡玛 − BH_卡玛", d["S2_卡玛"] - d["BH_卡玛"], ">0")]
        for label, s, _ in pairs:
            s = pd.Series(s).dropna()
            summ.append(dict(池=tag, 指标=label, N=len(s),
                             中位=s.median(), P25=s.quantile(0.25), P75=s.quantile(0.75),
                             方向为正比例=float((s > 0).mean())))
        print(f"[{tag}] 完成 {len(d)} 个起点", flush=True)

    rd = pd.DataFrame(rows)
    sm = pd.DataFrame(summ)
    rd.to_csv(os.path.join(OUT, "roll_starts.csv"), index=False, encoding="utf-8-sig")
    sm.to_csv(os.path.join(OUT, "roll_summary.csv"), index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 260)
    print("\n=== 滚动起点差值汇总 ===")
    print(sm.to_string(index=False, float_format=lambda x: "%.4f" % x))

    # ---- 判据 ----
    def med(tag, label):
        return float(sm[(sm["池"] == tag) & (sm["指标"] == label)]["中位"].iloc[0])

    def frac(tag, label):
        return float(sm[(sm["池"] == tag) & (sm["指标"] == label)]["方向为正比例"].iloc[0])

    r1 = med("宽ETF池", "S2_年化 − BH_年化") < 0
    r2 = med("实盘ext池", "S2_年化 − BH_年化") > 0
    r3 = min(frac("宽ETF池", "VT15_回撤 − S2_回撤"), frac("实盘ext池", "VT15_回撤 − S2_回撤")) >= 0.8
    r4 = min(frac("宽ETF池", "VT15_卡玛 − S2_卡玛"), frac("实盘ext池", "VT15_卡玛 − S2_卡玛")) >= 0.7
    print("\n=== 判据 ===")
    print(f"R1 宽池 S2−BH 年化中位 {med('宽ETF池','S2_年化 − BH_年化')*100:+.2f}pp  (<0)  -> {'✓' if r1 else '✗'}")
    print(f"R2 ext S2−BH 年化中位 {med('实盘ext池','S2_年化 − BH_年化')*100:+.2f}pp  (>0)  -> {'✓' if r2 else '✗'}")
    print(f"R3 VT15 回撤更浅比例：宽池 {frac('宽ETF池','VT15_回撤 − S2_回撤'):.1%} / "
          f"ext {frac('实盘ext池','VT15_回撤 − S2_回撤'):.1%}  (≥80%) -> {'✓' if r3 else '✗'}")
    print(f"R4 VT15 卡玛更高比例：宽池 {frac('宽ETF池','VT15_卡玛 − S2_卡玛'):.1%} / "
          f"ext {frac('实盘ext池','VT15_卡玛 − S2_卡玛'):.1%}  (≥70%) -> {'✓' if r4 else '✗'}")
    print(f"R5 诊断：VT15−S2 年化中位 宽池 {med('宽ETF池','VT15_年化 − S2_年化')*100:+.2f}pp / "
          f"ext {med('实盘ext池','VT15_年化 − S2_年化')*100:+.2f}pp；"
          f"VT15−BH 卡玛中位 宽池 {med('宽ETF池','VT15_卡玛 − BH_卡玛'):+.3f} / "
          f"ext {med('实盘ext池','VT15_卡玛 − BH_卡玛'):+.3f}")


if __name__ == "__main__":
    main()
