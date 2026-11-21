# -*- coding: utf-8 -*-
"""切入点敏感性测试：把每个交易日都当成"今天开始跟 S2+VT15"，看后续 20/60/120 日。

口径：
- 净值序列 runs/2026-11-15_k3_532/nav_daily.csv 的 s2vt15 列（已含 5bp 成本、t+1 成交）
- 目标权重 runs/2026-11-15_k3_532/weights_s2vt15.csv，逐日行求和 = 当日总仓
- 只在策略真正开始持仓之后（总仓 > 0 的首日 2019-08-14）取样，避免预热期净值恒为 1 的假样本
- "切入后浮亏" = 窗口内最低净值 / 切入日净值 - 1（体感最差的那一下）

输出：同目录 entry_timing_20261116.md
"""
from pathlib import Path

import numpy as np
import pandas as pd

RUN = Path(__file__).resolve().parents[1] / "runs" / "2026-11-15_k3_532"
OUT = Path(__file__).with_suffix(".md")
HORIZONS = [(20, "1 个月"), (60, "3 个月"), (120, "半年")]


def load():
    nav = pd.read_csv(RUN / "nav_daily.csv", parse_dates=["date"]).set_index("date")["s2vt15"]
    w = pd.read_csv(RUN / "weights_s2vt15.csv", parse_dates=["date"]).set_index("date")
    tot = w.sum(axis=1)
    mask = tot > 0
    return nav[mask], tot[mask]


def window_stats(nav, n, sel=None):
    idx = nav.index if sel is None else nav.index[sel]
    ret, worst = [], []
    for t in idx:
        seg = nav.loc[t:].iloc[: n + 1]
        if len(seg) < n + 1:
            continue
        ret.append(seg.iloc[-1] / seg.iloc[0] - 1)
        worst.append(seg.min() / seg.iloc[0] - 1)
    r, w = np.array(ret), np.array(worst)
    if len(r) == 0:
        return dict(n=0)
    return dict(n=len(r), med=np.median(r) * 100, p5=np.percentile(r, 5) * 100,
                p95=np.percentile(r, 95) * 100, lo=r.min() * 100, hi=r.max() * 100,
                lose=(r < 0).mean() * 100, wmed=np.median(w) * 100,
                w5=(w < -0.05).mean() * 100, w10=(w < -0.10).mean() * 100)


def main():
    nav, tot = load()
    lines = ["# S2+VT15 切入点敏感性（%s → %s，%d 个交易日）" %
             (nav.index[0].date(), nav.index[-1].date(), len(nav)), "",
             "每个交易日都当作建仓日，观察后续表现。净值已含 5bp 成本。", "",
             "## 1. 全样本：任意一天切入", "",
             "| 持有 | 收益中位 | P5 | P95 | 最差 | 亏钱概率 | 浮亏中位 | 浮亏>5% | 浮亏>10% |",
             "|---|---|---|---|---|---|---|---|---|"]
    for n, lab in HORIZONS:
        s = window_stats(nav, n)
        lines.append(f"| {lab}({n}日) | {s['med']:+.1f}% | {s['p5']:+.1f}% | {s['p95']:+.1f}% | "
                     f"{s['lo']:+.1f}% | {s['lose']:.0f}% | {s['wmed']:+.1f}% | "
                     f"{s['w5']:.0f}% | {s['w10']:.0f}% |")

    lines += ["", "## 2. 按入场当天的总仓分组（总仓 = 当日目标权重之和）", "",
              "| 入场总仓 | 天数 | 20日收益中位 | 20日亏钱 | 20日浮亏>10% | 60日收益中位 | 60日亏钱 | 120日收益中位 | 120日亏钱 |",
              "|---|---|---|---|---|---|---|---|---|"]
    buckets = [("满仓 ≥0.9", tot >= 0.9), ("0.6~0.9", (tot >= 0.6) & (tot < 0.9)),
               ("0.3~0.6", (tot >= 0.3) & (tot < 0.6)), ("空/轻 <0.3", tot < 0.3)]
    for name, sel in buckets:
        cells = []
        for n, _ in HORIZONS:
            st = window_stats(nav, n, sel)
            if not st["n"]:
                cells.append(("—", "—", "—"))
                continue
            cells.append((f"{st['med']:+.1f}%", f"{st['lose']:.0f}%", f"{st['w10']:.0f}%"))
        lines.append(f"| {name} | {int(sel.sum())} | {cells[0][0]} | {cells[0][1]} | {cells[0][2]} | "
                     f"{cells[1][0]} | {cells[1][1]} | {cells[2][0]} | {cells[2][1]} |")

    lines += ["", f"当前（{nav.index[-1].date()}）总仓 {tot.iloc[-1]:.2f}。", "",
              "## 3. 怎么读", "",
              "- 切入点影响的是“前一个月体感”，不是期望收益：1 个月收益中位几乎为 0，但分布两头很宽",
              "- 满仓状态切入的样本短期最稳（浮亏超过 10% 的历史 0 次），"
              "轻仓切入反而最容易接着亏——轻仓说明系统自己判定在防守期",
              "- 分批建仓不改变期望，只摊薄单点方差；想降体感就分批，别指望它提高收益"]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print("\n写出：", OUT)


if __name__ == "__main__":
    main()
