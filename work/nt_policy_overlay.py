"""把「国家队减持」接进现有的 VT15 仓位规则，看它到底是加分还是减分。

做法：写一个包装 Rule —— 先按 VolTarget(0.15, every=1) 算敞口，
再乘一个「国家队信号倍数」（信号触发后 k 天为 0/0.5，其余 1）。
其余口径完全沿用 POS-01：k=5、每 5 日调仓、逆波动权重、单票上限 40%、单边 5bp。

对照：同一次运行里算 VT15 无叠加的基线，并和 POLICY_v1 里的
ext 16.50% / -17.12% / 卡玛 0.96 对得上就算口径没错。

输出 runs/20261108_nt_sell_signal/policy_overlay.csv + policy_overlay.md
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from position.engine import (  # noqa: E402
    Config, Rule, VolTarget, features, load_panel, perf, simulate,
)

RUN = ROOT / "runs" / "20261108_nt_sell_signal"
SHARES = ROOT / "data" / "national_team" / "etf_shares_hist.csv"


class VolTargetNT(Rule):
    """VolTarget × 国家队信号倍数。"""

    requires_base = True

    def __init__(self, base: VolTarget, mult: np.ndarray, name: str):
        self.base, self.mult, self.name = base, mult, name
        self.every = base.every

    def reset(self):
        self.base.reset()

    def scale(self, j, ctx):
        return float(self.base.scale(j, ctx)) * float(self.mult[min(j, len(self.mult) - 1)])


def nt_series(dates: pd.DatetimeIndex) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    by_date: dict[str, dict[str, float]] = {}
    with SHARES.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            by_date.setdefault(r["date"], {})[r["code"]] = float(r["shares_wan"])
    sdates = sorted(by_date)
    chg = {}
    for i in range(1, len(sdates)):
        d, p = sdates[i], sdates[i - 1]
        a, b = by_date[p], by_date[d]
        common = [c for c in b if c in a]
        chg[d] = (sum(b[c] for c in common) / sum(a[c] for c in common) - 1) * 100

    days = [t.strftime("%Y-%m-%d") for t in dates]
    c1 = np.array([chg.get(d, np.nan) for d in days])
    # 5 日累计（按份额日历，缺失沿用）
    c5 = np.full(len(days), np.nan)
    shifted = sorted(chg)
    for i, d in enumerate(days):
        if d not in chg:
            continue
        j = shifted.index(d)
        seg = [chg[shifted[k]] for k in range(max(0, j - 4), j + 1)]
        c5[i] = sum(seg)
    return pd.Series(c1, index=dates), {"c1": c1, "c5": c5}


def mult_from_signal(cond: np.ndarray, k: int, level: float) -> np.ndarray:
    mult = np.ones(len(cond))
    cool = 0
    for i, hit in enumerate(cond):
        if hit:
            cool = k
        if cool > 0:
            mult[i] = level
            cool -= 1
    return mult


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    rows = []
    for pool in ("ext", "base"):
        close = load_panel(pool)
        f = features(close)
        c1s, arr = nt_series(close.index)
        c1, c5 = arr["c1"], arr["c5"]
        # 指数位置：等权基准 250 日分位
        bench = pd.Series(f["bench"], index=close.index)
        pct250 = bench.rolling(250, min_periods=120).apply(
            lambda w: (w[-1] - w.min()) / (w.max() - w.min()) * 100 if w.max() > w.min() else 50.0,
            raw=True,
        ).to_numpy()

        # 慢变量：20 日累计份额变化 → 仓位倾斜（不是开关）
        cum20 = pd.Series(c1, index=close.index).rolling(20, min_periods=5).sum().to_numpy()
        tilt_soft = np.clip(0.5 + np.nan_to_num(cum20, nan=0.0) / 10.0, 0.0, 1.0)
        tilt_hard = np.clip(0.5 + np.nan_to_num(cum20, nan=0.0) / 5.0, 0.0, 1.0)

        with np.errstate(invalid="ignore"):
            signals = {
                "VT15 基线": np.ones(len(close)),
                "慢变量：20日累计倾斜(1/10)": tilt_soft,
                "慢变量：20日累计倾斜(1/5)": tilt_hard,
                "NT d1≤-1% 空仓10日": mult_from_signal(np.nan_to_num(c1, nan=-9) <= -1.0, 10, 0.0),
                "NT d5≤-1% 空仓10日": mult_from_signal(np.nan_to_num(c5, nan=-9) <= -1.0, 10, 0.0),
                "NT d5≤-1% 半仓10日": mult_from_signal(np.nan_to_num(c5, nan=-9) <= -1.0, 10, 0.5),
                "NT d5≤-2% 空仓20日": mult_from_signal(np.nan_to_num(c5, nan=-9) <= -2.0, 20, 0.0),
                "NT 高位+d5≤-1% 空仓10日": mult_from_signal(
                    (np.nan_to_num(c5, nan=-9) <= -1.0) & (np.nan_to_num(pct250, nan=0) >= 65), 10, 0.0
                ),
                "反向：d5≥+1% 空仓10日": mult_from_signal(np.nan_to_num(c5, nan=9) >= 1.0, 10, 0.0),
                "反向：d5≥+2% 空仓20日": mult_from_signal(np.nan_to_num(c5, nan=9) >= 2.0, 20, 0.0),
            }

        for name, mult in signals.items():
            cfg = Config(k=5, reb=5, cap=0.40, rule=VolTargetNT(VolTarget(0.15, every=1), mult, name))
            res = simulate(close, f, cfg)
            lo = int(f["start"])
            m = perf(res["ret"][lo:])
            rows.append(
                {
                    "pool": pool,
                    "rule": name,
                    "cagr": m["cagr"] * 100,
                    "mdd": m["mdd"] * 100,
                    "calmar": m["calmar"],
                    "sharpe": m["sharpe"],
                    "avg_expo": float(np.mean(res["expo"][lo:])),
                    "turnover": float(np.sum(res["turn"][lo:]) / (len(res["ret"][lo:]) / 242)),
                    "cost_drag": float(np.sum(res["cost"][lo:]) / (len(res["ret"][lo:]) / 242)) * 100,
                }
            )
            print(f"[{pool}] {name}: 年化 {m['cagr']*100:.2f}% 回撤 {m['mdd']*100:.2f}% 卡玛 {m['calmar']:.2f} 平均仓位 {np.mean(res['expo'][lo:]):.0%}")

    out = pd.DataFrame(rows)
    out.to_csv(RUN / "policy_overlay.csv", index=False, encoding="utf-8-sig")
    lines = ["# 国家队减持信号接入 VT15 的增量检验", ""]
    lines.append("口径：k=5 / 每 5 日调仓 / 逆波动 / 单票 40% / 单边 5bp，样本 2019-08-13 起")
    lines.append("")
    lines.append("| 池 | 规则 | 年化 | 最大回撤 | 卡玛 | Sharpe | 平均仓位 | 年换手 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for _, r in out.iterrows():
        lines.append(
            "| {} | {} | {:.2f}% | {:.2f}% | {:.2f} | {:.2f} | {:.0f}% | {:.1f}x |".format(
                r["pool"], r["rule"], r["cagr"], r["mdd"], r["calmar"], r["sharpe"], r["avg_expo"] * 100, r["turnover"]
            )
        )
    (RUN / "policy_overlay.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
