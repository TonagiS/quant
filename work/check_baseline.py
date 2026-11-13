import sys

sys.path.insert(0, ".")
from position.engine import Config, VolTarget, features, load_panel, perf, simulate

close = load_panel("ext")
f = features(close)
print("样本", close.index[0].date(), close.index[-1].date(), len(close))
for cap in (1.0, 0.40):
    for every in (1, 5):
        r = simulate(close, f, Config(k=5, reb=5, cap=cap, rule=VolTarget(0.15, every=every)))
        m = perf(r["ret"])
        print(
            "cap=%.2f every=%d: 年化 %.2f%% 回撤 %.2f%% 卡玛 %.2f 仓位 %.0f%%"
            % (cap, every, m["cagr"] * 100, m["mdd"] * 100, m["calmar"], r["expo"].mean() * 100)
        )
