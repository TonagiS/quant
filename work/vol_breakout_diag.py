#!/usr/bin/env python3
"""事后诊断（不进判定，PREREG 冻结之后新增）：
组合排序改为随机顺序（5 个种子）——看预登记的"量比降序"排序本身是不是拖累。"""
import sys
from multiprocessing import Pool
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vol_breakout_backtest as vb


def main(panel="all_a"):
    out = vb.RUN / panel
    codes = pd.read_csv(out / "universe.csv").code.tolist()
    cands, paths = [], {}
    with Pool(5) as pool:
        for r in pool.imap_unordered(vb.pass1, codes, chunksize=8):
            if r is None: continue
            cands += [c for c in r["cands"] if c["kind"] == "main"]
            paths.update({k: v for k, v in r["paths"].items() if k.endswith("_main")})
    cands = pd.DataFrame(cands)
    for c in ("sig_date", "entry_date", "exit_date"): cands[c] = pd.to_datetime(cands[c])
    cal = pd.read_csv(vb.DB / "sh000985.csv", parse_dates=["date"]).set_index("date").index
    cal = cal[(cal >= pd.Timestamp("2006-01-04")) & (cal <= vb.END)]
    nd = pd.read_csv(out / "nav_daily.csv", index_col=0, parse_dates=True)
    r_ew = nd["EW-M"].pct_change().fillna(0)
    rows = []
    for seed in range(5):
        rng = np.random.default_rng(vb.SEED + seed)
        cc = cands.copy(); cc["vr"] = rng.random(len(cc))  # 随机数替代量比排序
        nav, ex, traded, costs, tlog, npos = vb.portfolio(cc, paths, cal)
        k = ex.mean(); st = (1 + k * r_ew).cumprod()
        for w in ("full", "crash2015", "y2018", "2023-2026"):
            m = vb.metrics(vb.window_nav(nav, w)); ms = vb.metrics(vb.window_nav(st, w))
            rows.append(dict(seed=seed, window=w, k=k, **m, static_cagr=ms["cagr"], static_mdd=ms["mdd"],
                             static_calmar=ms["calmar"], mean_trade=tlog.ret.mean(), n_trades=len(tlog)))
        print(pd.DataFrame(rows[-4:]).round(3).to_string(), flush=True)
    pd.DataFrame(rows).to_csv(out / "diag_random_rank_portfolio.csv", index=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all_a")
