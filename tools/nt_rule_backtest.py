"""汇金份额防守规则 · 口径 × 窗口 × 阈值 网格回测（只读，不改任何状态）。

背景
----
现行冻结规则：9 只沪市宽基 ETF 份额合计，n=14 个交易日逐日百分比相加，
累计 ≤ -10% 触发；触发后第 l=5 个交易日起空仓 k=20 个交易日。
本次要回答：换成"单只（中证500 / 中证1000 / 小盘合计 / 分板块取最差）"当信号、
阈值多开几档，结果会不会不一样。

口径（variant）
--------------
all       9 只合计（现行）
sz50      上证50（510050）
hs300     沪深300 三只合计（510300/510310/510330）
small500  中证500 双只合计（510500/512500）
small1000 中证1000（512100）
small     500 + 1000 合计
kc50      科创50 双只合计（588000/588080）
worst4    上证50/沪深300/500/1000 各自累计取最差（分档思路）

执行假设
--------
* 交易标的：沪深300ETF(510300) 收盘价（data/cache2019/sh510300.csv）。
* 不防守时满仓，防守窗口内空仓；每换手一次计万 5 成本。
* 信号在第 i 日收盘后可知，空仓从 i+l 当天开始，持续 k 个交易日。
* 回测区间：份额数据 2019-01-02 起；科创50 从 2020-11-16 起。
* 这是"份额信号 → 510300 择时"的简化回放，不含分红、冲击成本与滑点。

用法：venv\\Scripts\\python.exe tools\\nt_rule_backtest.py [--csv out.csv]
"""

from __future__ import annotations

import argparse
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SHARES = ROOT / "data" / "national_team" / "etf_shares_hist.csv"
PX = ROOT / "data" / "cache2019" / "sh510300.csv"

VARIANTS = {
    "all": ["510050", "510300", "510310", "510330", "510500", "512500", "512100", "588000", "588080"],
    "sz50": ["510050"],
    "hs300": ["510300", "510310", "510330"],
    "small500": ["510500", "512500"],
    "small1000": ["512100"],
    "small": ["510500", "512500", "512100"],
    "kc50": ["588000", "588080"],
    "worst4": ["SZ50", "HS300", "SM500", "SM1000"],   # 虚拟口径，单独处理
}
SECTORS = {
    "SZ50": ["510050"],
    "HS300": ["510300", "510310", "510330"],
    "SM500": ["510500", "512500"],
    "SM1000": ["512100"],
}

L, K = 5, 20          # 延迟与防守长度（冻结值）
COST = 0.0005         # 单边万 5


def load_panel(path: Path | str = SHARES) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"], dtype={"code": str})
    return df.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4


def daily_pct(panel: pd.DataFrame, codes: list[str]) -> pd.Series:
    """逐日百分比变化：只用前后两天都有的代码，按份额加总后取比值。"""
    sub = panel[[c for c in codes if c in panel.columns]]
    prev = sub.shift(1)
    common = sub.notna() & prev.notna()
    cur = (sub * common).sum(axis=1, min_count=1)
    old = (prev * common).sum(axis=1, min_count=1)
    return (cur / old - 1) * 100


def load_price() -> pd.Series:
    px = pd.read_csv(PX, encoding="utf-8-sig", parse_dates=["date"]).set_index("date").sort_index()
    return px["close"].astype(float)


def build_state(cum: pd.Series, m: float, l: int = L, k: int = K) -> np.ndarray:
    """cum ≤ m 触发；[i+L, i+L+K) 空仓。返回 0/1 仓位（1=满仓）。"""
    n = len(cum)
    pos = np.ones(n, dtype=int)
    for i, v in enumerate(cum.values):
        if v != v or v > m:
            continue
        lo = i + l
        hi = min(n, lo + k)
        pos[lo:hi] = 0
    return pos


def run(px: pd.Series, pos: np.ndarray) -> dict:
    ret = px.pct_change().fillna(0.0).values
    hold = np.roll(pos, 1)
    hold[0] = 1
    cost = np.abs(np.diff(np.concatenate([[1], pos]))) * COST
    strat = (1 + ret * hold) * (1 - cost)
    eq = np.cumprod(strat)
    n = len(eq)
    years = n / 252
    total = eq[-1] - 1
    cagr = eq[-1] ** (1 / years) - 1 if years > 0 else np.nan
    peak = np.maximum.accumulate(eq)
    mdd = float((eq / peak - 1).min())
    return {"total": total, "cagr": cagr, "mdd": mdd,
            "flat_pct": float((pos == 0).mean()), "eq": eq}


