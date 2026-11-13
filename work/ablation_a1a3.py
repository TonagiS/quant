"""A1 + A3：组合层的两件遗留事。

A1  D2 估值闸门放到 2007 长样本上（含 2007/2015 两次泡沫），对比 BASE / D1(年线) / D2(PE 分位)
    样本：cache_ohlc 的指数 + 蓝筹，2007-01 起；dev <= 2015-12-31，留出 2016+
A3  T1（20 日新高进 / 破 MA20 出）改用"风险归一"判据重看：
    ann、calmar、**每单位平均敞口的年化（ann/avg_expo）**、等波动年化（ann×0.15/vol）
    样本：cache_ohlc 全部 ETF，2019-01 起
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import TD, load_universe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / os.environ.get("SA_OUT_NAME", "2026-11-16_a1a3")
OUT.mkdir(parents=True, exist_ok=True)
COST = 0.0005


def metrics(net: pd.Series, expo: pd.Series | None = None) -> dict:
    net = net.dropna()
    if len(net) < 60:
        return {}
    eq = (1 + net).cumprod()
    yrs = len(net) / TD
    ann = eq.iloc[-1] ** (1 / yrs) - 1
    mdd = (eq / eq.cummax() - 1).min()
    vol = net.std() * np.sqrt(TD)
    m = {"ann": ann, "mdd": mdd, "calmar": ann / abs(mdd) if mdd else np.nan,
         "sharpe": net.mean() / net.std() * np.sqrt(TD) if net.std() else np.nan,
         "vol": vol}
    if expo is not None:
        ae = float(np.nanmean(expo))
        m["avg_expo"] = ae
        m["ann_per_expo"] = ann / ae if ae > 0 else np.nan
        m["ann_eqvol"] = ann * (0.15 / vol) if vol > 0 else np.nan
    return m


def vt(pnl_ret: pd.Series, target=0.15, window=60, every=5) -> pd.Series:
    vol = pnl_ret.rolling(window, min_periods=window // 2).std() * np.sqrt(TD)
    s = (target / vol).clip(0.0, 1.0)
    keep = pd.Series(False, index=s.index)
    keep.iloc[::every] = True
    s = s.where(keep).ffill().fillna(0.0)
    return s.shift(1).fillna(0.0)          # 信号在收盘算，次日执行


def ew(close: pd.DataFrame, min_n=5) -> pd.DataFrame:
    avail = close.notna()
    n = avail.sum(axis=1)
    w = avail.div(n.replace(0, np.nan), axis=0)
    w[n < min_n] = np.nan
    return w.fillna(0.0)


def run(w, ret, gate=None):
    """w/gate 都是收盘时点的信号，统一 shift(1) 次日执行。"""
    w = w.shift(1).fillna(0.0)
    pnl = (w * ret).fillna(0.0).sum(axis=1)
    expo = vt(pnl)
    if gate is not None:
        expo = expo * gate.shift(1).reindex(expo.index).ffill().fillna(1.0)
    expo = expo.clip(0, 1)
    net = pnl * expo
    weff = w.mul(expo, axis=0)
    turn = weff.diff().abs().sum(axis=1) / 2 + expo.diff().abs() * 0.5
    return net - COST * turn.fillna(0), expo


def load_panel(groups, start):
    uni = load_universe()
    cols = {}
    for code, (grp, df) in uni.items():
        if grp not in groups:
            continue
        s = df.set_index("date")["close"]
        s = s[s > 0]
        if len(s) < 400:
            continue
        cols[code] = s
    close = pd.DataFrame(cols).sort_index()
    return close.loc[start:].dropna(how="all")


def hs300_pe():
    p = ROOT / "data" / "cache_4pct" / "pe_沪深300.csv"
    df = pd.read_csv(p)
    df["日期"] = pd.to_datetime(df["日期"])
    return df.set_index("日期")["滚动市盈率"].astype(float).sort_index()


def gate_d1(close):
    hs = close.get("sh000300")
    if hs is None:
        return pd.Series(1.0, index=close.index)
    ma200 = hs.rolling(200, min_periods=200).mean()
    sig = (hs > ma200).astype(float)
    sig[ma200.isna()] = np.nan
    return sig.reindex(close.index).ffill().fillna(1.0).map({1.0: 1.0, 0.0: 0.5})


def gate_d2(close, min_periods=60, win=None):
    """PE 分位闸门。win=None → expanding；win=60 → 滚动 60 个月分位。"""
    pe = hs300_pe()
    m = pe.resample("ME").last().dropna()
    f = lambda a: (a[:-1] < a[-1]).mean()  # noqa: E731
    if win:
        pct = m.rolling(win, min_periods=min_periods).apply(f, raw=True)
    else:
        pct = m.expanding(min_periods=min_periods).apply(f, raw=True)
    sig = pd.Series(1.0, index=m.index)
    sig[pct > 0.80] = 0.5
    sig[pct > 0.90] = 0.2
    sig.iloc[:min_periods] = 1.0
    return sig.reindex(close.index, method="ffill").fillna(1.0)


def gate_t1(close):
    ma20 = close.rolling(20, min_periods=20).mean()
    hh = close.rolling(20, min_periods=20).max().shift(1)
    gate = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    c, m, h = close.to_numpy(), ma20.to_numpy(), hh.to_numpy()
    for j in range(close.shape[1]):
        hold = False
        col = np.zeros(len(c))
        for i in range(len(c)):
            if not np.isfinite(m[i, j]):
                col[i] = 0.0
                continue
            if hold and c[i, j] < m[i, j]:
                hold = False
            if (not hold) and np.isfinite(h[i, j]) and c[i, j] > h[i, j]:
                hold = True
            col[i] = 1.0 if hold else 0.0
        gate.iloc[:, j] = col
    return gate


def main():
    # ---------- A1：2007 长样本 D2 ----------
    print("== A1: 2007 长样本（指数+蓝筹）==")
    close = load_panel({"指数", "蓝筹"}, "2007-01-01")
    print(f"面板 {close.shape[0]} 日 × {close.shape[1]} 只 {close.index[0].date()}~{close.index[-1].date()}")
    ret = close.pct_change(fill_method=None)
    w = ew(close)
    g1 = gate_d1(close)
    g2 = gate_d2(close, 60)              # 原版：expanding min_periods=60（2007 泡沫前无分位）
    g2b = gate_d2(close, 24)             # 早期版：24 个月就能出分位
    g2c = gate_d2(close, 24, win=60)     # 滚动 5 年分位
    dev = close.index <= "2015-12-31"
    hold = ~dev
    rows = []
    for nm, g in [("BASE", None), ("D1 年线半仓", g1), ("D2 分位(mp60)", g2),
                  ("D2b 分位(mp24)", g2b), ("D2c 滚动5年分位", g2c),
                  ("D1+D2c", (g1 * g2c))]:
        net, expo = run(w, ret, g)
        for seg, mask in [("dev 2007-2015", dev), ("hold 2016+", hold), ("all", dev | hold)]:
            m = metrics(net[mask], expo[mask])
            if m:
                rows.append(dict(arm=nm, seg=seg, **m))
    a1 = pd.DataFrame(rows)
    a1.round(4).to_csv(OUT / "a1_d2_2007.csv", index=False, encoding="utf-8-sig")
    print(a1.round(3).to_string(index=False))

    # ---------- A3：T1 风险归一 ----------
    print(f"\n== A3: T1 等风险重评（ETF {os.environ.get('SA_A3_START', '2019-01-01')}+）==")
    close2 = load_panel({"ETF"}, os.environ.get("SA_A3_START", "2019-01-01"))
    print(f"面板 {close2.shape[0]} 日 × {close2.shape[1]} 只")
    ret2 = close2.pct_change(fill_method=None)
    w2 = ew(close2)
    t1 = gate_t1(close2)
    rows = []
    for nm, ww in [("BASE 等权", w2), ("T1 门控", w2 * t1)]:
        net, expo = run(ww, ret2, None)
        rows.append(dict(arm=nm, **metrics(net, expo)))
    a3 = pd.DataFrame(rows)
    a3.round(4).to_csv(OUT / "a3_t1_equalrisk.csv", index=False, encoding="utf-8-sig")
    print(a3.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
