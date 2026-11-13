"""为什么收益没掉：逐年的"在场/离场"收益归因，看 27% 均仓怎么拿到满仓的收益。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import SPLIT, factors, load_universe, run_pos, stats  # noqa
from single_asset_final import policy  # noqa

codes = ["sh000300", "sh510300", "sh000905", "sh510880", "sh518880", "sz159915"]
uni = load_universe()
for code in codes:
    if code not in uni:
        continue
    grp, df = uni[code]
    key = "ETF" if grp == "ETF" else "长样本"
    df = df[df["date"] >= SPLIT[key]].reset_index(drop=True)
    F = factors(df)
    pos, T = policy(F)
    ret = F["ret"].fillna(0.0)
    net, p = run_pos(pos, ret)
    d = pd.DataFrame(dict(date=df["date"].to_numpy(), ret=ret.to_numpy(), p=p.to_numpy(),
                          net=net.to_numpy(), T=T.to_numpy()))
    d["y"] = d.date.dt.year
    g = d.groupby("y").apply(lambda x: pd.Series({
        "均仓": x.p.mean(),
        "策略%": ((1 + x.net).prod() - 1) * 100,
        "满仓%": ((1 + x.ret).prod() - 1) * 100,
        "在场日均%": x.loc[x.p > 0.5, "ret"].mean() * 100 * 250 if (x.p > 0.5).any() else np.nan,
        "离场日均%": x.loc[x.p <= 0.1, "ret"].mean() * 100 * 250 if (x.p <= 0.1).any() else np.nan,
    }))
    print(f"\n=== {code} ({grp}) 全样本 均仓 {p.mean():.2f} 卡玛 {stats(net)['calmar']:.3f} vs {stats(ret)['calmar']:.3f}")
    print(g.round(2).to_string())
