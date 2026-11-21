#!/usr/bin/env python3
"""ruleA_recheck：单支规则 A / A-vol / A-SV / A-FC 在当前总收益口径、PIT 池和原面板上复核。
加 1 日延迟、5/20bp、滚动起点、四段、对买入持有 / 同平均仓位常数 / 随机择时零假设。规则见 runs/2026-11-21_ruleA_recheck/FREEZE.md。样本内。"""
from __future__ import annotations

import sys
import time
import zlib
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402
import fastcap_v5_backtest as FC  # noqa: E402

L, LH = AB.L, AB.LH
ROOT = AB.ROOT
RUN = ROOT / "runs" / "2026-11-21_ruleA_recheck"
END = pd.Timestamp("2026-11-13")
ARMS = ["A", "A-vol", "A-SV", "A-FC"]
NULL_ARMS = ["A", "A-FC"]
CONFIGS = [(1, 0.0005), (1, 0.002), (2, 0.0005), (2, 0.002)]   # (持仓滞后, 成本)；lag1=原口径（当日收盘信号当日收盘成交），lag2=再延迟 1 日
N_NULL = 100
SEGS = {"2014-16": ("2014-01-01", "2016-12-31"), "2017-19": ("2017-01-01", "2019-12-31"),
        "2020-22": ("2020-01-01", "2022-12-31"), "2023-26": ("2023-01-01", "2026-11-13")}
_P = {}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def panel_tr2():
    if not _P:
        import tr_ens_data as TD
        _P.update(TD.build())
    return _P


def tr2_df(code):
    P = panel_tr2()
    if code not in P["close_tr"].columns:
        return None
    c = P["close_tr"][code]
    a = P["amount"][code] if code in P["amount"].columns else pd.Series(np.nan, index=c.index)
    d = pd.DataFrame({"close": c, "amount": a}).dropna(subset=["close"])
    d = d[(d.close > 0) & (d.index <= END)]
    return d.rename_axis("date").reset_index()


def calmar(r):
    if len(r) < 60:
        return np.nan
    eq = np.cumprod(1 + r)
    cagr = eq[-1] ** (252 / len(r)) - 1 if eq[-1] > 0 else -1.0
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return cagr / abs(dd) if dd < 0 else np.nan


def stats(r):
    if len(r) < 60:
        return dict(cagr=np.nan, mdd=np.nan, calmar=np.nan)
    eq = np.cumprod(1 + r)
    cagr = eq[-1] ** (252 / len(r)) - 1 if eq[-1] > 0 else -1.0
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    return dict(cagr=float(cagr), mdd=dd, calmar=float(cagr / abs(dd)) if dd < 0 else np.nan)


def calmar_mat(Rm):
    """Rm: (k, n) 收益矩阵 → 每行卡玛。"""
    eq = np.cumprod(1 + Rm, axis=1)
    n = Rm.shape[1]
    cagr = np.where(eq[:, -1] > 0, eq[:, -1] ** (252 / n) - 1, -1.0)
    dd = (eq / np.maximum.accumulate(eq, axis=1) - 1).min(axis=1)
    return np.where(dd < 0, cagr / np.abs(np.where(dd < 0, dd, 1)), np.nan)


def net_of(p, ret, lag, cost):
    ps = p.shift(lag).fillna(0.0).to_numpy()
    turn = np.abs(np.diff(np.r_[0.0, ps]))
    return ps * ret - turn * cost, ps