def yearly(eq: np.ndarray, dates: pd.DatetimeIndex) -> dict:
    s = pd.Series(eq, index=dates)
    out = {}
    for y, grp in s.groupby(s.index.year):
        base = grp.iloc[0]
        out[y] = grp.iloc[-1] / base - 1
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "data" / "national_team" / "rule_grid_20261116.csv"))
    ap.add_argument("--n", default="3,5,10,14,20")
    ap.add_argument("--m", default="-3,-5,-8,-10,-12,-15")
    ap.add_argument("--shares", default=str(SHARES))
    a = ap.parse_args(argv)

    panel = load_panel(Path(a.shares))
    px_all = load_price()
    px = px_all.reindex(panel.index).ffill()
    dates = px.index
    ret_bh = px.pct_change().fillna(0.0)
    eq_bh = np.cumprod(1 + ret_bh.values)
    bh = {"total": eq_bh[-1] - 1, "cagr": eq_bh[-1] ** (252 / len(eq_bh)) - 1,
          "mdd": float((eq_bh / np.maximum.accumulate(eq_bh) - 1).min()),
          "flat_pct": 0.0}
    print("区间 %s ~ %s（%d 个交易日）｜510300 买入持有：%+.1f%%，年化 %+.1f%%，最大回撤 %.1f%%"
          % (dates[0].date(), dates[-1].date(), len(dates),
             bh["total"] * 100, bh["cagr"] * 100, bh["mdd"] * 100))

    pct_cache = {k: daily_pct(panel, v) for k, v in SECTORS.items()}
    pct_cache["all"] = daily_pct(panel, VARIANTS["all"])
    pct_cache["small"] = daily_pct(panel, VARIANTS["small"])
    pct_cache["kc50"] = daily_pct(panel, VARIANTS["kc50"])
    key_of = {"all": "all", "sz50": "SZ50", "hs300": "HS300", "small500": "SM500",
              "small1000": "SM1000", "small": "small", "kc50": "kc50"}

    ns = [int(x) for x in a.n.split(",")]
    ms = [float(x) for x in a.m.split(",")]
    rows = []
    for variant, n in itertools.product(VARIANTS, ns):
        if variant == "worst4":
            cs = {s: pct_cache[s].rolling(n).sum() for s in SECTORS}
            cum = pd.concat(cs, axis=1).min(axis=1)
        else:
            cum = pct_cache[key_of[variant]].rolling(n).sum()
        for m in ms:
            pos = build_state(cum, m)
            r = run(px, pos)
            ep = int(((cum <= m) & (cum.shift(1) > m)).sum())
            rows.append({"variant": variant, "n": n, "m": m,
                         "total_pct": round(r["total"] * 100, 1),
                         "cagr_pct": round(r["cagr"] * 100, 1),
                         "mdd_pct": round(r["mdd"] * 100, 1),
                         "flat_days_pct": round(r["flat_pct"] * 100, 1),
                         "episodes": ep,
                         **{"y%d" % y: round(v * 100, 1) for y, v in yearly(r["eq"], dates).items()}})
    df = pd.DataFrame(rows)
    df["calmar"] = (df.cagr_pct / df.mdd_pct.abs()).round(2)
    out = Path(a.csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print("\n窗口/阈值网格写入", out, "共", len(df), "组")
    print("\n买入持有基准：total %+.1f%% | cagr %+.1f%% | mdd %.1f%%"
          % (bh["total"] * 100, bh["cagr"] * 100, bh["mdd"] * 100))

    print("\n== 现行规则 all / n=14 / m=-10 ==")
    cur = df[(df.variant == "all") & (df.n == 14) & (df.m == -10)]
    print(cur.to_string(index=False))

    print("\n== 各口径在 n=14 下、阈值逐档（total% / cagr% / mdd% / 空仓占比%）==")
    t = df[df.n == 14].pivot_table(index=["variant"], columns="m",
                                   values=["total_pct", "cagr_pct", "mdd_pct", "flat_days_pct"])
    print(t.round(1).to_string())

    print("\n== 按 Calmar 排序前 12 ==")
    print(df.sort_values("calmar", ascending=False).head(12).to_string(index=False))


if __name__ == "__main__":
    main()
