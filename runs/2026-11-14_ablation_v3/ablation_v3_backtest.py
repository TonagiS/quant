#!/usr/bin/env python3
"""A 消融 / A-F 混合 / L 低波组 / 同仓位静态对照 / 跨市场。见 runs/2026-11-14_ablation_v3/PREREG.md"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layered_v1_backtest as L  # noqa: E402
import candidates_v2_backtest as CV  # noqa: E402
import long_history_backtest as LH  # noqa: E402
from single_asset_harness import run_pos, stats  # noqa: E402

ROOT = L.ROOT
RUN = ROOT / "runs" / "2026-11-14_ablation_v3"
LHR = ROOT / "runs" / "2026-11-14_long_history"
CM = ROOT / "data" / "cache_cross_market"
RAW2 = ROOT / "data" / "cache_ohlc_raw_2017"
COST = L.COST
END = "2026-11-13"
LOWVOL_TH, LOWVOL_WIN, LOWVOL_MIN = 0.18, 750, 500
ARMS = ["A", "A-vol", "A-taper", "A-G1", "F", "M", "L"]
NEW_ARMS = ["A-vol", "A-taper", "A-G1", "F", "M", "L"]
WINDOWS = [("full", None, None), ("2000-2002", "2000-01-01", "2002-12-31"),
           ("2007-2012", "2007-01-01", "2012-12-31"), ("2013-2015", "2013-01-01", "2015-12-31"),
           ("2016-2018", "2016-01-01", "2018-12-31"), ("2019-2022", "2019-01-01", "2022-12-31"),
           ("2023-2026", "2023-01-01", END)] + [(f"y{y}", f"{y}-01-01", f"{y}-12-31") for y in (2008, 2015, 2016, 2018, 2022)]
CS_WINDOWS = [("full", "2019-01-01", END), ("2019-2022", "2019-01-01", "2022-12-31"),
              ("2023-2026", "2023-01-01", END), ("y2022", "2022-01-01", "2022-12-31")]
XM_PRIMARY = [("SOX", "费城半导体指数", "价格"), ("NDX", "纳斯达克100", "价格"), ("SP500TR", "标普500全收益", "全收益"),
              ("HSI", "恒生指数", "价格"), ("3033_HK", "南方恒生科技ETF(HSTECH代理)", "ETF收盘(不派息)"),
              ("N225", "日经225", "价格")]
XM_TR = [("SMH", "SMH 半导体ETF", "复权"), ("QQQ", "QQQ", "复权"), ("SPY", "SPY", "复权"), ("2800_HK", "盈富基金", "复权")]
MIN_STATS = 60


def log(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def arms_positions(F):
    g1, volcap, T = L.g1_volcap_T(F)
    taper = T.map(L.TAPER_BASE).astype(float)
    A = (g1 * volcap * taper).clip(0, 1)
    pos = {"A": A, "A-vol": (g1 * taper).clip(0, 1), "A-taper": (g1 * volcap).clip(0, 1),
           "A-G1": (volcap * taper).clip(0, 1)}
    Fp = CV.policy_F(F)
    pos["F"] = Fp
    pos["M"] = (0.5 * A + 0.5 * Fp).clip(0, 1)
    low = (F["vol60"].rolling(LOWVOL_WIN, min_periods=LOWVOL_MIN).median() < LOWVOL_TH).fillna(False)
    pos["L"] = pd.Series(np.where(low.to_numpy(), pos["A-G1"].to_numpy(), A.to_numpy()), index=A.index)
    comps = dict(g1=g1, volcap=volcap, taper=taper, lowvol=low.astype(float))
    return pos, comps


def wstats(net, ret, ps, m):
    if m.sum() < MIN_STATS:
        return None
    s = stats(net[m])
    rr = ret[m].fillna(0.0)
    k = float(ps[m].mean())
    st = stats(k * rr)
    b = stats(rr)
    n = int(m.sum())
    return dict(n=n, cagr=s["cagr"], mdd=s["mdd"], calmar=s["calmar"], avg_pos=k,
                turn=float(ps[m].diff().abs().sum() / (n / 252)),
                static_cagr=st["cagr"], static_mdd=st["mdd"], static_calmar=st["calmar"],
                d_calmar_static=(s["calmar"] - st["calmar"]) if pd.notna(s["calmar"]) and pd.notna(st["calmar"]) else np.nan,
                bh_cagr=b["cagr"], bh_mdd=b["mdd"], bh_calmar=b["calmar"])


def eval_one(nm, panel, windows, ev, rows, diag):
    df = nm["df"]
    F = L.slim_factors(df)
    pos, comps = arms_positions(F)
    ret = F["ret"]
    dates = pd.Series(ret.index)
    nets = {a: run_pos(p, ret, cost=COST) for a, p in pos.items()}
    base = {k: nm.get(k, "") for k in ("code", "name", "sector", "category", "focus", "lane", "ret_type")}
    base.update(panel=panel, first=str(dates.iloc[0].date()), last=str(dates.iloc[-1].date()))
    for w, a, b in windows:
        m = ev.copy()
        if a:
            m &= ((dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b))).to_numpy()
        bh = None
        for arm, (net, ps) in nets.items():
            s = wstats(net, ret, ps, m)
            if s is None:
                break
            rows.append(dict(base, window=w, arm=arm, **s))
            bh = s
        if bh is not None:
            rows.append(dict(base, window=w, arm="BH", n=bh["n"], cagr=bh["bh_cagr"], mdd=bh["bh_mdd"],
                             calmar=bh["bh_calmar"], avg_pos=1.0, turn=0.0, static_cagr=bh["bh_cagr"],
                             static_mdd=bh["bh_mdd"], static_calmar=bh["bh_calmar"], d_calmar_static=0.0,
                             bh_cagr=bh["bh_cagr"], bh_mdd=bh["bh_mdd"], bh_calmar=bh["bh_calmar"]))
    if nm.get("diag"):
        for w, a, b in windows:
            m = ev.copy()
            if a:
                m &= ((dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b))).to_numpy()
            if m.sum() < MIN_STATS or w not in ("full", "2023-2026", "2019-2022", "2016-2018", "2013-2015", "y2015", "y2018"):
                continue
            # 信号日 t 的仓位用于 t+1，按信号日统计（取 shift 前分布，窗口按信号日）
            mm = pd.Series(m, index=ret.index)
            rec = dict(base, window=w, n=int(m.sum()),
                       volcap_at_floor=float((comps["volcap"][mm] <= 0.4 + 1e-9).mean()),
                       volcap_mean=float(comps["volcap"][mm].mean()),
                       g1_mean=float(comps["g1"][mm].mean()),
                       g1_zero_share=float((comps["g1"][mm] == 0).mean()),
                       taper_mean=float(comps["taper"][mm].mean()),
                       vol60_median=float(F["vol60"][mm].median()))
            for arm in ARMS:
                net, ps = nets[arm]
                s = wstats(net, ret, ps, m)
                rec[f"avgpos_{arm}"] = s["avg_pos"]
                rec[f"cagr_{arm}"] = s["cagr"]
                rec[f"mdd_{arm}"] = s["mdd"]
            rec["cagr_BH"] = s["bh_cagr"]
            rec["mdd_BH"] = s["bh_mdd"]
            diag.append(rec)


def load_xm(fname):
    d = pd.read_csv(CM / f"{fname}.csv", parse_dates=["date"]).sort_values("date")
    d = d[(d.adjclose > 0) & (d.date <= pd.Timestamp(END))].drop_duplicates("date")
    gap = d.date.diff().dt.days
    info = ""
    if (gap > 30).any():
        cut = d.date[gap > 30].max()
        d = d[d.date >= cut]
        info = f"gap_cut={cut.date()}"
    out = pd.DataFrame({"date": d.date, "close": d.adjclose, "amount": np.nan, "volume": d.volume})
    return out.reset_index(drop=True), info


def tr_series_any(code):
    """长面板的总收益重建；不复权价在 fullhist_raw 或 raw_2017 任一处。"""
    if (LH.RAW / f"{code}.csv").exists():
        return LH.tr_series(code)
    if (RAW2 / f"{code}.csv").exists():
        old = LH.RAW
        LH.RAW = RAW2
        try:
            return LH.tr_series(code)
        finally:
            LH.RAW = old
    return None, "无不复权价"


def med_block(g, ref):
    o = dict(n=len(g), med_cagr=g.cagr.median(), med_mdd=g.mdd.median(), med_calmar=g.calmar.median(),
             med_avg_pos=g.avg_pos.median(), med_turn=g.turn.median(),
             med_d_calmar_static=g.d_calmar_static.median(),
             pos_d_calmar_static=float((g.d_calmar_static > 0).mean()))
    if ref is not None:
        r = ref.reindex(g.code)
        dc = g.calmar.to_numpy() - r.calmar.to_numpy()
        dm = g.mdd.to_numpy() - r.mdd.to_numpy()
        dg = g.cagr.to_numpy() - r.cagr.to_numpy()
        o.update(med_d_calmar_vs_a=np.nanmedian(dc), pos_d_calmar_vs_a=float(np.nanmean(dc > 0)),
                 med_d_mdd_vs_a=np.nanmedian(dm), med_d_cagr_vs_a=np.nanmedian(dg),
                 pos_d_cagr_vs_a=float(np.nanmean(dg > 0)))
    return o


def main():
    CV.self_check()
    rows, diag, cover = [], [], []
    # ---- long ETF panel: same 190 names as long_history ----
    lh = pd.read_csv(LHR / "results_long.csv")
    lh_etf = lh[(lh.panel == "etf") & (lh.period == "full") & (lh.arm == "BH")]
    uni = pd.read_csv(L.PRIOR / "universe_all.csv").set_index("tx_code")
    for r in lh_etf.itertuples():
        df, info = LH.tr_series(r.code)
        nm = dict(code=r.code, name=r.name, sector=r.sector, category=r.category,
                  focus=r.focus if isinstance(r.focus, str) else "", lane=r.lane, ret_type="ETF总收益", df=df,
                  diag=(r.sector in ("芯片/半导体", "半导体设备")))
        ev = np.arange(len(df)) >= LH.WARMUP
        eval_one(nm, "etf_long", WINDOWS, ev, rows, diag)
    log(f"长 ETF {len(lh_etf)}")
    # ---- index proxies ----
    meta = pd.read_csv(LHR / "index_meta.csv")
    for r in meta[meta.status == "ok"].itertuples():
        df = LH.idx_series(r.code)
        nm = dict(code=r.code, name=r.name, sector=r.sector, category="指数", focus="", lane=r.lane,
                  ret_type=r.ret_type, df=df, diag=r.sector in ("芯片/半导体", "半导体设备"))
        ev = np.arange(len(df)) >= LH.WARMUP
        eval_one(nm, "index", WINDOWS, ev, rows, diag)
    log("指数代理完成")
    # ---- 951 cross-section with corrected TR ----
    cv = pd.read_csv(ROOT / "runs" / "2026-11-14_candidates_v2" / "results_long.csv")
    cs = cv[(cv.in_cross_section == True) & (cv.rule == "A")]  # noqa: E712
    miss = []
    for r in cs.itertuples():
        df, info = tr_series_any(r.tx_code)
        if df is None or len(df) < 260:
            miss.append(dict(code=r.tx_code, name=r.name, reason=info if df is None else "清洗后过短"))
            continue
        foc = r.focus if isinstance(r.focus, str) else ""
        nm = dict(code=r.tx_code, name=r.name, sector=r.sector, category=r.category, focus=foc, lane=r.lane,
                  ret_type="ETF总收益", df=df,
                  diag=(r.sector in ("芯片/半导体", "半导体设备")) or ("科创芯片" in foc) or ("半导体设备" in foc))
        ev = (pd.Series(df.date) >= pd.Timestamp("2019-01-01")).to_numpy()
        eval_one(nm, "cs951", CS_WINDOWS, ev, rows, diag)
    log(f"951 截面完成，缺失 {len(miss)}")
    # ---- cross-market ----
    cmeta = pd.read_csv(RUN / "cross_market_meta.csv")
    for panel, lst in (("xmkt", XM_PRIMARY), ("xmkt_tr", XM_TR)):
        for code, name, rt in lst:
            p = CM / f"{code}.csv"
            if not p.exists():
                cover.append(dict(panel=panel, code=code, name=name, status="missing"))
                continue
            df, info = load_xm(code)
            if len(df) < LH.WARMUP + LH.MIN_EVAL:
                cover.append(dict(panel=panel, code=code, name=name, status="too_short", rows=len(df)))
                continue
            nm = dict(code=code, name=name, sector="半导体" if code in ("SOX", "SMH") else "宽基/指数",
                      category="跨市场", lane="active", ret_type=rt, df=df, diag=code in ("SOX", "SMH"))
            ev = np.arange(len(df)) >= LH.WARMUP
            eval_one(nm, panel, WINDOWS, ev, rows, diag)
            cover.append(dict(panel=panel, code=code, name=name, ret_type=rt, status="ok", rows=len(df),
                              start=str(df.date.iloc[0].date()), eval_start=str(df.date.iloc[LH.WARMUP].date()),
                              end=str(df.date.iloc[-1].date()), info=info))
    res = pd.DataFrame(rows)
    res.to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(diag).to_csv(RUN / "diagnostics_components.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(miss).to_csv(RUN / "exclusions_cs951.csv", index=False, encoding="utf-8-sig")
    cov = pd.DataFrame(cover)
    cov.to_csv(RUN / "cross_market_coverage.csv", index=False, encoding="utf-8-sig")

    # sanity: A == policy_baseline
    # by_window medians
    bw = []
    for (panel, w), g in res.groupby(["panel", "window"]):
        ref = g[g.arm == "A"].set_index("code")
        for arm, ga in g.groupby("arm"):
            bw.append(dict(panel=panel, window=w, arm=arm, **med_block(ga, ref)))
    bw = pd.DataFrame(bw)
    bw.to_csv(RUN / "by_window.csv", index=False, encoding="utf-8-sig")
    vs = res[res.arm != "BH"][["panel", "code", "name", "window", "arm", "avg_pos", "cagr", "mdd", "calmar",
                               "static_cagr", "static_mdd", "static_calmar", "d_calmar_static"]]
    vs.to_csv(RUN / "vs_static.csv", index=False, encoding="utf-8-sig")
    xm = res[res.panel.isin(["xmkt", "xmkt_tr"])]
    xm.to_csv(RUN / "cross_market.csv", index=False, encoding="utf-8-sig")

    # adoption criteria
    def get(panel, w, arm, col):
        s = bw[(bw.panel == panel) & (bw.window == w) & (bw.arm == arm)]
        return float(s[col].iloc[0]) if len(s) else np.nan
    adopt = []
    for arm in NEW_ARMS:
        c1 = []
        for panel in ("index", "etf_long"):
            for w in ("y2008", "y2015", "y2016", "y2018"):
                ca, cb = get(panel, w, "A", "med_calmar"), get(panel, w, arm, "med_calmar")
                ma, mb = get(panel, w, "A", "med_mdd"), get(panel, w, arm, "med_mdd")
                ok = (cb >= ca) and (mb >= ma - 0.03)
                c1.append(dict(panel=panel, window=w, calmar_arm=cb, calmar_A=ca, mdd_arm=mb, mdd_A=ma, ok=bool(ok)))
        c1_ok = all(x["ok"] for x in c1)
        c2 = []
        for panel in ("index", "etf_long"):
            md, pp = get(panel, "full", arm, "med_d_calmar_static"), get(panel, "full", arm, "pos_d_calmar_static")
            c2.append(dict(panel=panel, med=md, pos=pp, ok=bool(md > 0 and pp > 0.5)))
        c2_ok = all(x["ok"] for x in c2)
        xa, xb = get("xmkt", "full", "A", "med_calmar"), get("xmkt", "full", arm, "med_calmar")
        c3_ok = bool(xb >= xa)
        adopt.append(dict(arm=arm, c1_ok=c1_ok, c1_detail=json.dumps(c1, ensure_ascii=False, default=float),
                          c2_ok=c2_ok, c2_detail=json.dumps(c2, ensure_ascii=False, default=float),
                          c3_ok=c3_ok, xmkt_calmar_arm=xb, xmkt_calmar_A=xa,
                          adopt=bool(c1_ok and c2_ok and c3_ok)))
    pd.DataFrame(adopt).to_csv(RUN / "adoption.csv", index=False, encoding="utf-8-sig")
    # A itself vs static
    snap = dict(end=END, cost_one_way=COST, delay=1, arms=ARMS, lowvol=dict(th=LOWVOL_TH, win=LOWVOL_WIN, min=LOWVOL_MIN),
                panels={p: int(res[(res.panel == p) & (res.window == "full")].code.nunique()) for p in res.panel.unique()},
                cs951_missing=len(miss), cross_market=cover, git_commit=L.git_commit(),
                prereg="runs/2026-11-14_ablation_v3/PREREG.md", script="work/ablation_v3_backtest.py",
                caches=[str(LH.QFQ_NEW), str(LH.QFQ_OLD), str(LH.RAW), str(RAW2), str(LH.IDX), str(CM)])
    (RUN / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    shutil.copy2(Path(__file__), RUN / "ablation_v3_backtest.py")
    log("完成")


if __name__ == "__main__":
    main()
