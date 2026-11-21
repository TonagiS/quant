"""国家队通道：7 日累计 + 趋势/慢性减仓检测。

用法
----
venv\\Scripts\\python.exe tools\\nt_trend.py

口径
----
* 数据：data/national_team/etf_shares_hist.csv（9 只沪市宽基，2019 起）
* 合计份额 = 9 只 ETF 份额相加（亿份）；金额 = 份额变动 × 最新价（近似）
* 趋势指标：
  1) 窗口族 3/5/7/10/14/20 日累计变化 + 相对过去 250 日的 z 分位
     （池子日波动大，固定阈值容易被噪声骗，z 更稳）
  2) 连续净赎回天数、距 60 日高点的回撤
  3) 慢性减仓(drip)：14 日累计 ≤ -4%、单日最大跌幅 < 1.5%、10 日内下跌 ≥ 6 天
     —— 用来抓“每天减一点、3/5 日窗口看不出来”的慢趋势
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / "data" / "national_team" / "etf_shares_hist.csv"
OUT = ROOT / "runs" / "2026-11-16_nt_3d5d"

PX = {
    510050: 2.883, 510300: 4.070, 510310: 3.956, 510330: 4.240,
    510500: 6.415, 512500: 3.633, 512100: 2.546, 588000: 1.314, 588080: 1.273,
}


def main() -> None:
    d = pd.read_csv(HIST, parse_dates=["date"])
    p = d.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4
    names = d.drop_duplicates("code").set_index("code")["name"]
    tot = p.sum(axis=1)
    x = tot.diff()

    last = tot.index[-1]
    rows = []
    for c in p.columns:
        a, b = p.loc[last, c], p.loc[tot.index[-8], c]
        rows.append(dict(code=c, name=names[c], now=round(a, 1),
                         d7=round(a - b, 2), d7pct=round((a / b - 1) * 100, 2),
                         d7amt=round((a - b) * PX[c], 1)))
    r = pd.DataFrame(rows)
    print("=== 7 日累计（%s，基准 %s）===" % (str(last)[:10], str(tot.index[-8])[:10]))
    print(r.to_string(index=False))
    print("合计 7 日 %+.2f 亿份 / %.2f%% / 约 %+.0f 亿元"
          % (tot.iloc[-1] - tot.iloc[-8], (tot.iloc[-1] / tot.iloc[-8] - 1) * 100, r.d7amt.sum()))

    print("\n=== 趋势窗口 ===")
    print("窗口  累计变化   250日z   250日分位")
    for w in (3, 5, 7, 10, 14, 20):
        cum = (tot / tot.shift(w) - 1) * 100
        h = cum.iloc[-251:-1]
        zz = (cum.iloc[-1] - h.mean()) / h.std()
        print("%3dd  %+7.2f%%   %+5.2f   %3.0f%%" % (w, cum.iloc[-1], zz, h.rank(pct=True).iloc[-1] * 100))

    run = 0
    for v in (x.iloc[-60:] < 0).iloc[::-1]:
        if v:
            run += 1
        else:
            break
    h60 = tot.iloc[-60:]
    print("\n连续净赎回 %d 天；60 日高点 %s %.1f，现回撤 %.1f 亿份（%.2f%%）"
          % (run, str(h60.idxmax())[:10], h60.max(), tot.iloc[-1] - h60.max(),
             (tot.iloc[-1] / h60.max() - 1) * 100))

    c3 = (tot / tot.shift(3) - 1) * 100
    c5 = (tot / tot.shift(5) - 1) * 100
    c14 = (tot / tot.shift(14) - 1) * 100
    down10 = x.rolling(10).apply(lambda s: (s < 0).sum())
    maxdrop = (x / tot.shift(1) * 100).rolling(14).min()
    drip = (c14 <= -4) & (c3.abs() <= 1.5) & (c5.abs() <= 2) & (down10 >= 6) & (maxdrop > -1.5)
    dn = drip.dropna()
    print("\n慢性减仓(drip) 历史触发 %d 天，最近一次 %s；今日 %s"
          % (int(dn.sum()), str(dn[dn].index[-1])[:10] if dn.sum() else "-",
             "触发" if dn.iloc[-1] else "未触发"))

    OUT.mkdir(parents=True, exist_ok=True)
    r.to_csv(OUT / "nt_7d.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