def eval_series(args):
    panel, code, name, df, ev_from = args
    F = L.slim_factors(df)
    pos, _ = FC.arms_positions(F)
    ret = F["ret"].fillna(0.0).to_numpy()
    dates = F["ret"].index
    if panel.startswith("cs950"):      # 原截面口径：2019 起，不要求 250 根预热，至少 60 日
        ev = np.asarray(dates >= pd.Timestamp(ev_from))
        min_n = 60
    else:
        ev = np.arange(len(dates)) >= LH.WARMUP
        if ev_from is not None:
            ev &= np.asarray(dates >= pd.Timestamp(ev_from))
        min_n = 250
    ev &= np.asarray(dates <= END)
    if ev.sum() < min_n:
        return []
    i0 = int(np.argmax(ev))
    i1 = int(len(ev) - np.argmax(ev[::-1]))
    rr = ret[i0:i1]
    d_ = dates[i0:i1]
    bh = stats(rr)
    # 滚动起点：2014-01 起每月，窗口 ≥ 3 年
    st = []
    for y in range(2014, 2024):
        for m in range(1, 13):
            t = pd.Timestamp(y, m, 1)
            if t > pd.Timestamp("2023-11-13"):
                continue
            j = int(d_.searchsorted(t))
            if j < len(d_) and d_[j] >= t and (d_[j] - t).days < 20:
                st.append(j)
    segi = {}
    for k, (a, b) in SEGS.items():
        ja, jb = int(d_.searchsorted(pd.Timestamp(a))), int(d_.searchsorted(pd.Timestamp(b), side="right"))
        if jb - ja >= 200 and d_[ja] <= pd.Timestamp(a) + pd.Timedelta(days=20):
            segi[k] = (ja, jb)
    rows = []
    base = dict(panel=panel, code=code, name=name, first=str(d_[0].date()), last=str(d_[-1].date()), n=len(rr))
    for lag, cost in CONFIGS:
        rows.append(dict(base, lag=lag, cost_bp=int(cost * 1e4), arm="BH", avg_pos=1.0, **bh))
        for arm in ARMS:
            net, ps = net_of(pos[arm], ret, lag, cost)
            nr, pr = net[i0:i1], ps[i0:i1]
            k = float(pr.mean())
            s = stats(nr)
            stc = stats(k * rr)
            row = dict(base, lag=lag, cost_bp=int(cost * 1e4), arm=arm, avg_pos=k, **s,
                       static_calmar=stc["calmar"], d_cal_bh=s["calmar"] - bh["calmar"], d_cal_static=s["calmar"] - stc["calmar"],
                       turn=float(np.abs(np.diff(pr)).sum() / (len(pr) / 252)))
            if arm in NULL_ARMS:
                rng = np.random.default_rng(zlib.crc32(f"{panel}|{code}|{lag}|{cost}|{arm}".encode()))
                n = len(pr)
                if n > 200:
                    ks = rng.integers(60, n - 60, size=N_NULL)
                    PM = np.stack([np.roll(pr, kk) for kk in ks])
                    TM = np.abs(np.diff(np.c_[PM[:, :1], PM], axis=1))
                    RM = PM * rr[None, :] - TM * cost
                    nl = calmar_mat(RM)
                    row["p_null"] = float((1 + np.sum(nl >= s["calmar"])) / (1 + np.sum(np.isfinite(nl)))) if np.isfinite(s["calmar"]) else np.nan
                    row["null_med"] = float(np.nanmedian(nl))
                # 滚动
                if st:
                    da, ds = [], []
                    for j in st:
                        ca = calmar(nr[j:])
                        cb = calmar(rr[j:])
                        kk = float(pr[j:].mean())
                        cs = calmar(kk * rr[j:])
                        da.append(ca - cb); ds.append(ca - cs)
                    da, ds = np.array(da), np.array(ds)
                    row.update(n_starts=len(st), roll_share_bh=float(np.nanmean(da > 0)), roll_med_bh=float(np.nanmedian(da)),
                               roll_share_static=float(np.nanmean(ds > 0)), roll_med_static=float(np.nanmedian(ds)))
                for kseg, (ja, jb) in segi.items():
                    ca = calmar(nr[ja:jb]); cb = calmar(rr[ja:jb]); kk = float(pr[ja:jb].mean()); cs = calmar(kk * rr[ja:jb])
                    row[f"seg_{kseg}_bh"] = ca - cb
                    row[f"seg_{kseg}_static"] = ca - cs
            rows.append(row)
    return rows


def build_tasks():
    tasks, cover = [], []
    lh = pd.read_csv(AB.LHR / "results_long.csv")
    lh_etf = lh[(lh.panel == "etf") & (lh.period == "full") & (lh.arm == "BH")]
    for r in lh_etf.itertuples():
        df, info = LH.tr_series(r.code)
        if df is not None:
            tasks.append(("etf_long_旧口径", r.code, r.name, df.reset_index() if "date" not in df.columns else df, None))
        d2 = tr2_df(r.code)
        if d2 is not None and len(d2) > LH.WARMUP + 250:
            tasks.append(("etf_long_TR2", r.code, r.name, d2, None))
        else:
            cover.append(dict(panel="etf_long_TR2", code=r.code, status="缺失或过短"))
    meta = pd.read_csv(AB.LHR / "index_meta.csv")
    for r in meta[meta.status == "ok"].itertuples():
        df = LH.idx_series(r.code)
        tasks.append(("index19", r.code, r.name, df, None))
    cv = pd.read_csv(ROOT / "runs" / "2026-11-14_candidates_v2" / "results_long.csv")
    cs = cv[(cv.in_cross_section == True) & (cv.rule == "A")]  # noqa: E712
    for r in cs.itertuples():
        d2 = tr2_df(r.tx_code)
        if d2 is None or len(d2) < 260:
            cover.append(dict(panel="cs950_TR2", code=r.tx_code, status="缺失或过短"))
            continue
        tasks.append(("cs950_TR2", r.tx_code, r.name, d2, "2019-01-01"))
    for code, name, rt in AB.XM_PRIMARY:
        if (AB.CM / f"{code}.csv").exists():
            df, info = AB.load_xm(code)
            tasks.append(("xmkt6", code, name, df, None))
    import recombine_core as RC
    D = RC.data()
    first_mem = D["mem_df"].groupby("code")["month_end"].min()
    for code in D["ever"]:
        d2 = tr2_df(code)
        if d2 is None:
            continue
        fm = None
        if first_mem is not None and code in first_mem.index:
            fm = str(pd.Timestamp(first_mem[code]).date())
        tasks.append(("pit189_TR2", code, code, d2, fm))
    return tasks, pd.DataFrame(cover)


