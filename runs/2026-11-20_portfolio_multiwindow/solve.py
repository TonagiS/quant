# -*- coding: utf-8 -*-
"""把"宽池上打不过等权"拆成 5 个可检验的问题，逐个用数据回答。

Q1 基准红利：等权 92 只"每日再平衡"比"买入后漂移"多赚多少？
Q2 信号有没有信息：随机选 5 只（300 次）的分布 vs 动量选 5 只，S2 落在哪个分位？
Q3 是不是池子太宽：按成交额取前 20/40/60/92 只，S2 年化怎么变？
Q4 是不是太集中：k=5/10/20 的年化与回撤？
Q5 VT15 到底有没有 timing 信息：对比"恒定 0.76 仓"的 S2。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = r"E:\Code\quant"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "runs", "2026-11-20_portfolio_multiwindow"))
from position.engine import Config, Fixed, VolTarget, features, perf, run_with_base  # noqa: E402
from bt import clean, load_etf_panel  # noqa: E402

COST = 0.0005
WIN = pd.Timestamp("2017-01-01")


def load_amount() -> pd.DataFrame:
    d = os.path.join(ROOT, "data", "cache_ohlc_fullhist")
    cols = {}
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".csv"):
            df = pd.read_csv(os.path.join(d, fn), parse_dates=["date"], usecols=["date", "amount"])
            cols[fn[:-4]] = df.drop_duplicates("date").set_index("date")["amount"].astype(float)
    return pd.DataFrame(cols).sort_index()


def cagr(r):
    r = np.asarray(r, float)
    return float(np.prod(1 + r) ** (252.0 / len(r)) - 1)


def mdd(r):
    eq = np.cumprod(1 + np.asarray(r, float))
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def rand_port(ret, vol, mask, rm, k, reb, cost, seed, top=False):
    """在候选掩码 mask 内选 k 只、逆波动加权、reb 天调仓、delay=1、单边 cost。

    top=True 时按 rm 取前 k（用于校验模拟器能否复现 S2），否则随机抽。
    """
    rng = np.random.default_rng(seed)
    T, N = ret.shape
    w = np.zeros(N)
    tw_pending = None
    out = np.zeros(T)
    for i in range(T):
        gross = float((w * ret[i]).sum())
        c = 0.0
        if tw_pending is not None:
            d = np.abs(tw_pending - w).sum()
            c = cost * d
            w = tw_pending
            tw_pending = None
        out[i] = gross - c
        if i >= 150 and i % reb == 0:
            idx = np.flatnonzero(mask[i] & ~np.isnan(rm[i]))
            if len(idx) >= k:
                pick = idx[np.argsort(-rm[i, idx])[:k]] if top else rng.choice(idx, k, replace=False)
                iv = 1.0 / np.maximum(vol[i, pick], 1e-6)
                tw = np.zeros(N)
                tw[pick] = iv / iv.sum()
                tw_pending = tw
    return out


def drift_equal(close, lo):
    """等权买入后不管（不再平衡）——只要还活着的标的。"""
    px = close.iloc[lo:].ffill()
    px = px.dropna(axis=1)
    w = px.iloc[0] * 0 + 1.0 / px.shape[1]
    val = (px / px.iloc[0])
    port = (val * w.values).sum(axis=1)
    return port.pct_change().fillna(0.0).to_numpy()[1:]


def main() -> None:
    close, _ = clean(load_etf_panel())
    f = features(close, min_names=8)
    dates = close.index
    lo = int(dates.searchsorted(WIN))
    ret = np.nan_to_num(f["ret"].to_numpy())
    valid = f["valid"].to_numpy()
    vol = np.nan_to_num(f["vol20"].to_numpy(), nan=np.inf)
    common = dict(k=5, weight_mode="invvol", buy_cost=COST, sell_cost=COST, min_names=8)

    s2 = run_with_base(close, f, Config(reb=10, reb_mode="rolling", rule=Fixed(1.0), **common))
    s2e = run_with_base(close, f, Config(k=5, reb=10, reb_mode="rolling", weight_mode="equal",
                                         buy_cost=COST, sell_cost=COST, min_names=8, rule=Fixed(1.0)))
    vt = run_with_base(close, f, Config(reb=10, reb_mode="rolling", rule=VolTarget(0.15, 60, every=1), **common))
    bh_ret = np.nan_to_num(f["ret"].mean(axis=1).fillna(0.0).to_numpy())

    print("窗口 %s → %s（%.1f 年），池 %d 只" % (dates[lo].date(), dates[-1].date(), (len(dates) - lo) / 252, close.shape[1]))

    # Q1 基准红利
    dr = drift_equal(close, lo)
    print("\nQ1 基准：每日再平衡 年化 %.1f%% / MDD %.0f%%；买入后漂移 年化 %.1f%% / MDD %.0f%%（差 %.1fpp/年）"
          % (cagr(bh_ret[lo:]) * 100, mdd(bh_ret[lo:]) * 100, cagr(dr) * 100, mdd(dr) * 100,
             (cagr(bh_ret[lo:]) - cagr(dr)) * 100))

    # Q2 随机 5 只的分布（同一候选集 cand，只把"按动量排序"换成"随机抽"）
    cand = f["cand"].to_numpy()
    rm = f["rm"].to_numpy()
    chk = rand_port(ret, vol, cand, rm, 5, 10, COST, 0, top=True)[lo:]
    print("\nQ2 模拟器校验：强制选动量前 5 → 年化 %.1f%% MDD %.0f%%（engine S2 为 %.1f%% / %.0f%%）"
          % (cagr(chk) * 100, mdd(chk) * 100, cagr(s2["ret"][lo:]) * 100, mdd(s2["ret"][lo:]) * 100))
    vals = []
    for sd in range(300):
        vals.append(cagr(rand_port(ret, vol, cand, rm, 5, 10, COST, sd)[lo:]))
    vals = np.array(vals)
    s = cagr(s2["ret"][lo:])
    pct = float((vals < s).mean() * 100)
    print("   随机5只(300次)：年化 均值 %.1f%% / 中位 %.1f%% / 5-95%% [%.1f%%, %.1f%%]"
          % (vals.mean() * 100, np.median(vals) * 100, np.percentile(vals, 5) * 100, np.percentile(vals, 95) * 100))
    print("   等权基准 %.1f%% | 动量S2 %.1f%%（逆波动）| S2等权权重 %.1f%% → 动量在随机分布第 %.0f 百分位"
          % (cagr(bh_ret[lo:]) * 100, s * 100, cagr(s2e["ret"][lo:]) * 100, pct))

    # Q2b 零成本对照：把"随机选出的标的天天换"的换手惩罚拿掉，只看排序有没有信息
    s2nc = run_with_base(close, f, Config(reb=10, reb_mode="rolling", rule=Fixed(1.0),
                                          buy_cost=0.0, sell_cost=0.0, **{k2: v for k2, v in common.items() if "cost" not in k2}))
    v0 = np.array([cagr(rand_port(ret, vol, cand, rm, 5, 10, 0.0, sd)[lo:]) for sd in range(300)])
    s_nc = cagr(s2nc["ret"][lo:])
    print("   零成本：随机5只 中位 %.1f%% / 5-95%% [%.1f%%, %.1f%%] | 动量S2 %.1f%% → 第 %.0f 百分位"
          % (np.median(v0) * 100, np.percentile(v0, 5) * 100, np.percentile(v0, 95) * 100,
             s_nc * 100, float((v0 < s_nc).mean() * 100)))

    # Q3 池宽扫描（按 2017-2021 日均成交额排序）
    amt = load_amount().reindex(columns=close.columns).loc["2017-01-01":"2021-12-31"].mean()
    order = amt.sort_values(ascending=False).dropna()
    print("\nQ3 池宽（按成交额前 N 只）")
    for n in (20, 40, 60, 92):
        sub = close[order.index[:n]]
        fn = features(sub, min_names=8)
        rr = run_with_base(sub, fn, Config(reb=10, reb_mode="rolling", rule=Fixed(1.0), **common))
        l2 = int(sub.index.searchsorted(WIN))
        bh2 = np.nan_to_num(fn["ret"].mean(axis=1).fillna(0.0).to_numpy())
        print("   前%2d 只：S2 年化 %.1f%% MDD %.0f%% | 等权 %.1f%% MDD %.0f%% | 差 %+.1fpp"
              % (n, cagr(rr["ret"][l2:]) * 100, mdd(rr["ret"][l2:]) * 100,
                 cagr(bh2[l2:]) * 100, mdd(bh2[l2:]) * 100,
                 (cagr(rr["ret"][l2:]) - cagr(bh2[l2:])) * 100))

    # Q4 k 扫描
    print("\nQ4 持仓只数（92 只全池）")
    for k in (5, 10, 20):
        rr = run_with_base(close, f, Config(k=k, reb=10, reb_mode="rolling", weight_mode="invvol",
                                            buy_cost=COST, sell_cost=COST, min_names=8, rule=Fixed(1.0)))
        print("   k=%2d：年化 %.1f%% MDD %.0f%%" % (k, cagr(rr["ret"][lo:]) * 100, mdd(rr["ret"][lo:]) * 100))

    # Q5 VT15 的 timing 是否有信息
    ex = vt["expo"][lo:]
    const = 0.76 * s2["ret"][lo:]
    print("\nQ5 敞口择时：VT15 年化 %.1f%% MDD %.0f%% | 恒定 %.0f%% 仓的 S2 年化 %.1f%% MDD %.0f%%"
          % (cagr(vt["ret"][lo:]) * 100, mdd(vt["ret"][lo:]) * 100, ex.mean() * 100,
             cagr(const) * 100, mdd(const) * 100))
    r_s2 = np.asarray(s2["ret"][lo:], float)
    fwd = pd.Series(r_s2).rolling(20).mean().shift(-20).to_numpy()   # 未来 20 日均收益
    ok = np.isfinite(fwd)
    corr = float(np.corrcoef(ex[ok], fwd[ok])[0, 1])
    print("   敞口 vs 未来20日均收益 相关系数 %+.3f（>0 才算择时有信息）" % corr)


if __name__ == "__main__":
    main()
