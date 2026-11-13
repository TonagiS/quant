"""POS-02 关键对比：新方法 vs VT15 的月度 bootstrap。"""
import sys
sys.path.insert(0, r"E:\Code\quant")
from position.engine import (load_panel, features, simulate, Config, Fixed, VolTarget,
                             monthly, stationary_bootstrap_ci)
from position.advanced import CPPI, EWMAVolTarget, CVaRTarget

pairs = {
    "ERC+VT15": Config(weight_mode="erc", rule=VolTarget(0.15)),
    "ERC+VT15+cap40": Config(weight_mode="erc", cap=0.40, rule=VolTarget(0.15)),
    "EWMA15": Config(rule=EWMAVolTarget(0.15, 0.97)),
    "CVAR2": Config(rule=CVaRTarget(0.02)),
    "CPPI3_75P": Config(rule=CPPI(3, 0.75, "peak")),
    "VT15": Config(rule=VolTarget(0.15)),
}
rows = []
for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    T, s = len(close), f["start"]
    base = simulate(close, f, Config())
    runs = {k: simulate(close, f, cfg) for k, cfg in pairs.items()}
    for k in pairs:
        if k == "VT15":
            continue
        a = monthly(runs[k]["ret"], close.index, s)
        b = monthly(runs["VT15"]["ret"], close.index, s)
        ci = stationary_bootstrap_ci(a, b)
        rows.append({"池": pool, "对比": f"{k} vs VT15",
                     "Sharpe差_5%": round(ci["sharpe_lo"], 3), "Sharpe差_50%": round(ci["sharpe_med"], 3),
                     "Sharpe差_95%": round(ci["sharpe_hi"], 3),
                     "CAGR差_5%": f"{ci['cagr_lo']:.2%}", "CAGR差_50%": f"{ci['cagr_med']:.2%}",
                     "CAGR差_95%": f"{ci['cagr_hi']:.2%}"})
    print(pool, "done")
import pandas as pd
df = pd.DataFrame(rows)
print(df.to_string(index=False))
df.to_csv(r"E:\Code\quant\runs\2026-11-03_POS-02\bootstrap_vs_vt.csv", index=False, encoding="utf-8-sig")
