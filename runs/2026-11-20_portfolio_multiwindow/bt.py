# -*- coding: utf-8 -*-
"""组合线（S2 / VT15 / VT15·5日）多年份起点回测 vs 无策略。

口径与实盘账本一致（position/engine.py + paper_account.py）：
  * 信号：rm = 20/60/120 日动量截面分位均值；候选 = 累计bar>=150 且收盘 > 60 日线
  * 选前 k=5，20 日波动逆波动加权，单边 5bp，delay=1 收盘成交，reb_mode=rolling
  * S2/VT15：reb=10；VT15·5日：reb=5
  * VT15 敞口 = min(1, 15% / vol60)，S2 满仓
  * 无策略 = 同池等权、每日再平衡、零成本

数据：
  * ETF 池：data/cache_ohlc_fullhist（92 只，含分红比例复权）
  * 指数池：data/cache2007 的 17 个中证/上证指数（基日 2004-12-31）
  * 校验：ext 池 2019-08-13 起应复现 engine.py 记录的 年化 18.8% / MDD -33.6% / 卡玛 0.561

数据清洗：任一日涨跌幅绝对值 > 21%（超出 ETF 涨跌停带宽）视为复权/拆分脏点，整只剔除。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

ROOT = r"E:\Code\quant"
sys.path.insert(0, ROOT)
from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, features, load_panel, perf, run_with_base,
)

DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "runs", "2026-11-20_portfolio_multiwindow")
YEARS = (2009, 2013, 2017, 2019)
COST = 0.0005
BAD_JUMP = 0.21


def clean(close: pd.DataFrame, max_jump: float = BAD_JUMP) -> tuple[pd.DataFrame, list]:
    """两件事：1) 去掉收盘 <=0 的脏点；2) 从"此后不再出现超限跳变"的第一天开始截断。

    早年（2005-2008）的比例复权重建有负价/断崖，直接算收益会造出 ±100% 的假行情。
    """
    out, dropped = {}, []
    for c in close.columns:
        s = close[c].replace(0.0, np.nan)
        s = s[s > 0].dropna()
        r = s.pct_change()
        bad = np.flatnonzero(np.abs(r.to_numpy()) > max_jump)
        lo = 0 if len(bad) == 0 else int(bad[-1]) + 1
        s = s.iloc[lo:]
        if len(s) < 300:
            dropped.append(c)
            continue
        out[c] = s
    return pd.DataFrame(out).sort_index(), dropped


def load_etf_panel() -> pd.DataFrame:
    d = os.path.join(DATA, "cache_ohlc_fullhist")
    closes = {}
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".csv"):
            continue
        df = pd.read_csv(os.path.join(d, fn), parse_dates=["date"], usecols=["date", "close"])
        closes[fn[:-4]] = df.drop_duplicates("date").set_index("date")["close"].astype(float).dropna()
    close = pd.DataFrame(closes).sort_index()
    return close.loc[:, close.notna().sum() >= 300]


def load_idx_panel() -> pd.DataFrame:
    d = os.path.join(DATA, "cache2007")
    closes = {}
    for fn in sorted(os.listdir(d)):
        if not fn.startswith("idx_"):
            continue
        df = pd.read_csv(os.path.join(d, fn), parse_dates=["date"])
        col = "close" if "close" in df.columns else df.columns[1]
        closes[fn[4:-13]] = df.drop_duplicates("date").set_index("date")[col].astype(float)
    close = pd.DataFrame(closes).sort_index()
    return close.loc[:, close.notna().sum() >= 300]


def load_h300() -> pd.Series:
    for d, name in (("cache_index_proxy", "H00300"), ("cache2019", "sh000300")):
        p = os.path.join(DATA, d, name + ".csv")
        if os.path.exists(p):
            df = pd.read_csv(p, parse_dates=["date"])
            col = "close" if "close" in df.columns else df.columns[1]
            return df.drop_duplicates("date").set_index("date")[col].astype(float).dropna()
    return pd.Series(dtype=float)


def configs() -> dict:
    common = dict(k=5, weight_mode="invvol", buy_cost=COST, sell_cost=COST, min_names=8)
    return {
        "S2": Config(reb=10, reb_mode="rolling", rule=Fixed(1.0), **common),
        "VT15": Config(reb=10, reb_mode="rolling", rule=VolTarget(0.15, 60, every=1), **common),
        "VT15_5d": Config(reb=5, reb_mode="rolling", rule=VolTarget(0.15, 60, every=5), **common),
    }


def stats(r, dates) -> dict:
    r = np.asarray(r, float)
    eq = np.cumprod(1.0 + np.nan_to_num(r))
    m = perf(r)
    dd = eq / np.maximum.accumulate(eq) - 1.0
    m["dd_date"] = str(dates[int(np.argmin(dd))].date())
    return m


def window_rows(tag: str, close: pd.DataFrame, f: dict, runs: dict) -> list[dict]:
    dates = close.index
    bench_ret = np.nan_to_num(f["ret"].mean(axis=1).fillna(0.0).to_numpy())
    rows = []
    for y in YEARS:
        lo = max(int(dates.searchsorted(pd.Timestamp(year=y, month=1, day=1))), int(f["start"]))
        if lo >= len(dates) - 500:
            continue
        row = dict(池=tag, 起点年=y, 实际起点=str(dates[lo].date()), 末端=str(dates[-1].date()),
                   年数=round((len(dates) - lo) / 252.0, 2),
                   起点有效标的=int(f["valid"].iloc[lo].sum()))
        for name, res in runs.items():
            m = stats(res["ret"][lo:], dates[lo:])
            row[name + "_年化"] = m["cagr"]
            row[name + "_回撤"] = m["mdd"]
            row[name + "_卡玛"] = m["calmar"]
            row[name + "_最深日"] = m["dd_date"]
            row[name + "_均仓"] = float(np.mean(res["expo"][lo:]))
        bm = stats(bench_ret[lo:], dates[lo:])
        row["BH_年化"], row["BH_回撤"], row["BH_卡玛"] = bm["cagr"], bm["mdd"], bm["calmar"]
        rows.append(row)
    return rows


def main() -> None:
    # 0) 复现校验：ext 池 2019-08-13 起
    ext = load_panel("ext")
    fe = features(ext, min_names=8)
    cfg = Config(k=5, reb=10, reb_mode="rolling", weight_mode="invvol",
                 buy_cost=COST, sell_cost=COST, rule=Fixed(1.0))
    res = run_with_base(ext, fe, cfg)
    lo = int(ext.index.searchsorted(pd.Timestamp("2019-08-13")))
    chk = stats(res["ret"][lo:], ext.index[lo:])
    print("[校验] ext 池 2019-08-13 起：年化 %.1f%% / MDD %.1f%% / 卡玛 %.3f（engine.py 记录 18.8 / -33.6 / 0.561）"
          % (chk["cagr"] * 100, chk["mdd"] * 100, chk["calmar"]), flush=True)

    # 1) 实盘 ext 池（只有 2019-08 起）：与宽池分开列，臂名对齐
    ext_rows = []
    for name, cc in configs().items():
        r = run_with_base(ext, fe, cc)
        m = stats(r["ret"][lo:], ext.index[lo:])
        ext_rows.append(dict(池="实盘ext池", 起点年=2019, 实际起点=str(ext.index[lo].date()),
                             末端=str(ext.index[-1].date()), 年数=round((len(ext) - lo) / 252.0, 2),
                             臂=name, 年化=m["cagr"], 回撤=m["mdd"], 卡玛=m["calmar"],
                             均仓=float(np.mean(r["expo"][lo:]))))
        print("[实盘ext池] %s 年化 %.1f%% / MDD %.1f%% / 卡玛 %.3f"
              % (name, m["cagr"] * 100, m["mdd"] * 100, m["calmar"]), flush=True)
    be = np.nan_to_num(fe["ret"].mean(axis=1).fillna(0.0).to_numpy())
    mb = stats(be[lo:], ext.index[lo:])
    ext_rows.append(dict(池="实盘ext池", 起点年=2019, 实际起点=str(ext.index[lo].date()),
                         末端=str(ext.index[-1].date()), 年数=round((len(ext) - lo) / 252.0, 2),
                         臂="无策略(等权)", 年化=mb["cagr"], 回撤=mb["mdd"], 卡玛=mb["calmar"], 均仓=1.0))
    print("[实盘ext池] 无策略 年化 %.1f%% / MDD %.1f%% / 卡玛 %.3f"
          % (mb["cagr"] * 100, mb["mdd"] * 100, mb["calmar"]), flush=True)
    os.makedirs(OUT, exist_ok=True)
    pd.DataFrame(ext_rows).to_csv(os.path.join(OUT, "ext_pool.csv"), index=False, encoding="utf-8-sig")

    rows = []
    for tag, close, note in (("ETF池", load_etf_panel(), ""), ("指数池", load_idx_panel(), "")):
        c2, bad = clean(close)
        f = features(c2, min_names=8)
        dates = c2.index
        runs = {name: run_with_base(c2, f, cc) for name, cc in configs().items()}
        print("[%s] 保留 %d 只（剔除脏点 %d 只：%s），%s → %s，可运行起点 %s"
              % (tag, c2.shape[1], len(bad), ",".join(bad[:6]), dates[0].date(),
                 dates[-1].date(), dates[int(f["start"])].date()), flush=True)
        rows += window_rows(tag, c2, f, runs)

    h = load_h300()
    hd = h.index
    for y in YEARS:
        lo = int(hd.searchsorted(pd.Timestamp(year=y, month=1, day=1)))
        m = stats(h.pct_change().fillna(0.0).to_numpy()[lo + 1:], hd[lo + 1:])
        rows.append(dict(池="沪深300全收益", 起点年=y, 实际起点=str(hd[lo + 1].date()),
                         末端=str(hd[-1].date()), 年数=round((len(hd) - lo - 1) / 252.0, 2),
                         BH_年化=m["cagr"], BH_回撤=m["mdd"], BH_卡玛=m["calmar"],
                         BH_最深日=m["dd_date"]))

    df = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(os.path.join(OUT, "summary.csv"), index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 320)
    pd.set_option("display.max_columns", 60)
    print(df.to_string(index=False, float_format=lambda x: "%.4f" % x))


if __name__ == "__main__":
    main()
