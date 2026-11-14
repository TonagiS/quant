#!/usr/bin/env python3
"""创业板 + AI 概念描述性回跑。口径见 runs/2026-11-14_chinext_ai/NOTE.md。只套用已冻结的臂。"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402
import bull_fast_backtest as BF  # noqa: E402
import fc_fastgate_backtest as FG  # noqa: E402
import layered_v1_backtest as L  # noqa: E402
import long_history_backtest as LH  # noqa: E402
import fastcap_ma_sens as MS  # noqa: E402
from single_asset_harness import run_pos  # noqa: E402

RUN = AB.ROOT / "runs" / "2026-11-14_chinext_ai"
WARM = 250
END = LH.END
CN_WIN = [("full", None, None), ("2013-2015", "2013-01-01", "2015-12-31"), ("y2015", "2015-01-01", "2015-12-31"),
          ("y2016", "2016-01-01", "2016-12-31"), ("y2018", "2018-01-01", "2018-12-31"),
          ("2019-2022", "2019-01-01", "2022-12-31"), ("y2022", "2022-01-01", "2022-12-31"), ("2023-2026", "2023-01-01", END)]
AI_RX = "人工智能|AI|算力|云计算|大数据|机器人|通信|5G|CPO"


def positions(F):
    pos, comps = MS.arms_positions(F)
    idx = pos["A"].index
    bf = BF.bench_flags("CSI300TR")
    fast, mkt = BF.align(bf["FAST-MKT"], idx), BF.align(bf["MKT"], idx)
    P = {"A": pos["A"], "A-FC": pos["A-FC"], "A-FC30": pos["A-FC30"], "A-FC20": pos["A-FC20"]}
    g1, volcap, T = L.g1_volcap_T(F)
    taper = T.map(L.TAPER_BASE).astype(float)
    for k in FG.NEW:
        P[k], _ = FG.gated_positions(F, g1, volcap, taper, k)
    P["A-FAST-MKT"] = pd.Series(np.where(fast, pos["A-FC"], pos["A"]), index=idx)
    P["A-BULL-MKT"] = pd.Series(np.where(mkt, pos["A-FC"], pos["A"]), index=idx)
    return P


def run_one(df, base, windows, start_mode):
    df = df.reset_index(drop=True)
    F = L.slim_factors(df)
    P = positions(F)
    ret = F["ret"]; idx = ret.index; dates = pd.Series(idx)
    nets = {a: run_pos(p, ret, cost=AB.COST) for a, p in P.items()}
    ev = np.arange(len(idx)) >= WARM
    rows = []
    for w, a, b in windows:
        m = ev.copy()
        if a:
            m &= ((dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b))).to_numpy()
        if m.sum() < AB.MIN_STATS:
            continue
        st = dict(base, window=w, start=str(dates[m].iloc[0].date()), end=str(dates[m].iloc[-1].date()))
        bh = None
        for arm, (net, ps) in nets.items():
            s = AB.wstats(net, ret, ps, m); bh = s
            rows.append(dict(st, arm=arm, **s))
        rows.append(dict(st, arm="BH", n=bh["n"], cagr=bh["bh_cagr"], mdd=bh["bh_mdd"], calmar=bh["bh_calmar"], avg_pos=1.0,
                         turn=0.0, d_calmar_static=0.0))
    return rows


def main():
    cv = pd.read_csv(AB.ROOT / "runs" / "2026-11-14_candidates_v2" / "results_long.csv")
    cv = cv[cv.rule == "A"].drop_duplicates("tx_code")
    rows, lst = [], []
    # 1) 创业板
    df = LH.idx_series("sz399006")
    rows += run_one(df, dict(group="创业板指", code="sz399006", name="创业板指(价格)", ret_type="价格"), CN_WIN, "warm")
    cn = cv[cv.name.str.contains(r"^(?:平安)?创业板(?:50)?ETF") & (cv["first"] <= "2020-12-31")]
    for r in cn.itertuples():
        d, msg = AB.tr_series_any(r.tx_code)
        lst.append(dict(group="创业板ETF", code=r.tx_code, name=r.name, first=r.first, status="ok" if d is not None else msg))
        if d is not None and len(d) > WARM + AB.MIN_STATS:
            rows += run_one(d, dict(group="创业板ETF", code=r.tx_code, name=r.name, ret_type="总收益重建"), CN_WIN, "warm")
    # 2) AI
    ai = cv[cv.name.str.contains(AI_RX) & ~cv.name.str.startswith("港股通") & (cv.sector != "跨境")]
    for r in ai.itertuples():
        main_grp = r.first <= "2021-12-31"
        g = "AI主组" if main_grp else "AI次组"
        d, msg = AB.tr_series_any(r.tx_code)
        ok = d is not None and len(d) > WARM + AB.MIN_STATS
        lst.append(dict(group=g, code=r.tx_code, name=r.name, first=r.first, status="ok" if ok else (msg if d is None else "too_short")))
        if ok:
            w = [("2023-2026", "2023-01-01", END)] if main_grp else [("own", None, None)]
            rows += run_one(d, dict(group=g, code=r.tx_code, name=r.name, ret_type="总收益重建"), w, "warm")
    for code, nm in (("930713", "CS人工智能"), ("H30590", "中证机器人"), ("931160", "中证通信设备(非CPO)")):
        rows += run_one(LH.idx_series(code), dict(group="AI指数", code=code, name=nm + "(价格)", ret_type="价格"),
                        [("2023-2026", "2023-01-01", END)], "warm")
    R = pd.DataFrame(rows)
    R.to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    pd.DataFrame(lst).to_csv(RUN / "universe.csv", index=False, encoding="utf-8-sig")
    S = R.groupby(["group", "window", "arm"]).agg(n=("code", "size"), cagr=("cagr", "median"), mdd=("mdd", "median"),
                                                 calmar=("calmar", "median"), avg_pos=("avg_pos", "median"),
                                                 turn=("turn", "median"), d_static=("d_calmar_static", "median"),
                                                 pos_static=("d_calmar_static", lambda x: (x > 0).mean())).reset_index()
    S.to_csv(RUN / "summary.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    print(pd.DataFrame(lst).groupby(["group", "status"]).size())


if __name__ == "__main__":
    main()
