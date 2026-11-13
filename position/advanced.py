"""仓位控制方法（网上经典做法）在 S2 上的回测。

方法来源（2026-11-03 检索；公式见 SOURCES.md）：
  * 波动率目标 / EWMA 波动率目标：Quantpedia "An Introduction to Volatility Targeting"
  * CPPI（含最大回撤 floor 变体）：Quantpedia "Introduction to CPPI"
  * 风险平价 ERC / 最大分散化：Quantpedia "Risk Parity Asset Allocation"
  * CVaR 目标：把风险预算从波动换成 5% 条件尾部损失（Quantpedia VaR 方法页）

本模块只做仓位层；信号/调仓/成本沿用 position.engine 的 S2 口径。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

from position.engine import (  # noqa: E402
    Config, Fixed, Rule, VolTarget, features, load_panel, monthly, simulate,
    slice_metrics, stationary_bootstrap_ci, yearly_table,
)

TD = 252


def _clip01(x):
    return float(np.clip(x, 0.0, 1.0))


class CPPI(Rule):
    """CPPI：floor = 初始/峰值的 floor_frac；敞口 = M × cushion / NAV，封顶 100%。"""
    def __init__(self, m=3.0, floor_frac=0.80, mode="initial", name=None):
        self.m, self.floor_frac, self.mode = float(m), float(floor_frac), mode
        self.peak = 1.0
        self.name = name or f"CPPI M={m:g} floor={floor_frac:.0%}({mode})"

    def reset(self):
        self.peak = 1.0

    def scale(self, j, ctx):
        eq = ctx["equity"][: j + 1]
        if len(eq) < 2:
            return 1.0
        self.peak = max(self.peak, float(eq[-1]))
        floor = self.floor_frac if self.mode == "initial" else self.floor_frac * self.peak
        cushion = max(float(eq[-1]) - floor, 0.0)
        return _clip01(self.m * cushion / max(float(eq[-1]), 1e-9))


class EWMAVolTarget(Rule):
    """EWMA 波动率目标：lambda 越大越平滑（Quantpedia 用 half-life≈20 日）。"""
    requires_base = True

    def __init__(self, target=0.15, lam=0.97, min_obs=20, cap=1.0, name=None):
        self.target, self.lam, self.min_obs, self.cap = target, lam, min_obs, cap
        self.var = None
        self.last = None
        self.name = name or f"EWMA波动目标{target:.0%}(λ={lam})"

    def reset(self):
        self.var = None
        self.last = None

    def scale(self, j, ctx):
        r = ctx.get("base_rets")
        s = int(ctx.get("start", 0))
        if r is None or j - s < self.min_obs:
            return 1.0
        if self.var is None:
            seg = np.asarray(r[s: j + 1], dtype=float)
            seg = seg[np.isfinite(seg)]
            self.var = float(np.var(seg, ddof=1)) if len(seg) > 1 else float(seg[-1] ** 2)
            self.last = j
        else:
            for k in range(self.last + 1, j + 1):
                x = float(r[k]) if np.isfinite(r[k]) else 0.0
                self.var = self.lam * self.var + (1 - self.lam) * x * x
            self.last = j
        vol = float(np.sqrt(max(self.var, 1e-12) * TD))
        return _clip01(self.target / vol) if vol > 1e-8 else 1.0


class CVaRTarget(Rule):
    """按 60 日 5% CVaR（条件尾部损失）缩放总敞口。"""
    requires_base = True

    def __init__(self, target_cvar=0.02, window=60, min_obs=40, cap=1.0, name=None):
        self.target_cvar, self.window, self.min_obs, self.cap = target_cvar, window, min_obs, cap
        self.name = name or f"CVaR目标{target_cvar:.1%}({window}日)"

    def scale(self, j, ctx):
        r = ctx.get("base_rets")
        s = int(ctx.get("start", 0))
        if r is None or j - s < self.min_obs:
            return 1.0
        seg = np.asarray(r[max(s, j - self.window + 1): j + 1], dtype=float)
        seg = seg[np.isfinite(seg)]
        if len(seg) < self.min_obs:
            return 1.0
        q = float(np.quantile(seg, 0.05))
        tail = seg[seg <= q]
        cvar = float(tail.mean()) if len(tail) else q
        if not np.isfinite(cvar) or cvar >= 0:
            return 1.0
        return _clip01(self.target_cvar / abs(cvar))


def variants():
    return [
        ("S2", "S2 基线(逆波动/满仓)", Config()),
        ("VT15", "波动目标15%(60日)", Config(rule=VolTarget(0.15))),
        ("CPPI3_80", "CPPI M=3 floor=80%(初始)", Config(rule=CPPI(3, 0.80, "initial"))),
        ("CPPI3_75P", "CPPI M=3 floor=75%(峰值)", Config(rule=CPPI(3, 0.75, "peak"))),
        ("CPPI5_75P", "CPPI M=5 floor=75%(峰值)", Config(rule=CPPI(5, 0.75, "peak"))),
        ("CPPI5_85", "CPPI M=5 floor=85%(峰值)", Config(rule=CPPI(5, 0.85, "peak"))),
        ("CPPI8_90", "CPPI M=8 floor=90%(峰值)", Config(rule=CPPI(8, 0.90, "peak"))),
        ("EWMA12", "EWMA波动目标12%", Config(rule=EWMAVolTarget(0.12, 0.97))),
        ("EWMA15", "EWMA波动目标15%", Config(rule=EWMAVolTarget(0.15, 0.97))),
        ("CVAR15", "CVaR目标1.5%/日", Config(rule=CVaRTarget(0.015))),
        ("CVAR2", "CVaR目标2%/日", Config(rule=CVaRTarget(0.02))),
        ("ERC", "ERC风险平价(126日)", Config(weight_mode="erc")),
        ("ERC_VT12", "ERC+波动目标12%", Config(weight_mode="erc", rule=VolTarget(0.12))),
        ("ERC_VT15", "ERC+波动目标15%", Config(weight_mode="erc", rule=VolTarget(0.15))),
        ("ERC_VT15_CAP40", "ERC+波动15%+单票40%", Config(weight_mode="erc", cap=0.40, rule=VolTarget(0.15))),
        ("MDP", "最大分散化(126日)", Config(weight_mode="mdp")),
        ("MDP_VT15", "最大分散化+波动15%", Config(weight_mode="mdp", rule=VolTarget(0.15))),
    ]


def run(pool="ext", out_dir=None):
    close = load_panel(pool)
    f = features(close)
    T, s = len(close), f["start"]
    base = simulate(close, f, Config())
    n = T - s
    bounds = [(s, s + int(n * 0.5)), (s + int(n * 0.5), s + int(n * 0.75)), (s + int(n * 0.75), T)]
    rows, subs, eq = [], [], {"date": close.index[s:]}
    runs = {}
    cfgs = {}
    for key, label, cfg in variants():
        res = simulate(close, f, cfg)
        runs[key] = res
        cfgs[key] = cfg
        m = slice_metrics(res, s, T)
        c = float(np.clip(m["avg_expo"], 0, 1))
        ctl = simulate(close, f, Config(cost=cfg.cost, cap=cfg.cap,
                                        weight_mode=cfg.weight_mode, rule=Fixed(c)))
        cm = slice_metrics(ctl, s, T)
        rows.append({"池": pool, "key": key, "机制": label,
                     "年化": f"{m['cagr']:.2%}", "波动": f"{m['vol']:.2%}",
                     "夏普": round(m["sharpe"], 2), "回撤": f"{m['mdd']:.2%}",
                     "卡玛": round(m["calmar"], 2), "平均仓位": f"{m['avg_expo']:.0%}",
                     "双边换手": f"{m['turn_two_way']:.0%}", "成本拖累": f"{m['cost_drag']*100:.2f}pp",
                     "固定同仓位卡玛": round(cm["calmar"], 2),
                     "卡玛-固定": round(m["calmar"] - cm["calmar"], 2),
                     "夏普-固定": round(m["sharpe"] - cm["sharpe"], 2)})
        for nm, (lo, hi) in zip(("训练", "验证", "测试"), bounds):
            sm = slice_metrics(res, lo, hi)
            subs.append({"池": pool, "机制": label, "段": nm, "年化": f"{sm['cagr']:.2%}",
                         "回撤": f"{sm['mdd']:.2%}", "卡玛": round(sm["calmar"], 2),
                         "平均仓位": f"{sm['avg_expo']:.0%}"})
        eq[key] = res["ret"][s:]

    # 关键对比 bootstrap（月度收益）
    boots = []
    for key, ctl_key in [("CPPI3_80", "S2"), ("CPPI5_85", "S2"), ("EWMA15", "FIXED"),
                         ("CVAR2", "FIXED"), ("ERC", "S2"), ("MDP", "S2")]:
        res = runs[key]
        if ctl_key == "FIXED":
            c = float(np.mean(res["expo"][s:]))
            ccfg = cfgs[key]
            ctl = simulate(close, f, Config(cost=ccfg.cost, cap=ccfg.cap,
                                            weight_mode=ccfg.weight_mode, rule=Fixed(c)))
        else:
            ctl = runs[ctl_key]
        ci = stationary_bootstrap_ci(monthly(res["ret"], close.index, s),
                                     monthly(ctl["ret"], close.index, s))
        boots.append({"池": pool, "对比": key + (" vs 固定同仓位" if ctl_key == "FIXED" else " vs S2"),
                      "Sharpe差_5%": round(ci["sharpe_lo"], 3), "Sharpe差_50%": round(ci["sharpe_med"], 3),
                      "Sharpe差_95%": round(ci["sharpe_hi"], 3),
                      "CAGR差_5%": f"{ci['cagr_lo']:.2%}", "CAGR差_95%": f"{ci['cagr_hi']:.2%}"})
    return rows, subs, boots, eq


if __name__ == "__main__":
    import json
    from datetime import date

    out = os.path.join(ROOT, "runs", f"{date.today()}_POS-02")
    os.makedirs(out, exist_ok=True)
    all_rows, all_subs, all_boots = [], [], []
    eq_all = {}
    for pool in ("ext", "base"):
        rows, subs, boots, eq = run(pool)
        all_rows += rows
        all_subs += subs
        all_boots += boots
        for k, v in eq.items():
            eq_all[f"{pool}_{k}"] = v
        print(f"== {pool} ==")
        for r in rows:
            print(r["机制"], r["年化"], r["回撤"], "calmar", r["卡玛"], "expo", r["平均仓位"])
    import pandas as pd
    pd.DataFrame(all_rows).to_csv(os.path.join(out, "summary.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(all_subs).to_csv(os.path.join(out, "subperiod.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(all_boots).to_csv(os.path.join(out, "bootstrap.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(eq_all).to_csv(os.path.join(out, "equity.csv"), index=False, encoding="utf-8-sig")
    print("written", out)
