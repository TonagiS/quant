#!/usr/bin/env python3
"""红利-科技跷跷板回测。口径见 runs/2026-11-15_div_tech_seesaw/PREREG.md（冻结于 commit 2778e79）。"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402
import layered_v1_backtest as L  # noqa: E402
import long_history_backtest as LH  # noqa: E402
import fastcap_ma_sens as MS  # noqa: E402

ROOT = AB.ROOT
RUN = ROOT / "runs" / "2026-11-15_div_tech_seesaw"
DT = ROOT / "data" / "cache_div_tech"
IDX = ROOT / "data" / "cache_index_proxy"
COST = 0.0005
WIN, HI, LO, NCONF = 750, 0.8, 0.2, 2
WARM = 250
TD = 252
SUB = [("full", None, None), ("2015", "2015-01-01", "2015-12-31"), ("2018", "2018-01-01", "2018-12-31"),
       ("2021-2022", "2021-01-01", "2022-12-31"), ("2023-now", "2023-01-01", "2026-12-31")]

TARGETS = [  # code, name, kind, short
    ("399606", "创业板指全收益", "index", False),
    ("H00998", "中证TMT全收益", "index", False),
    ("H30184", "中证半导体(价格)", "index", False),
    ("000688", "科创50(价格)", "index", True),
    ("sz159915", "创业板ETF", "etf", False),
    ("sz159939", "信息技术ETF", "etf", False),
    ("sh512480", "半导体ETF", "etf", True),
    ("sh515000", "科技ETF", "etf", True),
    ("sh515880", "通信ETF", "etf", True),
    ("sh515050", "5G通信ETF", "etf", True),
    ("sh588000", "科创50ETF", "etf", True),
]


def read_close(path):
    d = pd.read_csv(path, parse_dates=["date"]).drop_duplicates("date").sort_values("date")
    d = d[d.close > 0]
    return d.set_index("date").close.astype(float)


def series(code):
    if code == "399606": return read_close(DT / "sz399606.csv")
    if code == "399006": return read_close(DT / "sz399006.csv")
    if code in ("H00998", "000688", "000922", "H11001", "000998"): return read_close(DT / f"{code}.csv")
    if code in ("H00922", "H30184"): return read_close(IDX / f"{code}.csv")
    df, info = AB.tr_series_any(code)
    return df.set_index("date").close.astype(float)


def month_ends(idx):
    s = pd.Series(idx, index=idx)
    return set(s.groupby([idx.year, idx.month]).max())


def dividend_state(close):
    """返回 (日频 state 序列[当日收盘时已知的状态], 月末表 DataFrame)。"""
    mn = close.rolling(WIN, min_periods=WIN).min()
    mx = close.rolling(WIN, min_periods=WIN).max()
    pos = (close - mn) / (mx - mn)
    me = sorted(d for d in month_ends(close.index) if pd.notna(pos.get(d)))
    state, hi_run, lo_run, rec = "NEUTRAL", 0, 0, []
    for d in me:
        p = pos[d]
        hi_run = hi_run + 1 if p >= HI else 0
        lo_run = lo_run + 1 if p <= LO else 0
        if hi_run >= NCONF: state = "HIGH"
        elif lo_run >= NCONF: state = "LOW"
        rec.append(dict(date=d, pos=p, cand="H" if p >= HI else ("L" if p <= LO else ""), state=state))
    tab = pd.DataFrame(rec).set_index("date")
    daily = tab.state.reindex(close.index).ffill()
    return daily, tab, pos


def weights_for(state, arm):
    # 资产顺序：tech, div, bond
    if arm == "SEESAW" or arm == "RATIO":
        return {"LOW": (1, 0, 0), "HIGH": (0, 1, 0), "NEUTRAL": (.5, .5, 0)}[state]
    if arm == "SEESAW-BOND":
        return {"LOW": (1, 0, 0), "HIGH": (0, 0, 1), "NEUTRAL": (.5, .5, 0)}[state]
    if arm == "REVERSE":
        return {"HIGH": (1, 0, 0), "LOW": (0, 1, 0), "NEUTRAL": (.5, .5, 0)}[state]
    raise KeyError(arm)


def simulate(R, decide):
    """R: DataFrame[tech,div,bond] 日收益（评估窗口内）。decide: dict date->目标权重（该日收盘出信号，下一日收盘成交）。
    返回 (net 日收益, 科技权重序列, 调仓次数)。"""
    dates = R.index
    first = decide.get(dates[0])
    h = np.array(first if first is not None else (.5, .5, 0), float)
    pending = None
    net, wt, ntr = [], [], 0
    Rv = R.to_numpy()
    for i, d in enumerate(dates):
        if i > 0:
            g = h * (1 + Rv[i])
            tot = g.sum(); r = tot - 1; h = g / tot
            if pending is not None:
                tgt = np.array(pending, float); turn = np.abs(tgt - h).sum()
                if turn > 1e-9:
                    r = (1 + r) * (1 - COST * turn) - 1; ntr += 1
                h = tgt; pending = None
            net.append(r)
        else:
            net.append(0.0)
        wt.append(h[0])
        if d in decide:
            pending = decide[d]
    return pd.Series(net, index=dates), pd.Series(wt, index=dates), ntr


def stats(net):
    nav = (1 + net).cumprod(); n = len(net)
    if n < 60: return dict(cagr=np.nan, vol=np.nan, mdd=np.nan, calmar=np.nan)
    cagr = nav.iloc[-1] ** (TD / n) - 1
    mdd = (nav / nav.cummax() - 1).min()
    return dict(cagr=cagr, vol=net.std() * np.sqrt(TD), mdd=mdd, calmar=cagr / abs(mdd) if mdd < 0 else np.nan)


def state_decisions(daily_state, dates, arm, rebal_neutral=True):
    me = month_ends(dates)
    dec, prev = {}, None
    for d in dates:
        if d not in me: continue
        s = daily_state.get(d)
        if pd.isna(s): s = "NEUTRAL"
        if s != prev or (s == "NEUTRAL" and rebal_neutral):
            dec[d] = weights_for(s, arm)
        prev = s
    return dec


def run_target(code, name, kind, short, div, bond, dstate, label="main", extra_ratio=True):
    tech = series(code)
    common = tech.index.intersection(div.index).intersection(bond.index)
    tech, dv, bd = tech.reindex(common), div.reindex(common), bond.reindex(common)
    R_all = pd.DataFrame({"tech": tech.pct_change(), "div": dv.pct_change(), "bond": bd.pct_change()}).fillna(0.0)
    ds = dstate.reindex(common)  # 用红利自身交易日算出的状态，按日期对齐
    sig_ok = ds.first_valid_index()
    start = max(sig_ok, common[min(WARM, len(common) - 1)])
    R = R_all.loc[start:]
    dates = R.index
    if len(dates) < 120:
        return [], {}
    nets, wts, info = {}, {}, {}
    for arm in ["SEESAW", "SEESAW-BOND", "REVERSE"]:
        dec = state_decisions(ds, dates, arm)
        dec = {d: w for d, w in dec.items()}
        # 起点权重 = 起点当天已知状态
        s0 = ds.loc[:dates[0]].iloc[-1]
        dec0 = dict(dec); dec0.setdefault(dates[0], weights_for(s0 if isinstance(s0, str) else "NEUTRAL", arm))
        net, wt, ntr = simulate(R, dec0)
        nets[arm], wts[arm] = net, wt
        st = ds.loc[dates].fillna("NEUTRAL")
        info[arm] = dict(n_rebal=ntr, n_switch=int((st != st.shift()).iloc[1:].sum()))
    # 50/50 月度
    me = month_ends(dates)
    dec = {d: (.5, .5, 0) for d in dates if d in me}; dec[dates[0]] = (.5, .5, 0)
    nets["50/50"], wts["50/50"], ntr = simulate(R, dec); info["50/50"] = dict(n_rebal=ntr, n_switch=0)
    nets["BH-tech"], wts["BH-tech"] = R.tech.copy(), pd.Series(1.0, index=dates); nets["BH-tech"].iloc[0] = 0
    nets["BH-div"], wts["BH-div"] = R["div"].copy(), pd.Series(0.0, index=dates); nets["BH-div"].iloc[0] = 0
    # A：仓库单 ETF 规则（科技序列，余现金），额外 shift(1) => t+1 收盘成交
    dfA = pd.DataFrame({"date": common, "close": tech.to_numpy(), "amount": np.nan, "volume": np.nan})
    F = L.slim_factors(dfA)
    posA = MS.arms_positions(F)[0]["A"]; posA.index = common
    p = posA.shift(2).fillna(0.0)  # t 收盘信号 -> t+1 收盘成交 -> 吃 t+2 收益
    netA = p * R_all.tech - p.diff().abs().fillna(0) * COST
    nets["A"], wts["A"] = netA.loc[dates], p.loc[dates]; nets["A"].iloc[0] = 0
    # 描述性 RATIO
    if extra_ratio:
        ratio = (dv / tech)
        rs, _, _ = dividend_state(ratio)
        if rs.first_valid_index() is not None and rs.first_valid_index() <= dates[-120]:
            rstart = max(rs.first_valid_index(), dates[0])
            Rr = R.loc[rstart:]
            decr = state_decisions(rs, Rr.index, "RATIO")
            s0 = rs.loc[:Rr.index[0]].iloc[-1]
            decr.setdefault(Rr.index[0], weights_for(s0 if isinstance(s0, str) else "NEUTRAL", "RATIO"))
            n_, w_, t_ = simulate(Rr, decr)
            nets["RATIO"], wts["RATIO"] = n_.reindex(dates), w_.reindex(dates)
            st = rs.loc[Rr.index].fillna("NEUTRAL")
            info["RATIO"] = dict(n_rebal=t_, n_switch=int((st != st.shift()).iloc[1:].sum()), ratio_start=str(rstart.date()))
    rows = []
    for w, a, b in SUB:
        m = pd.Series(True, index=dates)
        if a: m = (dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b)); m = pd.Series(m, index=dates)
        if m.sum() < 60: continue
        for arm, net in nets.items():
            nn = net[m].dropna()
            if len(nn) < 60: continue
            s = stats(nn)
            row = dict(label=label, code=code, name=name, kind=kind, short=short, window=w,
                       start=str(nn.index[0].date()), end=str(nn.index[-1].date()), n=len(nn), arm=arm, **s,
                       avg_tech_w=float(wts[arm][m].dropna().mean()))
            if w == "full": row.update(info.get(arm, {}))
            if arm in ("SEESAW", "SEESAW-BOND", "REVERSE", "RATIO", "A"):
                k = row["avg_tech_w"]
                sc = stats(k * R.tech[m] + (1 - k) * R["div"][m]) if arm != "A" else stats(k * R.tech[m])
                row.update(static_cagr=sc["cagr"], static_mdd=sc["mdd"], static_calmar=sc["calmar"])
            rows.append(row)
    navs = {arm: (1 + n.fillna(0)).cumprod() for arm, n in nets.items()}
    return rows, navs


def yearly_hilo(close, name):
    out = []
    for y, g in close.groupby(close.index.year):
        prev = close[close.index.year < y]
        base = prev.iloc[-1] if len(prev) else g.iloc[0]
        out.append(dict(series=name, year=y, high=g.max(), high_date=str(g.idxmax().date()), low=g.min(),
                        low_date=str(g.idxmin().date()), year_ret=g.iloc[-1] / base - 1, partial=bool(y in (2004, 2026))))
    return pd.DataFrame(out)


def episodes(tab, close_idx, name):
    rows, cur, st = [], None, None
    st_series = tab.state
    for d, s in st_series.items():
        if s != cur:
            if cur is not None: rows[-1]["end_signal"] = str(prev.date())
            nxt = close_idx[close_idx > d]
            rows.append(dict(series=name, state=s, confirm_date=str(d.date()), trade_date=str(nxt[0].date()) if len(nxt) else ""))
            cur = s
        prev = d
    rows[-1]["end_signal"] = str(prev.date()) + "(至今)"
    df = pd.DataFrame(rows)
    df["months"] = [int(((pd.Timestamp(e[:10]) - pd.Timestamp(c)).days) / 30.44) for c, e in zip(df.confirm_date, df.end_signal)]
    return df


def main():
    div = series("H00922"); bond = series("H11001")
    dstate, dtab, dpos = dividend_state(div)
    dtab.to_csv(RUN / "dividend_monthly_state.csv", encoding="utf-8-sig")
    allrows, allnavs = [], {}
    for code, name, kind, short in TARGETS:
        rows, navs = run_target(code, name, kind, short, div, bond, dstate)
        allrows += rows; allnavs[code] = navs
        print(code, len(rows), flush=True)
    # 校验 1：510880 作信号+红利腿
    e = series("sh510880"); es, etab, _ = dividend_state(e)
    for code, name, kind, short in TARGETS:
        rows, _ = run_target(code, name, kind, short, e, bond, es, label="chk_510880", extra_ratio=False)
        allrows += rows
    # 校验 2：000922 价格作信号，红利腿仍为 H00922
    p = series("000922"); ps, ptab, _ = dividend_state(p)
    for code, name, kind, short in TARGETS:
        rows, _ = run_target(code, name, kind, short, div, bond, ps, label="chk_sig000922", extra_ratio=False)
        allrows += rows
    res = pd.DataFrame(allrows)
    res.to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    ep = pd.concat([episodes(dtab, div.index, "H00922(主)"), episodes(ptab, p.index, "000922价格(敏感性)"),
                    episodes(etab, e.index, "510880(校验)")])
    ep.to_csv(RUN / "state_episodes.csv", index=False, encoding="utf-8-sig")
    hl = pd.concat([yearly_hilo(p, "000922价格"), yearly_hilo(div, "H00922全收益")])
    hl.to_csv(RUN / "yearly_high_low_DESCRIPTIVE_hindsight.csv", index=False, encoding="utf-8-sig", float_format="%.6g")
    pd.to_pickle(dict(navs=allnavs, dpos=dpos, dstate=dstate, div=div), RUN / "_navs.pkl")


if __name__ == "__main__":
    main()
