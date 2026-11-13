"""慢信号版：用「14/28 日累计份额变化」当减持信号，而不是单日。

两种用法
--------
A. 前瞻收益：累计 ≤ 阈值 的那些天，之后 20 / 40 个交易日 510300 的收益
B. 接进 VT15：信号触发后 20 天空仓/半仓，或者状态式（跌破阈值进防守、
   回升到阈值一半才解除）

输出 runs/20261108_nt_sell_signal/slow_signal.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from position.engine import Config, VolTarget, features, load_panel, perf, simulate  # noqa: E402

sys.path.insert(0, str(ROOT / "work"))
from nt_policy_overlay import VolTargetNT, nt_series  # noqa: E402

RUN = ROOT / "runs" / "20261108_nt_sell_signal"


def mult_window(cond: np.ndarray, k: int, level: float) -> np.ndarray:
    mult = np.ones(len(cond))
    cool = 0
    for i, hit in enumerate(cond):
        if hit:
            cool = k
        if cool > 0:
            mult[i] = level
            cool -= 1
    return mult


def mult_state(cum: np.ndarray, enter: float, exit_: float, level: float) -> np.ndarray:
    """cum <= enter 进防守；cum >= exit_ 解除。"""
    mult = np.ones(len(cum))
    off = False
    for i, v in enumerate(cum):
        if not np.isfinite(v):
            mult[i] = 1.0
            continue
        if v <= enter:
            off = True
        elif v >= exit_:
            off = False
        mult[i] = level if off else 1.0
    return mult


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    lines = ["# 慢信号：14/28 日累计减持", ""]

    close = load_panel("ext")
    f = features(close)
    _, arr = nt_series(close.index)
    c1 = pd.Series(arr["c1"], index=close.index)

    # A. 前瞻收益（用 510300 当参照）
    price = close["sh510300"].to_numpy()
    fwd = {}
    for n in (20, 40):
        s = np.full(len(price), np.nan)
        s[:-n] = price[n:] / price[:-n] - 1
        fwd[n] = s * 100

    lines.append("## A. 累计减持之后 510300 的收益（%）")
    lines.append("")
    lines.append("| 窗口 | 阈值 | 命中天数 | 后20日 中位/均值 | 后40日 中位/均值 |")
    lines.append("|---|---|---|---|---|")
    base = {n: fwd[n][np.isfinite(fwd[n])] for n in (20, 40)}
    lines.append(
        "| 全样本 | - | {} | {:.2f} / {:.2f} | {:.2f} / {:.2f} |".format(
            len(base[20]), np.median(base[20]), np.mean(base[20]), np.median(base[40]), np.mean(base[40])
        )
    )
    for w in (14, 28):
        cum = c1.rolling(w, min_periods=max(5, w // 2)).sum().to_numpy()
        for th in (-3, -5, -8):
            mask = np.isfinite(cum) & (cum <= th)
            v20 = fwd[20][mask & np.isfinite(fwd[20])]
            v40 = fwd[40][mask & np.isfinite(fwd[40])]
            if len(v20) < 5:
                lines.append(f"| {w}日 | {th}% | {int(mask.sum())} | - | - |")
                continue
            lines.append(
                f"| {w}日 | {th}% | {int(mask.sum())} | {np.median(v20):.2f} / {np.mean(v20):.2f} | "
                f"{np.median(v40):.2f} / {np.mean(v40):.2f} |"
            )

    # B. 接进 VT15
    lines.append("")
    lines.append("## B. 接进 VT15（ext 池，其余口径同 POS-01）")
    lines.append("")
    rows = []
    for w in (14, 28):
        cum = c1.rolling(w, min_periods=max(5, w // 2)).sum().to_numpy()
        for th in (-5, -6, -8):
            variants = {
                f"{w}日≤{th}% 空仓20日": mult_window(np.nan_to_num(cum, nan=9) <= th, 20, 0.0),
                f"{w}日≤{th}% 半仓20日": mult_window(np.nan_to_num(cum, nan=9) <= th, 20, 0.5),
                f"{w}日≤{th}% 状态式半仓": mult_state(cum, th, th / 2, 0.5),
                f"{w}日≤{th}% 状态式空仓": mult_state(cum, th, th / 2, 0.0),
            }
            for name, mult in variants.items():
                cfg = Config(k=5, reb=5, cap=0.40, rule=VolTargetNT(VolTarget(0.15, every=1), mult, name))
                res = simulate(close, f, cfg)
                lo = int(f["start"])
                m = perf(res["ret"][lo:])
                rows.append((name, m["cagr"] * 100, m["mdd"] * 100, m["calmar"], np.mean(res["expo"][lo:]) * 100))

    base_m = perf(simulate(close, f, Config(k=5, reb=5, cap=0.40, rule=VolTarget(0.15, every=1)))["ret"][int(f["start"]):])
    lines.append(
        f"基线：年化 {base_m['cagr'] * 100:.2f}% / 回撤 {base_m['mdd'] * 100:.2f}% / 卡玛 {base_m['calmar']:.2f}"
    )
    lines.append("")
    lines.append("| 规则 | 年化 | 回撤 | 卡玛 | 平均仓位 |")
    lines.append("|---|---|---|---|---|")
    for name, cagr, mdd, calmar, expo in sorted(rows, key=lambda r: -r[3]):
        lines.append(f"| {name} | {cagr:.2f}% | {mdd:.2f}% | {calmar:.2f} | {expo:.0f}% |")

    (RUN / "slow_signal.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
