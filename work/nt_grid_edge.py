"""补充：m 阈值是不是一直越小越好（网格边缘检验），以及中位数参数的跨池表现。"""

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


def run(pool, c1, close, f, sig, l, k, level):
    mult = mult_rule(sig, l, k, level)
    res = simulate(close, f, Config(k=5, reb=5, cap=0.40, rule=VolTargetNT(VolTarget(0.15, every=1), mult, "x")))
    start = int(f["start"])
    m = perf(res["ret"][start:])
    return m, float(np.mean(res["expo"][start:]))


def main() -> None:
    out = []
    for pool in ("ext", "base"):
        close = load_panel(pool)
        f = features(close)
        _, arr = nt_series(close.index)
        c1 = pd.Series(arr["c1"], index=close.index)
        for n in (14, 20):
            for m in (-10, -12, -15, -20):
                for l in (1, 3, 5):
                    cum = c1.rolling(n, min_periods=max(5, n // 2)).sum().to_numpy()
                    sig = np.nan_to_num(cum, nan=9.0) <= m
                    if sig.sum() < 5:
                        continue
                    mm, expo = run(pool, c1, close, f, sig, l, 20, 0.0)
                    out.append({"pool": pool, "n": n, "m": m, "l": l, "cagr": mm["cagr"] * 100,
                                "mdd": mm["mdd"] * 100, "calmar": mm["calmar"], "expo": expo,
                                "signals": int(sig.sum())})
                    print(f"[{pool}] n={n} m={m} l={l}: 年化 {mm['cagr']*100:.2f}% 回撤 {mm['mdd']*100:.2f}% 卡玛 {mm['calmar']:.2f} 命中 {int(sig.sum())}天", flush=True)

    df = pd.DataFrame(out)
    RUN = ROOT / "runs" / "20261108_nt_sell_signal"
    df.to_csv(RUN / "grid_edge.csv", index=False, encoding="utf-8-sig")

    lines = ["# 阈值边缘检验（m 更极端是否更好）", "", "| 池 | n | m | l | 年化 | 回撤 | 卡玛 | 命中天数 |", "|---|---|---|---|---|---|---|---|"]
    for _, r in df.iterrows():
        lines.append(f"| {r['pool']} | {r['n']} | {r['m']}% | {r['l']} | {r['cagr']:.2f}% | {r['mdd']:.2f}% | {r['calmar']:.2f} | {r['signals']} |")
    (RUN / "grid_edge.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
