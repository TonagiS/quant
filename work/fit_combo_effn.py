"""补充：27 个组合的日收益有效自由度（原始 / 扣掉买入持有后）"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "work"))
from bt_ma5_ma20 import load_series, run  # noqa: E402
from fit_combo_grid import ENTRIES, EXITS, FILTERS, PRE, combo_weights  # noqa: E402

for code in ("sh000300", "sh510300", "sh510500"):
    close = load_series(code).dropna().iloc[PRE:]
    close = close / close.iloc[0]
    n = len(close)
    split = int(n * 0.5)
    c = close.iloc[split:]
    bh = c.pct_change().fillna(0.0).to_numpy()
    P = []
    for en in ENTRIES:
        for fi in FILTERS:
            for ex in EXITS:
                w = combo_weights(close, en, fi, ex).iloc[split:]
                res = run(c, w, 0.0005)
                p = res["pnl"]
                if p.std() > 1e-12:
                    P.append(p)
    M = np.array(P).T
    X = M - bh[:, None]          # 超额（择时贡献）

    def eff(a):
        a = a - a.mean(axis=0)
        lam = np.linalg.svd(a, compute_uv=False) ** 2
        lam = lam[lam > 1e-12]
        return (lam.sum() ** 2) / (lam ** 2).sum()

    print(f"{code}: raw effN={eff(M):.2f}  excess-effN={eff(X):.2f}  "
          f"excess corr median={np.median(np.corrcoef(X.T)[np.triu_indices(X.shape[1],1)]):.2f}")
