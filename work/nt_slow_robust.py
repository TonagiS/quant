"""对「28 日累计减持」慢信号做稳健性检查：分段、逐年、防守期统计。

候选：
  A. 28日累计 ≤ -6%，触发后空仓 20 日
  B. 28日累计 ≤ -8%，触发后空仓 20 日
对照：VT15 基线（同口径 k=5 / reb=5 / 单票 40% / 5bp）

分段沿用 POS-01：训练/验证/测试 = 50/25/25。
输出 runs/20261108_nt_sell_signal/slow_robust.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "work"))

from position.engine import Config, VolTarget, features, load_panel, perf, simulate  # noqa: E402
from nt_policy_overlay import VolTargetNT, nt_series  # noqa: E402
from nt_slow_signal import mult_window  # noqa: E402

RUN = ROOT / "runs" / "20261108_nt_sell_signal"


def main() -> None:
    close = load_panel("ext")
    f = features(close)
    start = int(f["start"])
    _, arr = nt_series(close.index)
    c1 = pd.Series(arr["c1"], index=close.index)
    cum28 = c1.rolling(28, min_periods=14).sum().to_numpy()

    variants = {"VT15 基线": np.ones(len(close))}
    for th in (-6.0, -8.0):
        variants[f"28日≤{th:.0f}%·空仓20日"] = mult_window(np.nan_to_num(cum28, nan=9) <= th, 20, 0.0)

    T = len(close)
    n_live = T - start
    bounds = [
        ("训练", start, start + int(n_live * 0.5)),
        ("验证", start + int(n_live * 0.5), start + int(n_live * 0.75)),
        ("测试", start + int(n_live * 0.75), T),
    ]

    lines = ["# 28 日累计减持信号 · 稳健性", ""]
    lines.append("口径同 POS-01：k=5 / 每 5 日调仓 / 逆波动 / 单票 40% / 单边 5bp")
    lines.append("")
    lines.append("## 全样本")
    lines.append("")
    lines.append("| 规则 | 年化 | 回撤 | 卡玛 | 平均仓位 |")
    lines.append("|---|---|---|---|---|")
    res_all = {}
    for name, mult in variants.items():
        cfg = Config(k=5, reb=5, cap=0.40, rule=VolTargetNT(VolTarget(0.15, every=1), mult, name))
        res = simulate(close, f, cfg)
        res_all[name] = res
        m = perf(res["ret"][start:])
        lines.append(
            f"| {name} | {m['cagr']*100:.2f}% | {m['mdd']*100:.2f}% | {m['calmar']:.2f} | {res['expo'][start:].mean()*100:.0f}% |"
        )

    for label, lo, hi in bounds:
        d0, d1 = close.index[lo].date(), close.index[hi - 1].date()
        lines.append("")
        lines.append(f"## {label}（{d0} ~ {d1}）")
        lines.append("")
        lines.append("| 规则 | 年化 | 回撤 | 卡玛 |")
        lines.append("|---|---|---|---|")
        for name, res in res_all.items():
            m = perf(res["ret"][lo:hi])
            lines.append(f"| {name} | {m['cagr']*100:.2f}% | {m['mdd']*100:.2f}% | {m['calmar']:.2f} |")

    # 逐年
    lines.append("")
    lines.append("## 逐年（年化收益）")
    lines.append("")
    years = sorted({d.year for d in close.index[start:]})
    lines.append("| 规则 | " + " | ".join(str(y) for y in years) + " |")
    lines.append("|---" * (len(years) + 1) + "|")
    for name, res in res_all.items():
        r = pd.Series(res["ret"][start:], index=close.index[start:])
        cells = []
        for y in years:
            g = r[r.index.year == y]
            cells.append(f"{(np.prod(1 + g.to_numpy()) - 1) * 100:+.1f}%" if len(g) else "-")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")

    # 防守期统计
    lines.append("")
    lines.append("## 防守期统计（信号触发段）")
    lines.append("")
    for name, mult in variants.items():
        if name.endswith("基线"):
            continue
        off = mult < 0.999
        days = int(off.sum())
        # 连续段
        segs = 0
        prev = False
        for v in off:
            if v and not prev:
                segs += 1
            prev = v
        lines.append(f"- {name}：防守 {days} 天（{days / T * 100:.1f}%），{segs} 段")

    (RUN / "slow_robust.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
