import sys
sys.path.insert(0, r"E:\Code\quant")
import numpy as np
import pandas as pd
from position.engine import (load_panel, features, simulate, Config, Fixed, VolTarget,
                             slice_metrics)

rows, mx_rows = [], []
for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    T, s = len(close), f["start"]
    base = simulate(close, f, Config(rule=Fixed(1.0)))
    f["base_ret"] = base["ret"]
    n = T - s
    bounds = [(s, s + int(n * .5)), (s + int(n * .5), s + int(n * .75)), (s + int(n * .75), T)]
    for tgt in (0.12, 0.15):
        res = simulate(close, f, Config(rule=VolTarget(tgt)))
        c = float(np.mean(res["expo"][s:]))
        ctl = simulate(close, f, Config(rule=Fixed(c)))
        for nm, (lo, hi) in zip(("train", "valid", "test"), bounds):
            a = slice_metrics(res, lo, hi)["calmar"]
            b = slice_metrics(ctl, lo, hi)["calmar"]
            rows.append({"pool": pool, "variant": f"VT{tgt:.0%}", "segment": nm,
                         "calmar": round(a, 3), "fixed_calmar": round(b, 3), "diff": round(a - b, 3)})
    for cap in (1.0, 0.30, 0.40):
        rr = simulate(close, f, Config(cap=cap, rule=Fixed(1.0)), keep_weights=True)
        mx = max((max(w.values()) if w else 0) for w in rr["w_hist"][s:])
        mx_rows.append({"pool": pool, "cap": f"{cap:.0%}", "max_single_weight": round(mx, 3)})

pd.DataFrame(rows).to_csv(r"E:\Code\quant\runs\2026-11-03_POS-01\subperiod_vs_fixed.csv",
                          index=False, encoding="utf-8-sig")
pd.DataFrame(mx_rows).to_csv(r"E:\Code\quant\runs\2026-11-03_POS-01\max_weight.csv",
                             index=False, encoding="utf-8-sig")
print(pd.DataFrame(rows).to_string(index=False))
print(pd.DataFrame(mx_rows).to_string(index=False))
