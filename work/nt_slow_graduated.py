"""分档版：按 28 日累计份额变化，渐进减仓（不是一刀切空仓）。

档位示例 A（温和）：> -4% 满仓；-6~-4% → 0.7；-8~-6% → 0.4；<= -8% → 0
档位示例 B（激进）：> -6% 满仓；-10~-6% → 0.5；<= -10% → 0

输出 runs/20261108_nt_sell_signal/slow_graduated.md
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

RUN = ROOT / "runs" / "20261108_nt_sell_signal"


def grad(cum: np.ndarray, steps: list[tuple[float, float]]) -> np.ndarray:
    """steps: [(阈值上限, 仓位)]，按从松到紧；未命中给 1.0。"""
    mult = np.ones(len(cum))
    for i, v in enumerate(cum):
        if not np.isfinite(v):
            continue
        for th, lvl in steps:
            if v <= th:
                mult[i] = lvl
                break
    return mult


def main() -> None:
    close = load_panel("ext")
    f = features(close)
    start = int(f["start"])
    _, arr = nt_series(close.index)
    c1 = pd.Series(arr["c1"], index=close.index)
    cum28 = c1.rolling(28, min_periods=14).sum().to_numpy()

    variants = {
        "VT15 基线": np.ones(len(close)),
        "分档A：-4/-6/-8 → 0.7/0.4/0": grad(cum28, [(-8, 0.0), (-6, 0.4), (-4, 0.7)]),
        "分档B：-6/-10 → 0.5/0": grad(cum28, [(-10, 0.0), (-6, 0.5)]),
        "分档C：-5/-8 → 0.7/0.3": grad(cum28, [(-8, 0.3), (-5, 0.7)]),
    }

    lines = ["# 28 日累计减持 · 分档减仓（不是一刀切）", ""]
    lines.append("口径同 POS-01；累计窗口 28 个交易日，阈值按 9 只宽基份额合计的累计变化")
    lines.append("")
    lines.append("## 全样本")
    lines.append("")
    lines.append("| 规则 | 年化 | 回撤 | 卡玛 | 平均仓位 |")
    lines.append("|---|---|---|---|---|")
    res_all = {}
    for name, mult in variants.items():
        res = simulate(close, f, Config(k=5, reb=5, cap=0.40, rule=VolTargetNT(VolTarget(0.15, every=1), mult, name)))
        res_all[name] = res
        m = perf(res["ret"][start:])
        lines.append(f"| {name} | {m['cagr']*100:.2f}% | {m['mdd']*100:.2f}% | {m['calmar']:.2f} | {res['expo'][start:].mean()*100:.0f}% |")

    T = len(close)
    n_live = T - start
    for label, lo, hi in [
        ("训练", start, start + int(n_live * 0.5)),
        ("验证", start + int(n_live * 0.5), start + int(n_live * 0.75)),
        ("测试", start + int(n_live * 0.75), T),
    ]:
        lines.append("")
        lines.append(f"## {label}（{close.index[lo].date()} ~ {close.index[hi-1].date()}）")
        lines.append("")
        lines.append("| 规则 | 年化 | 回撤 | 卡玛 |")
        lines.append("|---|---|---|---|")
        for name, res in res_all.items():
            m = perf(res["ret"][lo:hi])
            lines.append(f"| {name} | {m['cagr']*100:.2f}% | {m['mdd']*100:.2f}% | {m['calmar']:.2f} |")

    years = sorted({d.year for d in close.index[start:]})
    lines.append("")
    lines.append("## 逐年")
    lines.append("")
    lines.append("| 规则 | " + " | ".join(str(y) for y in years) + " |")
    lines.append("|---" * (len(years) + 1) + "|")
    for name, res in res_all.items():
        r = pd.Series(res["ret"][start:], index=close.index[start:])
        cells = [f"{(np.prod(1 + r[r.index.year == y].to_numpy()) - 1) * 100:+.1f}%" for y in years]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")

    (RUN / "slow_graduated.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
