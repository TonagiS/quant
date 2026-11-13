"""事后补充（非预登记）：波动目标 + 个券上限的组合，只用于提出 policy 候选。"""
import sys
sys.path.insert(0, r"E:\Code\quant")
import pandas as pd
from position.engine import (load_panel, features, simulate, Config, Fixed, VolTarget,
                             slice_metrics)

rows = []
combos = [(0.12, 0.30, 1), (0.15, 0.30, 1), (0.20, 0.30, 1),
          (0.12, 0.40, 1), (0.15, 0.40, 1), (0.20, 0.40, 1),
          (0.15, 0.40, 5)]
for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    T, s = len(close), f["start"]
    base = simulate(close, f, Config(rule=Fixed(1.0)))
    f["base_ret"] = base["ret"]
    for tgt, cap, every in combos:
        cfg = Config(cap=cap, rule=VolTarget(tgt, every=every))
        r = simulate(close, f, cfg)
        m = slice_metrics(r, s, T)
        c = m["avg_expo"]
        cm = slice_metrics(simulate(close, f, Config(rule=Fixed(c))), s, T)
        rows.append({"池": pool, "机制": f"波动目标{tgt:.0%}+上限{cap:.0%}", "更新": f"每{every}日",
                     "年化": f"{m['cagr']:.2%}", "波动": f"{m['vol']:.2%}", "夏普": round(m["sharpe"], 2),
                     "回撤": f"{m['mdd']:.2%}", "卡玛": round(m["calmar"], 2),
                     "平均仓位": f"{m['avg_expo']:.0%}", "双边换手": f"{m['turn_two_way']:.0%}",
                     "成本拖累": f"{m['cost_drag']*100:.2f}pp",
                     "卡玛-固定": round(m["calmar"] - cm["calmar"], 2)})

df = pd.DataFrame(rows)
print(df.to_string(index=False))
df.to_csv(r"E:\Code\quant\runs\2026-11-03_POS-01\posthoc_vt_cap.csv", index=False, encoding="utf-8-sig")
