#!/usr/bin/env python3
"""降息周期事件研究（滞后性检验）+ 各 regime 段落列表。见 runs/2026-11-15_rate_cycle/PREREG.md"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_history_backtest as LH  # noqa: E402
import rate_cycle_backtest as R  # noqa: E402

RUN = R.RUN
KS = [-60, -20, 20, 60, 120, 250]
IDX = [("H00300", "沪深300全收益"), ("H00905", "中证500全收益"), ("sz399006", "创业板指"), ("399986", "中证银行"),
       ("H00922", "中证红利全收益"), ("H11001", "中证全债")]


def close(code):
    if code == "H11001":
        d = pd.read_csv(R.ROOT / "data" / "macro" / "H11001.csv", parse_dates=["date"])
    else:
        d = LH.idx_series(code)
    return d.drop_duplicates("date").set_index("date").close.astype(float).sort_index()


def episodes(kind, cal):
    lab = R.label(cal, kind)
    lab = lab[lab.index >= R.START]
    ch = lab.ne(lab.shift())
    seg = ch.cumsum()
    out = []
    for _, g in lab.groupby(seg):
        out.append(dict(defn=kind, regime=g.iloc[0], start=g.index[0].date(), end=g.index[-1].date(), days=len(g)))
    return pd.DataFrame(out)


def first_events(kind):
    """每段 EASE/TIGHT 的首个事件公告日。"""
    ev = R.event_dates(kind)
    cal = pd.bdate_range("2004-01-01", R.END)
    lab = R.label(cal, kind)
    res = []
    for d, direc in ev.items():
        if d < R.START - pd.Timedelta(days=10):
            continue
        prev = lab[lab.index <= d]
        st = prev.iloc[-1] if len(prev) else "NEUTRAL"
        want = "EASE" if direc == "cut" else "TIGHT"
        if st != want:  # 公告前不在同向 regime -> 新周期首个事件
            res.append((d, direc))
    return res


def main():
    cs = close("H00300")
    cal = cs.index[cs.index <= pd.Timestamp(R.END)]
    eps = pd.concat([episodes("PRI", cal), episodes("RRR", cal)])
    eps.to_csv(RUN / "regime_episodes.csv", index=False, encoding="utf-8-sig")
    rows = []
    for kind in ("PRI", "RRR"):
        for d, direc in first_events(kind):
            for code, name in IDX:
                c = close(code)
                t = c.index.searchsorted(d, side="right")  # 公告后首个交易日
                if t <= 0 or t >= len(c):
                    continue
                i0 = t - 1
                base = c.iloc[i0]
                r = dict(defn=kind, event=str(d.date()), direction=direc, index=name, d0=str(c.index[i0].date()))
                hi = c.iloc[max(0, i0 - 249):i0 + 1].max()
                r["dd_from_250d_high_at_d0"] = base / hi - 1 if i0 >= 249 else np.nan
                for k in KS:
                    j = i0 + k
                    if 0 <= j < len(c) and (k > 0 or i0 + k >= 0):
                        r[f"r{k:+d}"] = (c.iloc[j] / base - 1) if k > 0 else (base / c.iloc[j] - 1)
                    else:
                        r[f"r{k:+d}"] = np.nan
                rows.append(r)
    es = pd.DataFrame(rows)
    # 逐段收益（沪深300全收益 / 创业板指：BH 与 A）
    import ablation_v3_backtest as AB
    import layered_v1_backtest as L
    from single_asset_harness import run_pos
    er = []
    for code, name in (("H00300", "沪深300全收益"), ("H00905", "中证500全收益"), ("sz399006", "创业板指")):
        d = LH.idx_series(code).reset_index(drop=True)
        F = L.slim_factors(d)
        A = AB.arms_positions(F)[0]["A"]
        net, ps = run_pos(A, F["ret"], cost=R.COST)
        for _, e in eps[eps.defn == "PRI"].iterrows():
            m = (F["ret"].index >= pd.Timestamp(e.start)) & (F["ret"].index <= pd.Timestamp(e.end))
            m &= np.arange(len(m)) >= R.WARMUP
            if m.sum() < 20:
                continue
            rb = float((1 + F["ret"][m].fillna(0)).prod() - 1); ra = float((1 + net[m]).prod() - 1)
            nav_b = (1 + F["ret"][m].fillna(0)).cumprod(); nav_a = (1 + net[m]).cumprod()
            er.append(dict(index=name, regime=e.regime, start=e.start, end=e.end, days=int(m.sum()), bh_ret=rb,
                           bh_mdd=float((nav_b / nav_b.cummax() - 1).min()), a_ret=ra,
                           a_mdd=float((nav_a / nav_a.cummax() - 1).min()), a_avg_pos=float(ps[m].mean())))
    pd.DataFrame(er).to_csv(RUN / "episode_returns.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    es.to_csv(RUN / "event_study.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    print(eps.to_string()); print(es[es.index.isin(es.index)].to_string())


if __name__ == "__main__":
    main()
