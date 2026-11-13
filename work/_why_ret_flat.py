"""核查：定稿策略在 test 段的收益（cagr）到底有没有掉，掉多少，和均仓对不对得上。

不解构策略，只算：test 段 pos 均值、strategy cagr、buyhold cagr、Δcagr，按 group 中位。
外加一个"同均仓固定仓位"的 cagr 作为机械对照。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import SPLIT, TD, TRAIN_END, factors, load_universe, run_pos, stats  # noqa
from single_asset_final import policy  # noqa

uni = load_universe()
rows = []
for code, (grp, df) in uni.items():
    key = "ETF" if grp == "ETF" else "长样本"
    df = df[df["date"] >= SPLIT[key]].reset_index(drop=True)
    if len(df) < 500:
        continue
    F = factors(df)
    pos, T = policy(F)
    ret = F["ret"].fillna(0.0)
    net, p = run_pos(pos, ret)
    tr = (df["date"] <= pd.Timestamp(TRAIN_END[key])).to_numpy()
    for split, mask in (("train", tr), ("test", ~tr), ("all", np.ones(len(df), bool))):
        st = stats(net[mask])
        bh = stats(ret[mask])
        fx = stats(p[mask].mean() * ret[mask])
        rows.append(dict(code=code, group=key, split=split, n=int(mask.sum()),
                         avg_pos=float(p[mask].mean()),
                         cagr=st["cagr"], base_cagr=bh["cagr"], dcagr=st["cagr"] - bh["cagr"],
                         cagr_fixed=fx["cagr"],
                         calmar=st["calmar"], base_calmar=bh["calmar"],
                         mdd=st["mdd"], base_mdd=bh["mdd"]))
d = pd.DataFrame(rows)
for split in ("train", "test", "all"):
    print(f"\n===== {split} =====")
    for g in ("ETF", "长样本"):
        s = d[(d.split == split) & (d.group == g)]
        print(f"{g:<4} n={len(s):3d} 均仓 {s.avg_pos.median():.2f}  "
              f"策略cagr {s.cagr.median()*100:+.2f}%  满仓cagr {s.base_cagr.median()*100:+.2f}%  "
              f"Δcagr {s.dcagr.median()*100:+.2f}pp  "
              f"同仓cagr {s.cagr_fixed.median()*100:+.2f}%  "
              f"mdd {s.mdd.median()*100:.1f}% vs {s.base_mdd.median()*100:.1f}%  "
              f"卡玛 {s.calmar.median():.3f} vs {s.base_calmar.median():.3f}  "
              f"Δ>0 {(s.dcagr>0).mean():.0%}")
d.round(5).to_csv(r"E:\Code\quant\runs\2026-11-15_single_asset_state\_ret_decomp.csv", index=False)
