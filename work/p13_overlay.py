"""P1 / P3：S2+VT15 之上叠"单支快线择时"与"VT×持仓质量"，按权重重算（与 P2 基线同口径）。

baseline = Config(reb=5, k=5, rule=VolTarget(0.15, 60, every=5)) —— 与 weekly_ma_ladder 基线一致（16.25%/0.984）
叠加臂：
  P1 质量Q        ：总敞口 × clip(0.5+0.5*持仓中"收盘>MA60"占比, 0.5, 1)
  P1 单支快线     ：每只 × G1(20日动量破位: 年线上行减半/否则清仓) × clip(0.15/vol60,0.4,1) × 趋势衰减
  P1 快线×质量Q   ：两者相乘
  P1 快线半仓版   ：(0.5+0.5*快线)
P3：k=2、单票上限 40%，同四臂。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
import position.engine as E  # noqa: E402

OUT = Path(r"E:\Code\quant\runs\2026-11-16_s2_overlays")
OUT.mkdir(parents=True, exist_ok=True)
TD, COST = 252, 0.0005


def fast_pos(close: pd.Series) -> pd.Series:
    """单支快线仓位（0/0.5/1）"""
    s = close.dropna()
    mom20 = s / s.shift(20) - 1
    ma200 = s.rolling(200, min_periods=150).mean()
    slope = ma200.pct_change(60)
    vol60 = s.pct_change().rolling(60, min_periods=40).std() * np.sqrt(TD)
    bull = ((s > ma200) & (slope > 0)).fillna(False)
    trig = (mom20 < 0).fillna(False)
    rec = (mom20 > 0.01).fillna(False)
    out = np.ones(len(s))
    st = 1.0
    t_, r_ = trig.to_numpy(), rec.to_numpy()
    b_ = bull.to_numpy()
    for i in range(len(s)):
        if t_[i]:
            st = 0.0
        elif r_[i]:
            st = 1.0
        out[i] = 1.0 if st > 0 else (0.5 if b_[i] else 0.0)
    volcap = (0.15 / vol60).clip(0.4, 1.0).fillna(1.0)
    above = (s > ma200).fillna(False)
    T = np.select([above & (slope > 0.02), above & (slope >= -0.02), above,
                   (~above) & (slope >= -0.02)], [0, 1, 2, 3], default=4)
    taper = pd.Series(T, index=s.index).map({0: 1.0, 1: 1.0, 2: 0.85, 3: 0.6, 4: 0.5}).fillna(1.0)
    return (pd.Series(out, index=s.index) * volcap * taper).clip(0, 1)


def build(k, cap, target=0.15):
    close = E.load_panel("ext")
    f = E.features(close)
    cfg = E.Config(reb=5, k=k, delay=1, cost=COST, cap=cap,
                   rule=E.VolTarget(target=target, window=60, every=5))
    res = E.simulate(close, f, cfg, keep_weights=True)
    return close, f, res


def sim(close, f, res, ov=None, q_mode=False, vt=True, gate=None):
    start = f["start"]
    dates = close.index
    ret = np.nan_to_num(f["ret"].to_numpy(), nan=0.0)
    wh = res["w_hist"]
    cols = list(close.columns)
    idx = {c: j for j, c in enumerate(cols)}
    ma60 = close.rolling(60, min_periods=40).mean()
    above = (close > ma60).fillna(False)
    T = len(close)
    port, turn_a, expo_a = np.zeros(T), np.zeros(T), np.zeros(T)
    prev = {}
    for i in range(start + 1, T):
        w = {cols[c]: v for c, v in wh[i - 1].items()}   # wh[i-1] 才是赚 ret[i] 的权重
        if q_mode and w:
            held = list(w)
            share = float(np.mean([above.iat[i - 1, idx[c]] for c in held]))
            q = min(1.0, max(0.5, 0.5 + 0.5 * share))
        else:
            q = 1.0
        new = {}
        for cname, v in w.items():
            vv = v * q * (ov[cname][i - 1] if ov is not None else 1.0)
            if gate is not None and cname not in prev and not gate[cname][i - 1]:
                vv = 0.0          # 门票：首次建仓必须通过趋势门槛
            if vv > 1e-9:
                new[cname] = vv
        gross = sum(new[c] * ret[i, idx[c]] for c in new)
        turn = sum(abs(new.get(c, 0.0) - prev.get(c, 0.0)) for c in set(new) | set(prev))
        port[i] = gross - turn * COST
        turn_a[i], expo_a[i] = turn, sum(new.values())
        prev = new
    r = port[start:]
    eq = np.cumprod(1 + r)
    cagr = eq[-1] ** (TD / len(r)) - 1
    mdd = (eq / np.maximum.accumulate(eq) - 1).min()
    return dict(cagr=cagr, mdd=mdd, calmar=cagr / abs(mdd) if mdd < 0 else np.nan,
                sharpe=r.mean() / r.std() * np.sqrt(TD),
                turn=turn_a[start:].sum() / (len(r) / TD), expo=expo_a[start:].mean())


def main():
    rows = []
    for k, cap, tag in [(5, 1.0, "P1 k=5"), (2, 0.40, "P3 k=2/cap40")]:
        close, f, res = build(k, cap)
        ov = {c: fast_pos(close[c]).reindex(close.index).ffill().fillna(1.0).to_numpy()
              for c in close.columns}
        ov_half = {c: np.clip(0.5 + 0.5 * v, 0, 1) for c, v in ov.items()}
        mom20 = close / close.shift(20) - 1
        ma200 = close.rolling(200, min_periods=150).mean()
        gate_df = ((mom20 > 0) & (close > ma200)).fillna(False)
        gate = {c: gate_df[c].to_numpy() for c in close.columns}
        arms = {
            "S2+VT15(基线)": sim(close, f, res),
            "VT15×质量Q": sim(close, f, res, q_mode=True),
            "单支快线": sim(close, f, res, ov=ov),
            "单支快线×质量Q": sim(close, f, res, ov=ov, q_mode=True),
            "快线半仓版": sim(close, f, res, ov=ov_half),
            "入场门票(新买入需mom20>0&>MA200)": sim(close, f, res, gate=gate),
        }
        # 等敞口对照：把 VT 目标抬高，让"快线臂"的平均敞口接近基线（0.64）
        base_expo = arms["S2+VT15(基线)"]["expo"]
        ov_expo = arms["单支快线"]["expo"]
        hi = 0.15 * base_expo / max(ov_expo, 1e-6)
        close2, f2, res2 = build(k, cap, target=round(hi, 2))
        ov2 = {c: fast_pos(close2[c]).reindex(close2.index).ffill().fillna(1.0).to_numpy()
               for c in close2.columns}
        arms[f"等敞口快线(VT{hi:.2f})"] = sim(close2, f2, res2, ov=ov2)
        arms[f"等敞口对照(VT{hi:.2f})"] = sim(close2, f2, res2)
        print(f"\n== {tag} ==")
        for nm, a in arms.items():
            rows.append(dict(book=tag, arm=nm, **a))
        print(pd.DataFrame([r for r in rows if r["book"] == tag]).round(3).to_string(index=False))
    pd.DataFrame(rows).round(4).to_csv(OUT / "overlay_arms.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
