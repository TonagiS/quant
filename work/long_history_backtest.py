#!/usr/bin/env python3
"""长历史七臂回测（规则不变）。见 runs/2026-11-14_long_history/PREREG.md

面板：
  etf   长历史 ETF（全量历史，比例复权总收益，预热 250 日后 ≥1250 日，近60日均额≥500万，非货币）
  index 指数代理（中证/腾讯；全收益或价格指数，见 index_meta.csv）
敏感性：etf_qfqnaive = 同一批 ETF 用旧管线的 qfq pct_change（仅全历史）
"""
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
from single_asset_harness import run_pos, stats  # noqa: E402

ROOT = L.ROOT
RUN = ROOT / "runs" / "2026-11-14_long_history"
QFQ_OLD = ROOT / "data" / "cache_ohlc_full"
QFQ_NEW = ROOT / "data" / "cache_ohlc_fullhist"
RAW = ROOT / "data" / "cache_ohlc_fullhist_raw"
IDX = ROOT / "data" / "cache_index_proxy"
UNI = L.PRIOR / "universe_all.csv"

WARMUP = 250
MIN_EVAL = 1250
MIN_STATS = 60
COST = L.COST
END = "2026-11-13"
SPLIT_JUMP = 0.30
MAX_GAP_DAYS = 30
ARMS = ["A", "B", "C", "D", "E", "F"]
ALL_ARMS = ["BH"] + ARMS
PERIODS = [("full", None, None), ("2007-2012", "2007-01-01", "2012-12-31"),
           ("2013-2015", "2013-01-01", "2015-12-31"), ("2016-2018", "2016-01-01", "2018-12-31"),
           ("2019-2022", "2019-01-01", "2022-12-31"), ("2023-2026", "2023-01-01", END)]
YEARS = list(range(2005, 2027))
KEY_SECTORS = ["芯片/半导体", "半导体设备", "机器人", "传媒", "银行", "风格-红利/低波",
               "商品-黄金", "宽基", "债券", "养殖/畜牧", "通信", "军工"]


