import sys
sys.path.insert(0, r"E:\Code\quant")
from position.engine import load_panel, features, simulate, Config, Fixed, VolTarget, slice_metrics

for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    T, s = len(close), f["start"]
    base = simulate(close, f, Config(rule=Fixed(1.0)))
    f["base_ret"] = base["ret"]
    for cap in (0.35, 0.40):
        r = simulate(close, f, Config(cap=cap, rule=VolTarget(0.15)))
        m = slice_metrics(r, s, T)
        c = m["avg_expo"]
        cm = slice_metrics(simulate(close, f, Config(rule=Fixed(c))), s, T)
        print(pool, "VT15 cap", cap, f"{m['cagr']:.2%} {m['mdd']:.2%}",
              f"calmar {m['calmar']:.2f} expo {m['avg_expo']:.0%}",
              f"excess {m['calmar'] - cm['calmar']:+.2f}")
