"""诊断：VT15 在 52 只等权基座上为什么失效。

看三件事：
1) 等权组合自己的 60 日波动水平（决定 VT15 平均敞口）
2) 敞口低（<0.5）的时段之后 60 日等权收益 vs 全样本
3) 回撤最深的那段，VT15 敞口是多少（是不是砍在反弹前）
"""
import glob
import os
import sys
import numpy as np
import pandas as pd

QUANT = r"E:\Code\quant"
CACHE = os.path.join(QUANT, "data", "cache2019")
sys.path.insert(0, QUANT)
from position.policy import realized_vol  # noqa: E402

TD = 252


def load_all():
    frames = {}
    for p in glob.glob(os.path.join(CACHE, "*.csv")):
        code = os.path.basename(p)[:-4]
        df = pd.read_csv(p, parse_dates=["date"])
        s = df.set_index("date")["close"].astype(float)
        frames[code] = s.pct_change()
    rets = pd.DataFrame(frames)
    # 只用 2019-01 起全样本在的标的；等权 = 当日有报价的标的平均
    return rets.loc["2019-01-02":].dropna(how="all")


def main():
    rets = load_all()
    eq = rets.mean(axis=1).dropna()
    eq = eq.loc[:"2026-11-13"]
    nav = (1 + eq).cumprod()

    vol60 = eq.rolling(60, min_periods=40).std() * np.sqrt(TD)
    expo = (0.15 / vol60).clip(0, 1)

    print(f"样本 {eq.index[0].date()} ~ {eq.index[-1].date()}  {len(eq)} 日")
    print(f"等权组合 vol60：均值 {vol60.mean():.2%}  中位 {vol60.median():.2%}  "
          f"P10 {vol60.quantile(.1):.2%}  P90 {vol60.quantile(.9):.2%}")
    print(f"VT15 敞口：均值 {expo.mean():.1%}  中位 {expo.median():.1%}  "
          f"<0.5 的天数 {(expo < 0.5).sum()} ({(expo < 0.5).mean():.1%})  最低 {expo.min():.1%} @ {expo.idxmin().date()}")

    # 敞口低时买入，之后 60 日等权收益
    fwd60 = eq.rolling(60).apply(lambda x: (1 + x).prod() - 1).shift(-60)
    ok = expo.notna() & fwd60.notna()
    low = ok & (expo < 0.5)
    high = ok & (expo >= 0.9)
    print(f"\n之后 60 日等权收益：全样本均值 {fwd60[ok].mean():+.2%}")
    print(f"  敞口<0.5 的日子：n={low.sum():4d}  均值 {fwd60[low].mean():+.2%}")
    print(f"  敞口≥0.9 的日子：n={high.sum():4d}  均值 {fwd60[high].mean():+.2%}")

    # 最大回撤区间
    dd = nav / nav.cummax() - 1
    trough = dd.idxmin()
    peak = nav.loc[:trough].idxmax()
    print(f"\n最大回撤 {dd.min():.2%}：峰 {peak.date()} → 谷 {trough.date()}")
    seg = expo.loc[peak:trough]
    print(f"  该区间 VT15 敞口：均值 {seg.mean():.1%}  最低 {seg.min():.1%} @ {seg.idxmin().date()}")
    # 谷底后 60 日反弹
    after = nav.loc[trough:].pct_change(60).dropna()
    print(f"  谷底后 60 日等权涨幅：{after.iloc[0] if len(after) else float('nan'):+.2%}")
    print(f"  谷底当天/附近敞口：{expo.loc[trough:trough].iloc[0]:.1%}" if trough in expo.index else "")

    # 敞口最低的 5 段（连续段中心）
    tmp = expo.dropna()
    low_days = tmp[tmp < 0.45].index
    print(f"\n敞口<0.45 的日子 {len(low_days)} 天，示例：{', '.join(str(d.date()) for d in low_days[:6])} ...")

    # 分年：等权收益、平均敞口
    print("\n分年：等权收益 / 平均敞口")
    for y, g in eq.groupby(eq.index.year):
        e = expo.loc[g.index].mean()
        print(f"  {y}: {((1+g).prod()-1):+7.2%} / {e:5.1%}")


if __name__ == "__main__":
    main()
