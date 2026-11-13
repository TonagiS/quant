# -*- coding: utf-8 -*-
"""reb_mode 三态对照：grid（回测基准）/ rolling（实盘现行为）/ rolling_change（换过才清零）。"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "position"))
import engine  # noqa: E402

TD = 252


def one(pool, cad, mode, vt=True, tol=0.05):
    close = engine.load_panel(pool)
    f = engine.features(close)
    rule = engine.VolTarget(target=0.15, window=60, every=1) if vt else engine.Fixed(1.0)
    cfg = engine.Config(k=5, reb=cad, fee_tier=True, rule=rule, reb_mode=mode, reb_change_tol=tol)
    r = engine.run_with_base(close, f, cfg)
    st = f["start"]
    m = engine.perf(r["ret"][st:])
    days = len(close) - st
    return {"pool": pool, "cad": cad, "mode": mode,
            "cagr": m["cagr"], "mdd": m["mdd"], "calmar": m["calmar"],
            "turn": float(np.sum(r["turn"][st:]) / (days / TD)),
            "cost": float(np.sum(r["cost"][st:]) / (days / TD)),
            "n_due": r.get("n_due"), "n_skip": r.get("n_skip")}


def main():
    rows = []
    for pool in ("ext", "base"):
        for cad in (10, 14):
            for mode in ("grid", "rolling", "rolling_change"):
                rows.append(one(pool, cad, mode))
    df = pd.DataFrame(rows)
    fmt = df.copy()
    for c in ("cagr", "mdd", "cost"):
        fmt[c] = (fmt[c] * 100).round(2)
    for c in ("calmar", "turn"):
        fmt[c] = fmt[c].round(2)
    pd.set_option("display.width", 220)
    print("（VT15 + 场外分档费 + 5bp 佣金；cad 单位=交易日）")
    print(fmt.to_string(index=False))
    out = os.path.join(ROOT, "runs", "2026-11-10_reb-reset-ab")
    os.makedirs(out, exist_ok=True)
    df.to_csv(os.path.join(out, "reb_mode_results.csv"), index=False, encoding="utf-8-sig")
    print("\n->", os.path.join(out, "reb_mode_results.csv"))


if __name__ == "__main__":
    main()
