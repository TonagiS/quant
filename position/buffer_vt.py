"""POS-09：VT15 + 缓冲带 k+2 的成本边界重测。

预登记见 runs/2026-11-04_POS-09_buffer/PREREG.md。
只用现有 data/cache2019；对称成本；等权基准=valid 日内等权。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, features, load_panel, simulate, slice_metrics,
)

OUT = os.path.join(ROOT, "runs", f"{date.today()}_POS-09_buffer")
os.makedirs(OUT, exist_ok=True)
COSTS = [0.0005, 0.001, 0.002, 0.003, 0.004, 0.006]


def cfg_for(cost, buffer_n=7, reb=5, delay=1, target=0.15, cap=1.0):
    return Config(reb=reb, k=5, delay=delay, cost=cost, buy_cost=cost, sell_cost=cost,
                  cap=cap, buffer_n=buffer_n,
                  rule=VolTarget(target=target, window=60, every=reb))


def bench_ret(f, start):
    r = f["ret"].where(f["valid"]).mean(axis=1).fillna(0.0).to_numpy()
    return r[start:]


def cagr(r):
    r = np.asarray(r, float)
    eq = np.cumprod(1 + r)
    return float(eq[-1] ** (252 / len(r)) - 1)


def breakeven(costs, excess):
    for i in range(1, len(costs)):
        if excess[i - 1] > 0 and excess[i] <= 0:
            a, b = excess[i - 1], excess[i]
            return costs[i - 1] + (costs[i] - costs[i - 1]) * a / (a - b)
    if excess[-1] > 0:
        return None
    return None


def run_one(close, f, cost, buffer_n=7, reb=5, delay=1, target=0.15):
    cfg = cfg_for(cost, buffer_n, reb, delay, target)
    res = simulate(close, f, cfg)
    s, T = f["start"], len(close)
    m = slice_metrics(res, s, T)
    b = bench_ret(f, s)
    bm = cagr(b)
    return res, m, m["cagr"] - bm, bm


def main():
    rows, checks, sens = [], {}, []
    lines = ["# POS-09 VT15 + 缓冲带 k+2 结果", ""]
    for pool in ("ext", "base"):
        close = load_panel(pool)
        f = features(close)
        s = f["start"]
        base_res, base_m, base_ex, bench = run_one(close, f, 0.0005, buffer_n=None)
        buf_res, buf_m, buf_ex, _ = run_one(close, f, 0.0005, buffer_n=7)
        lines += [f"## {pool}", "",
                  f"- VT15 基线 5bp：年化 {base_m['cagr']:.2%}，回撤 {base_m['mdd']:.2%}，卡玛 {base_m['calmar']:.2f}，"
                  f"换手 {base_m['turn_two_way']:.1f}x，超额 {base_ex*100:+.2f}pp",
                  f"- +buffer7 5bp：年化 {buf_m['cagr']:.2%}，回撤 {buf_m['mdd']:.2%}，卡玛 {buf_m['calmar']:.2f}，"
                  f"换手 {buf_m['turn_two_way']:.1f}x，超额 {buf_ex*100:+.2f}pp", ""]
        costs, exc_buf, exc_base = [], [], []
        for c in COSTS:
            _, bm, be, _ = run_one(close, f, c, buffer_n=None)
            _, vm, ve, _ = run_one(close, f, c, buffer_n=7)
            costs.append(c); exc_base.append(be); exc_buf.append(ve)
            rows.append({"池": pool, "配置": "VT15", "cost_bp": c * 1e4, "年化": f"{bm['cagr']:.2%}",
                         "回撤": f"{bm['mdd']:.2%}", "卡玛": round(bm["calmar"], 3),
                         "换手": round(bm["turn_two_way"], 2), "超额pp": round(be * 100, 3)})
            rows.append({"池": pool, "配置": "VT15+buffer7", "cost_bp": c * 1e4, "年化": f"{vm['cagr']:.2%}",
                         "回撤": f"{vm['mdd']:.2%}", "卡玛": round(vm["calmar"], 3),
                         "换手": round(vm["turn_two_way"], 2), "超额pp": round(ve * 100, 3)})
        be_buf = breakeven(costs, exc_buf)
        be_base = breakeven(costs, exc_base)
        c1 = be_buf is not None and be_buf >= 0.003
        c2 = (buf_m["cagr"] >= base_m["cagr"] - 0.01) and (buf_m["calmar"] >= base_m["calmar"] - 0.05)
        c3 = buf_m["turn_two_way"] <= base_m["turn_two_way"] * 0.80
        # C4 delay2 + exclude top3
        _, d2m, d2e, _ = run_one(close, f, 0.0005, buffer_n=7, delay=2)
        perf = (close.iloc[-1] / close.iloc[0] - 1).sort_values(ascending=False)
        top3 = list(perf.index[:3])
        c2close = close.drop(columns=top3)
        f2 = features(c2close)
        _, e3m, e3e, _ = run_one(c2close, f2, 0.0005, buffer_n=7)
        c4 = (d2e > 0) and (e3e > 0)
        checks[pool] = {"breakeven_bp": None if be_buf is None else round(be_buf * 1e4, 1),
                        "breakeven_base_bp": None if be_base is None else round(be_base * 1e4, 1),
                        "C1": bool(c1), "C2": bool(c2), "C3": bool(c3),
                        "C3_turn_ratio": round(buf_m["turn_two_way"] / base_m["turn_two_way"], 3),
                        "C4_delay2_excess_pp": round(d2e * 100, 2),
                        "C4_exclude3_excess_pp": round(e3e * 100, 2),
                        "C4": bool(c4)}
        lines += [f"- 盈亏平衡：buffer {checks[pool]['breakeven_bp']}bp，基线 {checks[pool]['breakeven_base_bp']}bp",
                  f"- 判据：C1 {c1} / C2 {c2} / C3 {c3}（换手比 {checks[pool]['C3_turn_ratio']:.2f}） / C4 {c4}",
                  ""]
        # 敏感性
        for label, kw in [("buffer6", dict(buffer_n=6)), ("buffer8", dict(buffer_n=8)),
                          ("reb10", dict(buffer_n=7, reb=10)),
                          ("VT12", dict(buffer_n=7, target=0.12)),
                          ("VT20", dict(buffer_n=7, target=0.20))]:
            _, m, e, _ = run_one(close, f, 0.0005, **kw)
            sens.append({"池": pool, "变体": label, "年化": f"{m['cagr']:.2%}", "回撤": f"{m['mdd']:.2%}",
                         "卡玛": round(m["calmar"], 3), "换手": round(m["turn_two_way"], 2),
                         "超额pp": round(e * 100, 2), "平均仓位": f"{m['avg_expo']:.0%}"})

    passed = all(v["C1"] and v["C2"] and v["C3"] and v["C4"] for v in checks.values())
    lines += ["## 判定", "",
              f"- 两池同时满足 C1~C4：**{'通过' if passed else '未通过'}**", "",
              "```json", json.dumps(checks, ensure_ascii=False, indent=2), "```", ""]
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "summary.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(sens).to_csv(os.path.join(OUT, "sensitivity.csv"), index=False, encoding="utf-8-sig")
    open(os.path.join(OUT, "REPORT.md"), "w", encoding="utf-8").write("\n".join(lines))
    meta = {"date": str(date.today()), "costs_bp": [c * 1e4 for c in COSTS],
            "config": "k5 buffer7 reb5 delay1 VT15 every5, symmetric costs",
            "checks": checks, "passed": bool(passed)}
    open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8").write(json.dumps(meta, ensure_ascii=False, indent=2))
    print("\n".join(lines))
    print("written", OUT)


if __name__ == "__main__":
    main()
