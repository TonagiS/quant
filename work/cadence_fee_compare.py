"""5 日 / 10 交易日 一调的换手与佣金成本对比（去跨境池，10 万本金）。

佣金口径（场内 ETF，免印花税）：
    A 券商默认：万 2.5，每笔最低 5 元
    B 优惠/免五：万 1.5，无最低
    C 保守：万 5，每笔最低 5 元
场外（支付宝 C 类，7 天后 0 赎回费）不适用本脚本。

用法：venv\\Scripts\\python.exe work\\cadence_fee_compare.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "position"))

from engine import Config, features, load_panel, run_with_base  # noqa: E402

START, END = "2026-06-01", "2026-09-30"
CAP = 100_000.0
CROSS = ["sh513500", "sh513100"]


def fees(notional: float, rate: float, floor: float) -> float:
    if notional <= 0:
        return 0.0
    return max(notional * rate, floor)


def main() -> None:
    close = load_panel("ext").drop(columns=[c for c in CROSS if c in load_panel("ext").columns])
    f = features(close)
    dates = close.index
    idx = np.flatnonzero((dates >= START) & (dates <= END))
    i0, i1 = int(idx[0]), int(idx[-1])

    for reb, tag in ((10, "14 自然日（主臂）"), (5, "5 交易日（参考臂）")):
        cfg = Config(k=5, reb=reb, buy_cost=0.0005, sell_cost=0.0005,
                     reb_mode="rolling_change", weight_mode="invvol")
        res = run_with_base(close, f, cfg, keep_weights=True)
        w_hist = res["w_hist"]
        turn = 0.0
        n_trades = 0
        fee = {"A": 0.0, "B": 0.0, "C": 0.0}
        for i in range(i0, i1 + 1):
            prev = w_hist[i - 1] if i > 0 else {}
            cur = w_hist[i]
            if prev == cur:
                continue
            eq = CAP * res["equity"][i] / res["equity"][i0 - 1]
            for c in set(prev) | set(cur):
                d = cur.get(c, 0.0) - prev.get(c, 0.0)
                if abs(d) < 1e-9:
                    continue
                notional = abs(d) * eq
                turn += notional
                n_trades += 1
                fee["A"] += fees(notional, 0.00025, 5.0)
                fee["B"] += fees(notional, 0.00015, 0.0)
                fee["C"] += fees(notional, 0.0005, 5.0)
        ret = res["equity"][i1] / res["equity"][i0 - 1] - 1
        print(f"\n=== {tag} reb={reb} 天 ===")
        print(f"换仓 {res['n_due']} 次 / 成交 {n_trades} 笔；双边换手 {turn / CAP * 100:.0f}%（本金 10 万）")
        print(f"收益 {ret * 100:+.1f}%；佣金：A 万2.5+最低5元 {fee['A']:.0f} 元（{fee['A'] / CAP * 100:.2f}%）"
              f"｜B 万1.5免五 {fee['B']:.0f} 元（{fee['B'] / CAP * 100:.2f}%）"
              f"｜C 万5+最低5元 {fee['C']:.0f} 元（{fee['C'] / CAP * 100:.2f}%）")


if __name__ == "__main__":
    main()