def log(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def tr_series(code: str, naive=False):
    """返回 (df[date, close(比例复权), amount], info)。"""
    qp = QFQ_NEW / f"{code}.csv"
    if not qp.exists():
        qp = QFQ_OLD / f"{code}.csv"
    if not qp.exists():
        return None, "无 qfq 缓存"
    q = pd.read_csv(qp, parse_dates=["date"]).drop_duplicates("date").sort_values("date")
    q = q[q.date <= pd.Timestamp(END)].set_index("date")
    if naive:
        q = q[q.close > 0]
        r = q.close.pct_change()
        info = "qfq_naive"
    else:
        rp = RAW / f"{code}.csv"
        if not rp.exists():
            return None, "无不复权价"
        raw = pd.read_csv(rp, parse_dates=["date"]).drop_duplicates("date").set_index("date").close
        df = pd.concat({"q": q.close, "r": raw}, axis=1, sort=True).dropna()
        df = df[df.r > 0]
        gap = df.index.to_series().diff().dt.days
        if (gap > MAX_GAP_DAYS).any():
            cut = gap[gap > MAX_GAP_DAYS].index.max()
            df = df[df.index >= cut]
            gap_info = f";gap_cut={cut.date()}"
        else:
            gap_info = ""
        # qfq = 拆分/折算按比例 + 分红按减法。按拆分日（不复权单日变动>30%）分段，
        # 段内比例 k = median(Δqfq/Δraw)，总收益 r_t = Δqfq_t / (k * raw_{t-1})；拆分日用 qfq pct。
        rr = df.r.pct_change()
        jump = (rr.abs() > SPLIT_JUMP).fillna(False)
        seg = jump.cumsum()
        dq, dr = df.q.diff(), df.r.diff()
        x = (dq / dr).where((dr.abs() > 0) & ~jump)
        k = x.groupby(seg).transform("median")
        r = dq / (k * df.r.shift(1))
        qpct = df.q.pct_change()
        r[jump] = qpct[jump]
        q = q.loc[df.index]
        kseg = k.groupby(seg).first()
        info = f"tr(jump_days={int(jump.sum())};k_segs={','.join(f'{v:.3g}' for v in kseg.dropna())}){gap_info}"
    r = r.fillna(0.0)
    bad = ~np.isfinite(r) | (r <= -0.95)
    if bad.any():
        # 无法计算的日子：从最后一个异常日之后开始
        last_bad = r.index[bad.to_numpy()].max()
        r = r[r.index > last_bad]
        q = q.loc[r.index]
        info += f";trunc_after={last_bad.date()}"
    close = (1 + r).cumprod()
    out = pd.DataFrame({"date": close.index, "close": close.to_numpy(),
                        "amount": q["amount"].reindex(close.index).to_numpy(),
                        "volume": q["volume"].reindex(close.index).to_numpy()})
    return out.reset_index(drop=True), info


def idx_series(code: str):
    p = IDX / f"{code}.csv"
    if not p.exists():
        return None
    d = pd.read_csv(p, parse_dates=["date"]).drop_duplicates("date").sort_values("date")
    d = d[(d.close > 0) & (d.date <= pd.Timestamp(END))].reset_index(drop=True)
    if "amount" not in d.columns:
        d["amount"] = np.nan
    return d[["date", "close", "amount", "volume"]]


def eval_name(df, slow):
    F = L.slim_factors(df)
    pos = {"A": L.policy_baseline(F)[0], "B": L.policy_layered(F, slow=slow)[0],
           "C": CV.policy_C(F, slow)}
    sw = CV.switch_D(F)
    pos["D"] = sw
    pos["E"] = CV.policy_E(F, sw)[0]
    pos["F"] = CV.policy_F(F)
    ret = F["ret"]
    dates = ret.index
    ev = np.arange(len(dates)) >= WARMUP
    nets = {}
    for arm, p in pos.items():
        net, ps = run_pos(p, ret, cost=COST)
        nets[arm] = (net, ps)
    live = {f"pos_{a}": float(p.iloc[-1]) for a, p in pos.items()}
    return F, ret, dates, ev, nets, live


def block(nets, ret, mask):
    """对一个窗口给出 BH 和各臂指标。"""
    if mask.sum() < MIN_STATS:
        return None
    out = {}
    b = stats(ret[mask].fillna(0.0))
    out["BH"] = dict(cagr=b["cagr"], mdd=b["mdd"], calmar=b["calmar"], avg_pos=1.0, turn=0.0)
    for arm, (net, ps) in nets.items():
        m = L.metrics_block(net, ret, ps, mask)
        out[arm] = dict(cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"], avg_pos=m["avg_pos"],
                        turn=m["turn"], d_calmar_fixed=m["d_calmar_fixed"])
    return out


def run_panel(names, panel, rows, yrows, live, naive_rows=None):
    for nm in names:
        df = nm["df"]
        F, ret, dates, ev, nets, lv = eval_name(df, nm["lane"] == "slow")
        d = pd.Series(dates)
        base = dict(panel=panel, code=nm["code"], name=nm["name"], sector=nm["sector"],
                    category=nm.get("category", ""), focus=nm.get("focus", ""), lane=nm["lane"],
                    ret_type=nm.get("ret_type", ""), first=str(dates[0].date()),
                    eval_start=str(dates[WARMUP].date()), last=str(dates[-1].date()),
                    eval_n=int(ev.sum()), info=nm.get("info", ""))
        live.append(dict(base, date=str(dates[-1].date()), **lv))
        targets = rows if naive_rows is None else naive_rows
        periods = PERIODS if naive_rows is None else PERIODS[:1]
        for per, a, b in periods:
            m = ev.copy()
            if a:
                m &= ((d >= pd.Timestamp(a)) & (d <= pd.Timestamp(b))).to_numpy()
            blk = block(nets, ret, m)
            if blk is None:
                continue
            for arm, v in blk.items():
                targets.append(dict(base, period=per, period_n=int(m.sum()), arm=arm, **v))
        if naive_rows is not None:
            continue
        for y in YEARS:
            m = ev & (d.dt.year == y).to_numpy()
            blk = block(nets, ret, m)
            if blk is None:
                continue
            for arm, v in blk.items():
                yrows.append(dict(panel=panel, code=nm["code"], name=nm["name"], sector=nm["sector"],
                                  lane=nm["lane"], year=y, arm=arm, n=int(m.sum()),
                                  cagr=v["cagr"], mdd=v["mdd"], calmar=v["calmar"], avg_pos=v["avg_pos"]))


def add_deltas(res, keys):
    """按 (keys + code) 对齐，加 d vs BH / vs A。"""
    bh = res[res.arm == "BH"].set_index(keys + ["code"])[["cagr", "mdd", "calmar"]]
    aa = res[res.arm == "A"].set_index(keys + ["code"])[["cagr", "mdd", "calmar"]]
    r = res.set_index(keys + ["code"])
    for k in ("cagr", "mdd", "calmar"):
        r[f"d_{k}_vs_bh"] = r[k] - bh[k].reindex(r.index).to_numpy()
        r[f"d_{k}_vs_a"] = r[k] - aa[k].reindex(r.index).to_numpy()
    return r.reset_index()


def agg(g):
    o = dict(n=len(g), med_cagr=g.cagr.median(), med_mdd=g.mdd.median(), med_calmar=g.calmar.median(),
             med_avg_pos=g.avg_pos.median(), med_turn=g.turn.median() if "turn" in g else np.nan)
    for k in ("calmar", "cagr", "mdd"):
        for ref in ("bh", "a"):
            c = f"d_{k}_vs_{ref}"
            o[f"med_{c}"] = g[c].median()
            o[f"pos_{c}"] = float((g[c] > 0).mean())
    return pd.Series(o)


def summarize(res, by):
    return res.groupby(by + ["arm"]).apply(agg, include_groups=False).reset_index()


def P(x, s=False):
    return "NA" if pd.isna(x) else (f"{x*100:+.1f}%" if s else f"{x*100:.1f}%")


def N(x, s=False, nd=2):
    return "NA" if pd.isna(x) else (f"{x:+.{nd}f}" if s else f"{x:.{nd}f}")


def main():
    CV.self_check()
    uni = pd.read_csv(UNI)
    excl = []
    etf_names = []
    for _, row in uni.iterrows():
        code = row.tx_code
        if row.category == "货币":
            continue
        qp = QFQ_NEW / f"{code}.csv" if (QFQ_NEW / f"{code}.csv").exists() else QFQ_OLD / f"{code}.csv"
        if not qp.exists():
            excl.append(dict(panel="etf", code=code, name=row["name"], reason="无 qfq 缓存"))
            continue
        nrows = sum(1 for _ in open(qp)) - 1
        if nrows < WARMUP + MIN_EVAL:
            continue  # 历史不足，不进长面板（不单独记录）
        df, info = tr_series(code)
        if df is None:
            excl.append(dict(panel="etf", code=code, name=row["name"], reason=info))
            continue
        if len(df) < WARMUP + MIN_EVAL:
            excl.append(dict(panel="etf", code=code, name=row["name"], reason=f"清洗后评估窗<1250 ({info})"))
            continue
        amt = df.amount.tail(60).mean()
        if pd.isna(amt) or amt < L.MIN_AVG_AMOUNT:
            excl.append(dict(panel="etf", code=code, name=row["name"], reason="流动性不足(近60日均额<500万元)"))
            continue
        etf_names.append(dict(code=code, name=row["name"], sector=row.sector, category=row.category,
                              focus=row.focus if isinstance(row.focus, str) else "",
                              lane="slow" if L.is_slow_group(row.category, row.sector) else "active",
                              ret_type="ETF比例复权总收益", info=info, df=df))
    log(f"长 ETF 面板 {len(etf_names)} 只")

    meta = pd.read_csv(RUN / "index_meta.csv")
    idx_names = []
    for r in meta.itertuples():
        if r.status != "ok":
            excl.append(dict(panel="index", code=r.code, name=r.name, reason=f"拉取失败 {r.err}"))
            continue
        df = idx_series(r.code)
        if df is None or len(df) < WARMUP + MIN_EVAL:
            excl.append(dict(panel="index", code=r.code, name=r.name, reason="评估窗<1250"))
            continue
        idx_names.append(dict(code=r.code, name=r.name, sector=r.sector, category="指数", focus="",
                              lane=r.lane, ret_type=r.ret_type, info=r.source, df=df))
    log(f"指数代理 {len(idx_names)} 只")

    rows, yrows, live, naive = [], [], [], []
    run_panel(etf_names, "etf", rows, yrows, live)
    run_panel(idx_names, "index", rows, yrows, live)
    # 敏感性：同一批 ETF 用旧管线 qfq pct_change
    naive_names = []
    for nm in etf_names:
        df, info = tr_series(nm["code"], naive=True)
        if df is not None and len(df) >= WARMUP + MIN_EVAL:
            naive_names.append(dict(nm, df=df, info=info))
    run_panel(naive_names, "etf_qfqnaive", rows, yrows, [], naive_rows=naive)

    res = add_deltas(pd.DataFrame(rows), ["panel", "period"])
    res.to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig")
    nv = add_deltas(pd.DataFrame(naive), ["panel", "period"])
    nv.to_csv(RUN / "sensitivity_qfq_naive.csv", index=False, encoding="utf-8-sig")
    yr = add_deltas(pd.DataFrame(yrows).assign(turn=np.nan), ["panel", "year"])
    yr.to_csv(RUN / "by_year_names.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(excl).to_csv(RUN / "exclusions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(live).to_csv(RUN / "live_signal.csv", index=False, encoding="utf-8-sig")

    bp = summarize(res, ["panel", "period"])
    bp.to_csv(RUN / "by_period.csv", index=False, encoding="utf-8-sig")
    by = summarize(yr, ["panel", "year"])
    by.to_csv(RUN / "by_year.csv", index=False, encoding="utf-8-sig")
    bl = summarize(res[res.panel == "etf"], ["period", "lane"])
    bl.to_csv(RUN / "by_lane_etf.csv", index=False, encoding="utf-8-sig")
    bs = summarize(res[res.panel == "etf"], ["period", "sector"])
    bs.to_csv(RUN / "by_sector_etf.csv", index=False, encoding="utf-8-sig")
    nvs = summarize(nv, ["panel", "period"])
    nvs.to_csv(RUN / "sensitivity_qfq_naive_summary.csv", index=False, encoding="utf-8-sig")

    # index theme table
    it = res[res.panel == "index"].pivot_table(index=["code", "name", "sector", "lane", "ret_type", "period"],
                                               columns="arm", values=["cagr", "mdd", "calmar"]).reset_index()
    it.columns = ["_".join([c for c in col if c]) for col in it.columns]
    it.to_csv(RUN / "index_theme_table.csv", index=False, encoding="utf-8-sig")
    # ETF focus table
    foc = res[(res.panel == "etf") & (res.focus.fillna("") != "")]
    foc.to_csv(RUN / "focus_results.csv", index=False, encoding="utf-8-sig")

    # best arm per period
    best = []
    for (pn, per), g in bp.groupby(["panel", "period"]):
        g = g.sort_values("med_calmar", ascending=False)
        best.append(dict(panel=pn, period=per, n=int(g.n.max()), ranking=" > ".join(g.arm),
                         best=g.arm.iloc[0], best_calmar=g.med_calmar.iloc[0],
                         A_rank=int(list(g.arm).index("A")) + 1, C_rank=int(list(g.arm).index("C")) + 1))
    best = pd.DataFrame(best)
    best.to_csv(RUN / "best_arm_by_period.csv", index=False, encoding="utf-8-sig")

    # sector consistency (ETF full + per period, sectors n>=3) & index per-name consistency
    cons = []
    for per in [p[0] for p in PERIODS]:
        s = bs[(bs.period == per) & (bs.n >= 3) & (bs.arm.isin(["B", "C", "D", "E", "F"]))]
        for arm, g in s.groupby("arm"):
            cons.append(dict(panel="etf板块", period=per, arm=arm, n_groups=len(g),
                             frac_dcalmar_vs_a_pos=float((g.med_d_calmar_vs_a > 0).mean()),
                             frac_dcagr_vs_a_pos=float((g.med_d_cagr_vs_a > 0).mean())))
        s = res[(res.panel == "index") & (res.period == per) & (res.arm.isin(["B", "C", "D", "E", "F"]))]
        for arm, g in s.groupby("arm"):
            cons.append(dict(panel="指数逐只", period=per, arm=arm, n_groups=len(g),
                             frac_dcalmar_vs_a_pos=float((g.d_calmar_vs_a > 0).mean()),
                             frac_dcagr_vs_a_pos=float((g.d_cagr_vs_a > 0).mean())))
    cons = pd.DataFrame(cons)
    cons.to_csv(RUN / "consistency.csv", index=False, encoding="utf-8-sig")

    snap = dict(end=END, warmup=WARMUP, min_eval=MIN_EVAL, cost_one_way=COST, delay=1,
                etf_n=len(etf_names), index_n=len(idx_names),
                etf_earliest_first=min(n["df"].date.iloc[0] for n in etf_names).strftime("%Y-%m-%d"),
                etf_earliest_eval=min(n["df"].date.iloc[WARMUP] for n in etf_names).strftime("%Y-%m-%d"),
                index_meta=meta[["code", "name", "ret_type", "status", "start", "end"]].to_dict("records"),
                caches=dict(qfq_new=str(QFQ_NEW), qfq_old=str(QFQ_OLD), raw=str(RAW), index=str(IDX)),
                return_method="ETF: 拆分日分段, k=median(Δqfq/Δraw), r=Δqfq/(k·raw_{t-1}); 拆分日用 qfq pct",
                git_commit=L.git_commit(), prereg="runs/2026-11-14_long_history/PREREG.md",
                script="work/long_history_backtest.py")
    (RUN / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    shutil.copy2(Path(__file__), RUN / "long_history_backtest.py")
    log("完成")


if __name__ == "__main__":
    main()
