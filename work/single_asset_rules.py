"""单标的仓位规则扫描：VT12/VT15/VT20 vs 回撤节流 vs 净值均线。

用户问题：仓位控制应该逐个 ETF 测，在单标的基准下哪个规则更有效？拉长样本看。

口径
- 标的：cache2019 全部 ETF（2019 起）；cache2007 的 15 个指数 + 22 只蓝筹（2007 起）
- 规则都在单标的上作用，敞口 ∈ [0,1]，不加杠杆
- 信号 t 日收盘计算、t+1 生效（delay=1），调仓每 5 日（VT 类），成本单边 5bp
- 对照：①满仓 ②同平均仓位的固定仓位（无成本）
- 探索性归因，不作判据、不预登记
"""
import glob
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "runs" / "2026-11-15_single_asset_vt"
OUT.mkdir(parents=True, exist_ok=True)
TD = 252
COST = 0.0005


def load_series(path):
    df = pd.read_csv(path, parse_dates=["date"])
    s = df.set_index("date")["close"].astype(float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def stats(net):
    nav = (1 + net).cumprod()
    n = len(net)
    if n < 60:
        return dict(cagr=np.nan, mdd=np.nan, calmar=np.nan, sharpe=np.nan)
    cagr = nav.iloc[-1] ** (TD / n) - 1
    mdd = (nav / nav.cummax() - 1).min()
    sd = net.std()
    sharpe = net.mean() / sd * np.sqrt(TD) if sd > 0 else np.nan
    calmar = cagr / abs(mdd) if mdd < 0 else np.nan
    return dict(cagr=cagr, mdd=mdd, calmar=calmar, sharpe=sharpe)


def held(pos, ret, cost=COST):
    pos = pos.shift(1).fillna(0.0)
    gross = pos * ret.fillna(0.0)
    turn = pos.diff().abs().fillna(pos.abs())
    net = gross - turn * cost
    return net, pos


def rule_vt(ret, target=0.15, window=60, reb=5):
    vol = ret.rolling(window, min_periods=40).std() * np.sqrt(TD)
    raw = (target / vol).clip(0, 1)
    mask = np.arange(len(raw)) % reb == 0
    return raw.where(mask).ffill().fillna(0.0)


def rule_throttle(nav, thresh=0.20, floor=0.5):
    """回撤节流：从高点回撤≥20% 后敞口降到 0.5，回到 10% 以内恢复 1。"""
    dd = nav / nav.cummax() - 1
    pos = np.ones(len(dd))
    cur = 1.0
    for i, d in enumerate(dd.values):
        if d <= -thresh:
            cur = floor
        elif d >= -thresh / 2:
            cur = 1.0
        pos[i] = cur
    return pd.Series(pos, index=dd.index)


def rule_navma(nav, window=60, low=0.0):
    ma = nav.rolling(window, min_periods=20).mean()
    return pd.Series(np.where(nav > ma, 1.0, low), index=nav.index)


def eval_one(close, name, group):
    ret = close.pct_change()
    ret = ret[ret.index >= close.index[0]]
    rows = {}
    base_net = ret.fillna(0.0)
    rows["满仓"] = stats(base_net)

    for tgt in (0.12, 0.15, 0.20):
        pos = rule_vt(ret, tgt)
        net, p = held(pos, ret)
        st = stats(net)
        st["avg_pos"] = p.loc[p.index >= pos.first_valid_index()].mean()
        st["turnover"] = p.diff().abs().sum() / (len(p) / TD)
        rows[f"VT{int(tgt*100)}"] = st

    # 回撤节流 / 净值均线：用自身净值路径
    nav0 = (1 + base_net).cumprod()
    for label, pos in (("回撤节流20", rule_throttle(nav0)),
                       ("净值MA60清仓", rule_navma(nav0, 60, 0.0)),
                       ("净值MA60半仓", rule_navma(nav0, 60, 0.5))):
        net, p = held(pos, ret)
        st = stats(net)
        st["avg_pos"] = p.mean()
        rows[label] = st

    # 固定同平均仓位对照（以 VT15 平均仓位为准，无成本）
    v = rows["VT15"]["avg_pos"]
    fixed_net = v * ret.fillna(0.0)
    st = stats(fixed_net)
    st["avg_pos"] = v
    rows["固定同仓位"] = st

    out = []
    for k, st in rows.items():
        out.append(dict(group=group, name=name, rule=k, **st))
    return out


def main():
    all_rows = []
    # ETF
    for p in sorted(glob.glob(str(DATA / "cache2019" / "*.csv"))):
        code = os.path.basename(p)[:-4]
        s = load_series(p)
        if len(s) < 200:
            continue
        all_rows += eval_one(s, code, "ETF")
    # 指数 + 蓝筹（长样本）
    for p in sorted(glob.glob(str(DATA / "cache2007" / "*.csv"))):
        code = os.path.basename(p)[:-4].replace("_20070101", "")
        s = load_series(p)
        if len(s) < 250:
            continue
        grp = "指数" if code.startswith("idx_") else "蓝筹"
        all_rows += eval_one(s, code, grp)

    df = pd.DataFrame(all_rows)
    df.to_csv(OUT / "single_asset_rules.csv", index=False, encoding="utf-8-sig")

    print(f"标的 {df['name'].nunique()} 个，规则 {df['rule'].nunique()} 种\n")

    # 汇总：每个规则相对"满仓"的卡玛增量，按组
    piv = df.pivot_table(index=["group", "name"], columns="rule", values="calmar")
    for g in ["ETF", "指数", "蓝筹"]:
        sub = piv.loc[g]
        print(f"== {g}（n={len(sub)}）卡玛中位 ==")
        print(sub.median().round(3).to_string())
        print()

    # 相对满仓的增量
    for g in ["ETF", "指数", "蓝筹"]:
        sub = piv.loc[g]
        delta = sub.sub(sub["满仓"], axis=0)
        print(f"== {g} 相对满仓的卡玛增量 ==")
        for r in ["VT12", "VT15", "VT20", "回撤节流20", "净值MA60清仓", "净值MA60半仓", "固定同仓位"]:
            d = delta[r].dropna()
            print(f"  {r:<12} 中位 {d.median():+.3f}  为正 {(d>0).mean():5.1%}  "
                  f"≥0.05 {(d>=0.05).mean():5.1%}  n={len(d)}")
        print()

    # 单标的最优规则分布
    print("== 每个标的最优规则（按卡玛）==")
    best = piv.drop(columns=["固定同仓位"]).idxmax(axis=1)
    print(best.value_counts().to_string())
    print()

    # 典型案例
    for nm in ["sh515050", "sh512760", "sh512980", "sz159869", "sh588810"]:
        if nm in df["name"].values:
            t = df[df["name"] == nm].set_index("rule")[["cagr", "mdd", "calmar", "avg_pos"]]
            print(f"-- {nm} --")
            print(t.round(3).to_string())
            print()

    # 长样本尾部
    print("== 指数/蓝筹里 VT15 卡玛增量最好的 5 个 / 最差 5 个 ==")
    sub = piv.loc[["指数", "蓝筹"]]
    d = sub["VT15"] - sub["满仓"]
    print(d.sort_values().head(5).round(3).to_string())
    print("...")
    print(d.sort_values().tail(5).round(3).to_string())

    (OUT / "summary.json").write_text(json.dumps({
        "n_names": int(df["name"].nunique()),
        "note": "exploratory; no prereg",
    }, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