def summarize(res):
    out = []
    for (panel, lag, cost, arm), g in res.groupby(["panel", "lag", "cost_bp", "arm"]):
        row = dict(panel=panel, lag=lag, cost_bp=cost, arm=arm, n_names=len(g), cagr_med=g.cagr.median(), mdd_med=g.mdd.median(),
                   calmar_med=g.calmar.median(), avg_pos_med=g.avg_pos.median())
        if arm != "BH":
            row.update(share_cal_gt_bh=float((g.d_cal_bh > 0).mean()), d_cal_bh_med=g.d_cal_bh.median(),
                       share_gt_static=float((g.d_cal_static > 0).mean()), d_cal_static_med=g.d_cal_static.median(),
                       turn_med=g.turn.median())
            if "p_null" in g and g.p_null.notna().any():
                row.update(share_p10=float((g.p_null <= 0.10).mean()), p_null_med=g.p_null.median())
            if "roll_share_bh" in g and g.roll_share_bh.notna().any():
                row.update(roll_share_bh_med=g.roll_share_bh.median(), roll_share_static_med=g.roll_share_static.median(),
                           names_roll70_bh=float((g.roll_share_bh >= 0.7).mean()), names_roll70_static=float((g.roll_share_static >= 0.7).mean()),
                           roll_med_bh_med=g.roll_med_bh.median(), roll_med_static_med=g.roll_med_static.median())
            for k in SEGS:
                for vs in ("bh", "static"):
                    c = f"seg_{k}_{vs}"
                    if c in g and g[c].notna().sum() >= 3:
                        row[f"{c}_med"] = g[c].median()
                        row[f"{c}_pos"] = float((g[c].dropna() > 0).mean())
        out.append(row)
    return pd.DataFrame(out)


def main():
    RUN.mkdir(parents=True, exist_ok=True)
    tasks, cover = build_tasks()
    cover.to_csv(RUN / "coverage_missing.csv", index=False)
    log("tasks", len(tasks), pd.Series([t[0] for t in tasks]).value_counts().to_dict())
    rows = []
    with Pool(6) as p:
        for k, r in enumerate(p.imap_unordered(eval_series, tasks, chunksize=4)):
            rows += r
            if k % 200 == 0:
                log(k, len(tasks))
    res = pd.DataFrame(rows)
    res.to_csv(RUN / "results_long.csv.gz", index=False)
    sm = summarize(res)
    sm.to_csv(RUN / "summary.csv", index=False)
    # A-FC 对 A 逐名
    w = res[res.arm.isin(["A", "A-FC"])].pivot_table(index=["panel", "code", "lag", "cost_bp"], columns="arm", values=["calmar", "mdd"])
    fc = pd.DataFrame({"fc_gt_a": (w[("calmar", "A-FC")] > w[("calmar", "A")]), "mdd_diff": w[("mdd", "A-FC")] - w[("mdd", "A")]})
    fcs = fc.groupby(level=["panel", "lag", "cost_bp"]).agg(share_fc_gt_a=("fc_gt_a", "mean"), mdd_diff_med=("mdd_diff", "median")).reset_index()
    fcs.to_csv(RUN / "afc_vs_a.csv", index=False)
    return sm, fcs


if __name__ == "__main__":
    sm, fcs = main()
    pd.set_option("display.width", 300)
    pd.set_option("display.max_columns", 60)
    print(sm[sm.arm.isin(["BH", "A", "A-FC"])][["panel", "lag", "cost_bp", "arm", "n_names", "cagr_med", "mdd_med", "calmar_med", "avg_pos_med",
                                             "share_cal_gt_bh", "share_gt_static", "d_cal_static_med", "share_p10", "roll_share_bh_med",
                                             "roll_share_static_med"]].round(3).to_string())
    print(fcs.round(3).to_string())
