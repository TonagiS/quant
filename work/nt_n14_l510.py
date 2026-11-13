"""补跑：n=14 时 l ∈ {5,10}（延迟 5 天 / 10 天才减仓），顺便把 k 的两种读法都覆盖。

k = 减仓持续天数（不是持仓只数；Config.k=5 才是"持 5 只"）。
输出 runs/20261108_nt_sell_signal/grid_n14_l510.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "work"))

from position.engine import Config, Fixed, VolTarget, features, load_panel, perf, simulate  # noqa: E402
from nt_policy_overlay import VolTargetNT, nt_series  # noqa: E402
from nt_grid_gates import mult_rule  # noqa: E402

RUN = ROOT / "runs" / "20261108_nt_sell_signal"


def main() -> None:
    rows = []
    base = {}
    for pool in ("ext", "base"):
        close = load_panel(pool)
        f = features(close)
        _, arr = nt_series(close.index)
        c1 = pd.Series(arr["c1"], index=close.index).to_numpy()
        start = int(f["start"])
        rb = simulate(close, f, Config(k=5, reb=5, cap=0.40, rule=VolTarget(0.15, every=1)))
        mb = perf(rb["ret"][start:])
        base[pool] = mb
        print(f"[{pool}] 基线 年化 {mb['cagr']*100:.2f}% 回撤 {mb['mdd']*100:.2f}% 卡玛 {mb['calmar']:.2f}", flush=True)

        for n in (14,):
            cum = pd.Series(c1).rolling(n, min_periods=max(5, n // 2)).sum().to_numpy()
            for m in (-5, -6, -8, -10, -12):
                for l in (5, 10):
                    for k in (10, 20, 40):
                        for level in (0.0, 0.5):
                            sig = np.nan_to_num(cum, nan=9.0) <= m
                            if sig.sum() < 10:
                                continue
                            mult = mult_rule(sig, l, k, level)
                            res = simulate(
                                close, f,
                                Config(k=5, reb=5, cap=0.40,
                                       rule=VolTargetNT(VolTarget(0.15, every=1), mult, "x")),
                            )
                            mm = perf(res["ret"][start:])
                            segs = []
                            live = len(close) - start
                            for lo, hi in ((start, start + int(live * 0.5)),
                                           (start + int(live * 0.5), start + int(live * 0.75)),
                                           (start + int(live * 0.75), len(close))):
                                segs.append(perf(res["ret"][lo:hi])["calmar"])
                            rows.append({
                                "pool": pool, "n": n, "m": m, "l": l, "k": k, "level": level,
                                "cagr": mm["cagr"] * 100, "mdd": mm["mdd"] * 100,
                                "calmar": mm["calmar"], "expo": float(np.mean(res["expo"][start:])),
                                "seg_ok": sum(1 for s, bb in zip(segs, (
                                    perf(rb["ret"][start:start + int(live * 0.5)])["calmar"],
                                    perf(rb["ret"][start + int(live * 0.5):start + int(live * 0.75)])["calmar"],
                                    perf(rb["ret"][start + int(live * 0.75):])["calmar"],
                                )) if s >= bb),
                            })
                            print(f"[{pool}] n={n} m={m} l={l} k={k} L={level}: "
                                  f"{mm['cagr']*100:.2f}% / {mm['mdd']*100:.2f}% / {mm['calmar']:.2f} "
                                  f"仓位{np.mean(res['expo'][start:])*100:.0f}% 段过{rows[-1]['seg_ok']}/3", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(RUN / "grid_n14_l510.csv", index=False, encoding="utf-8-sig")

    lines = ["# n=14，延迟 l=5 / l=10 的补跑", ""]
    lines.append(f"基线 ext {base['ext']['calmar']:.2f} / base 池 {base['base']['calmar']:.2f}")
    lines.append("")
    lines.append("| 池 | n | m | l | k | 仓位 | 年化 | 回撤 | 卡玛 | 段过 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for _, r in df.sort_values(["pool", "calmar"], ascending=[True, False]).iterrows():
        lines.append(
            f"| {r['pool']} | {r['n']} | {r['m']}% | {r['l']} | {r['k']} | {r['level']} | "
            f"{r['cagr']:.2f}% | {r['mdd']:.2f}% | {r['calmar']:.2f} | {r['seg_ok']}/3 |"
        )
    (RUN / "grid_n14_l510.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
