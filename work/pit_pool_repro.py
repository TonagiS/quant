#!/usr/bin/env python3
"""复现 dsh 的 S2 / VT15 / VT15·5日 与等权数字（ext 池 2019 起；宽 92 只 2013/2017/2019 起）。
口径猜测：engine.Config(k=5, reb=10/5, reb_mode='rolling', cost=5bp, delay=1)，窗口内从零起算
（数据截断到窗口起点，engine 自己预热 150 根）；无策略 = f['bench'] 等权每日再平衡、无成本。
data/cache2019 不在本机，ext 池改用 cache_ohlc_full（腾讯前复权，2017 起）。
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "position")); sys.path.insert(0, str(ROOT / "indicators" / "code"))
sys.path.insert(0, str(ROOT / "work"))
import types
sys.modules.setdefault('akshare', types.ModuleType('akshare'))  # pools 只用常量，不联网
import engine as E  # noqa
import pit_pool_data as D  # noqa
from pools import get_pool  # noqa
from macd_structure import ETF_POOL  # noqa

RUN = ROOT / "runs" / "2026-11-20_pit_pool_diversified"


def run_set(close, label, rows, every_opts=(1, "reb")):
    f = E.features(close)
    lo = f["start"]
    b = f["bench"].to_numpy()
    br = np.r_[0, b[1:] / b[:-1] - 1]
    m = E.perf(br[lo + 1:]); rows.append({"set": label, "arm": "EW_daily", "start": str(close.index[lo].date()), **m})
    for reb, nm in ((10, "S2"),):
        res = E.simulate(close, f, E.Config(k=5, reb=reb, reb_mode="rolling"))
        rows.append({"set": label, "arm": nm, "start": str(close.index[lo].date()), **E.perf(res["ret"][lo:])})
    for ev in every_opts:
        for reb, nm in ((10, "VT15"), (5, "VT15_5d")):
            e = reb if ev == "reb" else ev
            cfg = E.Config(k=5, reb=reb, reb_mode="rolling", rule=E.VolTarget(0.15, 60, 40, every=e))
            res = E.run_with_base(close, f, cfg)
            rows.append({"set": label, "arm": f"{nm}_every{ev}", "start": str(close.index[lo].date()),
                         **E.perf(res["ret"][lo:])})


def main():
    P = D.build()
    ext = list(get_pool("ext", ETF_POOL).keys())
    rows = []
    for kind in ("close_naive", "close_tr"):
        C = P[kind]
        have = [c for c in ext if c in C.columns]
        c19 = C.loc["2019-01-02":, have]
        c19 = c19.loc[:, c19.notna().sum() >= 300]
        run_set(c19, f"ext_{kind}_n{c19.shape[1]}", rows)
        fh = sorted(p.stem for p in D.FH.glob("*.csv"))
        for st in ("2013-01-01", "2017-01-01", "2019-01-01"):
            cc = C.loc[st:, fh]
            cc = cc.loc[:, cc.notna().sum() >= 300]
            run_set(cc, f"broad92_{kind}_{st[:4]}_n{cc.shape[1]}", rows)
    df = pd.DataFrame(rows)
    df.to_csv(RUN / "repro_check.csv", index=False)
    print("missing ext:", [c for c in ext if c not in P["close_tr"].columns], len(ext))
    with pd.option_context("display.width", 200, "display.max_rows", 200):
        print(df[["set", "arm", "start", "cagr", "mdd", "calmar"]].round(3).to_string())


if __name__ == "__main__":
    main()
