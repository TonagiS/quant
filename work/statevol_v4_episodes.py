#!/usr/bin/env python3
"""v4 附加诊断：崩盘第一段（峰值→A-SV 开帽日）各臂损失。口径同 statevol_v4（delay=1、5bp）。
峰值/谷底按各标的 BH 在固定日期窗内自行确定（不针对任何臂优化）。"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa
import long_history_backtest as LH  # noqa
import layered_v1_backtest as L  # noqa
import statevol_v4_backtest as S  # noqa
from single_asset_harness import run_pos  # noqa

RUN = S.RUN
# name, peak window, trough-search end
EPIS = {"A2008": ("2007-09-01", "2008-01-31", "2008-12-31"),
        "A2015": ("2015-04-15", "2015-07-01", "2015-09-30"),
        "A2018": ("2017-11-01", "2018-03-31", "2018-12-31"),
        "US2000": ("2000-01-01", "2000-12-31", "2002-12-31"),
        "GL2008": ("2007-06-01", "2008-06-30", "2009-03-31")}


def series():
    meta = pd.read_csv(AB.LHR / "index_meta.csv")
    for r in meta[meta.status == "ok"].itertuples():
        yield "index", r.code, r.name, LH.idx_series(r.code)
    lh = pd.read_csv(AB.LHR / "results_long.csv")
    for r in lh[(lh.panel == "etf") & (lh.period == "full") & (lh.arm == "BH")].itertuples():
        yield "etf_long", r.code, r.name, LH.tr_series(r.code)[0]
    for panel, lst in (("xmkt", AB.XM_PRIMARY), ("xmkt_tr", AB.XM_TR)):
        for code, name, _ in lst:
            yield panel, code, name, AB.load_xm(code)[0]


def main():
    out = []
    for panel, code, name, df in series():
        F = L.slim_factors(df)
        pos, comps = S.arms_positions(F)
        ret = F["ret"].fillna(0.0)
        eq = {"BH": (1 + ret).cumprod()}
        for a, p in pos.items():
            net, _ = run_pos(p, F["ret"], cost=AB.COST)
            eq[a] = (1 + net.fillna(0.0)).cumprod()
        idx = ret.index
        ev_start = idx[LH.WARMUP] if len(idx) > LH.WARMUP else idx[-1]
        for ep, (p0, p1, t1) in EPIS.items():
            if ep.startswith("A") and panel.startswith("xmkt"):
                continue
            if not ep.startswith("A") and not panel.startswith("xmkt"):
                continue
            pw = eq["BH"][(idx >= pd.Timestamp(p0)) & (idx <= pd.Timestamp(p1))]
            if len(pw) < 20 or pw.index[0] < ev_start:
                continue
            P = pw.idxmax()
            tw = eq["BH"][(idx > P) & (idx <= pd.Timestamp(t1))]
            if len(tw) < 5:
                continue
            Tr = tw.idxmin()
            off = comps["capoff"]
            after = off[(idx >= P) & (idx <= Tr)]
            on_days = after.index[after.to_numpy() == 0]
            C = on_days[0] if len(on_days) else Tr
            # 开帽在信号日 C 收盘决定，仓位 C+1 生效；第一段损失统计到 C+1 收盘（含执行滞后）
            iC = idx.get_loc(C)
            C1 = idx[min(iC + 1, idx.get_loc(Tr))]
            rec = dict(panel=panel, code=code, name=name, episode=ep, peak=str(P.date()), cap_on=str(C.date()),
                       trough=str(Tr.date()), days_peak_to_capon=int(idx.get_loc(C) - idx.get_loc(P)),
                       capoff_at_peak=float(off.loc[P]), bh_peak_to_trough=float(eq["BH"][Tr] / eq["BH"][P] - 1))
            for a in ["BH", "A", "A-vol", "A-SV"]:
                e = eq[a]
                rec[f"leg1_{a}"] = float(e[C1] / e[P] - 1)
                seg = e[(idx >= P) & (idx <= Tr)] / e[P]
                rec[f"mdd_{a}"] = float((seg / seg.cummax() - 1).min())
                rec[f"ret_{a}"] = float(seg.iloc[-1] - 1)
            for a in ["A", "A-SV"]:
                rec[f"pos_peak_{a}"] = float(pos[a].loc[P])
            out.append(rec)
    d = pd.DataFrame(out)
    d.to_csv(RUN / "crash_first_leg.csv", index=False, encoding="utf-8-sig")
    print(len(d))


if __name__ == "__main__":
    main()
