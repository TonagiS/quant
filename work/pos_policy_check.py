import sys
sys.path.insert(0, r"E:\Code\quant")
from position.engine import load_panel, features, simulate, Config, Fixed, VolTarget, slice_metrics

for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    T, s = len(close), f["start"]
    base = simulate(close, f, Config(rule=Fixed(1.0)))
    f["base_ret"] = base["ret"]
    for every in (1, 5):
        r = simulate(close, f, Config(cap=0.40, rule=VolTarget(0.15, every=every)))
        m = slice_metrics(r, s, T)
        print(pool, "VT15 cap40 every", every, f"{m['cagr']:.2%} {m['vol']:.2%} {m['mdd']:.2%}",
              f"calmar {m['calmar']:.2f} expo {m['avg_expo']:.0%} turn {m['turn_two_way']:.0%}")
