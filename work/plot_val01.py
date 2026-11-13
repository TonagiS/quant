import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "runs", "2026-11-13_VAL-01_valuation_light")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

cfg = {
    "ext": ("52 只 ETF 池 · 2019-08 ~ 2026-11", ["S2", "VT15", "L1", "L3", "L4"]),
    "long": ("15 个宽基/行业指数池 · 2010-02 ~ 2026-11", ["S2", "VT15", "L1", "L3", "L4"]),
}
names = {"S2": "S2 满仓", "VT15": "VT15（基线）", "L1": "VT15+灯L1",
         "L3": "VT15+灯L3（只砍红）", "L4": "灯调波动目标"}

fig, ax = plt.subplots(2, 1, figsize=(11.5, 8))
for k, (pool, (title, keys)) in enumerate(cfg.items()):
    df = pd.read_csv(os.path.join(D, f"equity_{pool}.csv"), parse_dates=["date"]).set_index("date")
    for key in keys:
        ax[k].plot(df.index, (1 + df[key]).cumprod(), label=names[key],
                   lw=1.4 if key != "S2" else 1.0)
    ax[k].set_title(title)
    ax[k].legend(loc="upper left", fontsize=9)
    ax[k].grid(alpha=0.3)
    ax[k].set_ylabel("净值")
fig.suptitle("VAL-01 估值灯叠加测试：灯 vs 无灯 vs 满仓", y=0.995)
fig.tight_layout()
out = os.path.join(D, "equity.png")
fig.savefig(out, dpi=130)
print("saved", out, os.path.getsize(out) // 1024, "KB")
