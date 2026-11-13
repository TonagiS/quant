"""POS-01（VT15）对指定板块 ETF 的当前总敞口判断。

规则（position/README.md + policy.py）：
  总敞口 = clip(15% / 60日已实现年化波动, 0, 1)，不加杠杆。
输出：每个标的的最新价、vol20/vol40/vol60、目标敞口、近 20 日动量、距 MA60 偏离。
"""
import os
import sys
import numpy as np
import pandas as pd

QUANT = r"E:\Code\quant"
sys.path.insert(0, QUANT)
from position.policy import realized_vol, target_exposure  # noqa: E402

CACHE = os.path.join(QUANT, "data", "cache2019")
OUT = os.path.join(QUANT, "work")

TARGETS = [
    ("sh512760", "芯片(国证芯片)"),
    ("sh512480", "半导体(中证全指半导)"),
    ("sh515050", "CPO(5G通信/光模块)"),
    ("sh515320", "PCB(电子50)"),
    ("sh512980", "中证传媒"),
    ("sz159869", "动漫游戏(中证动漫游戏)"),
]


def load(code):
    """优先读缓存；缺失则用 akshare 拉全历史并写回缓存。"""
    path = os.path.join(CACHE, f"{code}.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["date"])
    else:
        import time
        import akshare as ak
        sym = code[-6:]
        last = None
        df = None
        for i in range(3):
            try:
                raw = ak.fund_etf_hist_em(symbol=sym, period="daily", start_date="20190101",
                                          end_date="20261231", adjust="qfq")
                df = pd.DataFrame({
                    "date": pd.to_datetime(raw["日期"]),
                    "close": pd.to_numeric(raw["收盘"], errors="coerce"),
                    "amount": pd.to_numeric(raw["成交额"], errors="coerce"),
                })
                break
            except Exception as e:  # 东财接口偶发断连
                last = e
                time.sleep(2 + 2 * i)
        if df is None:  # 东财挂了走新浪（不复权，ETF 分红影响小）
            raw = ak.fund_etf_hist_sina(symbol=code)
            df = pd.DataFrame({
                "date": pd.to_datetime(raw["date"]),
                "close": pd.to_numeric(raw["close"], errors="coerce"),
                "amount": pd.to_numeric(raw["amount"], errors="coerce"),
            })
        df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        df.to_csv(path, index=False)
    return df.sort_values("date").reset_index(drop=True)


def main():
    rows = []
    for code, name in TARGETS:
        df = load(code)
        close = pd.to_numeric(df["close"], errors="coerce").dropna()
        rets = close.pct_change().dropna()
        last_date = df["date"].iloc[-1]
        vol60 = realized_vol(rets.values, 60, 40)
        vol40 = realized_vol(rets.values, 40, 30)
        vol20 = realized_vol(rets.values, 20, 15)
        vol120 = realized_vol(rets.values, 120, 80)
        expo = target_exposure(rets.values, 0.15, 60, 40, 1.0)
        px = float(close.iloc[-1])
        mom20 = float(close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 21 else np.nan
        mom5 = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) > 6 else np.nan
        ma60 = float(close.tail(60).mean())
        rows.append(dict(code=code, name=name, last_date=str(last_date.date()), px=px,
                         vol20=vol20, vol40=vol40, vol60=vol60, vol120=vol120, expo=expo,
                         mom5=mom5, mom20=mom20, ma60_dev=px / ma60 - 1, n=len(close)))

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "pos1_sectors_20261114.csv"), index=False, encoding="utf-8-sig")

    show = df.copy()
    show["px"] = show["px"].map(lambda x: f"{x:.3f}")
    for c in ["vol20", "vol40", "vol60", "vol120", "expo", "mom5", "mom20", "ma60_dev"]:
        show[c] = show[c].map(lambda x: f"{x:6.2%}")
    print(show[["code", "name", "last_date", "px", "vol20", "vol40", "vol60", "vol120", "expo",
                "mom5", "mom20", "ma60_dev"]].to_string(index=False))

    print("\n按敞口分组：")
    for _, r in df.sort_values("expo").iterrows():
        print(f"  {r['name']:<22} 敞口 {r['expo']:5.0%}   vol60 {r['vol60']:5.1%}   "
              f"近20日 {r['mom20']:+6.2%}   距MA60 {r['ma60_dev']:+6.2%}")

    # ---- 等权组合口径：pos-1 的 VT15 本来是作用在策略组合日收益上的 ----
    rets_map = {}
    for code, _ in TARGETS:
        s = load(code).set_index("date")["close"].astype(float)
        rets_map[code] = s.pct_change()
    rets = pd.DataFrame(rets_map).dropna(how="all")
    combos = {
        "科技四件套(芯片+半导体+CPO+PCB)": ["sh512760", "sh512480", "sh515050", "sh515320"],
        "传媒+游戏(等权)": ["sh512980", "sz159869"],
        "六个等权": [c for c, _ in TARGETS],
    }
    print("\n等权组合口径（按组合日收益重算 VT15）：")
    for label, cols in combos.items():
        r = rets[cols].mean(axis=1).dropna()
        v60 = realized_vol(r.values, 60, 40)
        e = target_exposure(r.values, 0.15, 60, 40, 1.0)
        print(f"  {label:<28} vol60 {v60:5.1%}  组合敞口 {e:5.1%}  (成分平均敞口 "
              f"{df[df['code'].isin(cols)]['expo'].mean():5.1%})")

    # 相关性（60 日）
    print("\n60 日日收益相关性：")
    sub = rets[[c for c, _ in TARGETS]].tail(60)
    print(sub.corr().round(2).to_string())


if __name__ == "__main__":
    main()
