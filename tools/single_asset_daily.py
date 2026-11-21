"""单支增减仓触发扫描（14:30 播报第二段用）。

口径与 `work/single_asset_final.py` 定稿一致：
    pos = G1 × 波动上限 × 趋势衰减
      G1    跌破 20 日动量(0%) → 年线上行则半仓 0.5，否则清仓 0；回到 +1% 复原 1.0
      波动上限 clip(0.15/vol60, 0.4, 1.0)
      趋势衰减 T0/T1=1.0、T2=0.85、T3=0.6、T4=0.5（close vs MA200 + MA200 60 日斜率 ±2%）

用法：
    from single_asset_daily import triggers
    rows = triggers("2026-11-13", px={"sh510300": 4.12, ...})
    # -> [{'code','name','prev','now','delta','kind','reason'}, ...]
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "cache_ohlc"
DATA_FULL = ROOT / "data" / "cache_ohlc_full"     # 全市场缓存，补池外标的（科创芯片、养殖等）
UNIVERSE = ROOT / "position" / "live" / "shadow" / "universe_v1.csv"
TD = 252
TAPER = np.array([1.0, 1.0, 0.85, 0.6, 0.5])

# 播报第二段固定展示的关注板块（label, code）
WATCH = [
    ("CPO通信", "sh515050"),
    ("半导体设备", "sz159516"),
    ("科创芯片", "sh588200"),
    ("机器人", "sh562500"),
    ("传媒", "sh512980"),
    ("动漫游戏", "sz159869"),
    ("银行", "sh512800"),
    ("猪肉养殖", "sz159865"),
]


def names_map() -> dict[str, str]:
    out = {}
    if UNIVERSE.exists():
        with UNIVERSE.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                out[r["code"]] = r.get("name") or r["code"]
    full = ROOT / "data" / "etf_universe.csv"      # 更全的 ETF 名单，补 universe_v1 里没有的
    if full.exists():
        with full.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                out.setdefault(r["code"], r.get("name") or r["code"])
    return out


def guess_kind(code: str) -> str:
    """_meta.csv 缺 kind 列时的兜底判别（2026-11-18 事故：cache_refresh 重写 meta 漏了 kind）。"""
    if code.startswith(("sh5", "sz15", "sz16")):
        return "etf"
    if code.startswith(("sh000", "sz399")):
        return "index"
    return "stock"


def load_close(only_etf: bool = True) -> pd.DataFrame:
    kinds = {}
    meta = DATA / "_meta.csv"
    if only_etf and meta.exists():
        m = pd.read_csv(meta)
        if "kind" in m.columns:
            kinds = {c: k for c, k in zip(m["code"], m["kind"]) if isinstance(k, str) and k}
    closes = {}
    for p in sorted(DATA.glob("*.csv")):
        if p.stem.startswith("_"):
            continue
        if only_etf and (kinds.get(p.stem) or guess_kind(p.stem)) != "etf":
            continue
        df = pd.read_csv(p, parse_dates=["date"])
        if len(df) < 300:
            continue
        closes[p.stem] = df.set_index("date")["close"].astype(float)
    return pd.DataFrame(closes).sort_index()


def load_codes(codes, dirs=(DATA,)) -> pd.DataFrame:
    """按指定代码从若干缓存目录凑一张收盘价表（先命中先用）。"""
    closes = {}
    for c in codes:
        for d in dirs:
            p = Path(d) / (c + ".csv")
            if p.exists():
                df = pd.read_csv(p, parse_dates=["date"])
                if len(df) >= 300:
                    closes[c] = df.set_index("date")["close"].astype(float)
                break
    return pd.DataFrame(closes).sort_index()


def parts(close: pd.DataFrame) -> dict[str, pd.DataFrame]:
    ret = close.pct_change(fill_method=None)
    mom20 = close / close.shift(20) - 1.0
    ma200 = close.rolling(200, min_periods=200).mean()
    vol60 = ret.rolling(60, min_periods=42).std() * np.sqrt(TD)

    bull = (close > ma200) & (ma200 > ma200.shift(60))
    st = np.where(mom20 < 0.0, 0.0, np.where(mom20 > 0.01, 1.0, np.nan))
    state = pd.DataFrame(st, index=close.index, columns=close.columns).ffill().fillna(1.0)
    g1 = pd.DataFrame(np.where(state.to_numpy() > 0, 1.0,
                               np.where(bull.to_numpy(), 0.5, 0.0)),
                      index=close.index, columns=close.columns)
    volcap = (0.15 / vol60).clip(0.4, 1.0).fillna(1.0)

    slope = ma200.pct_change(60)
    above = close > ma200
    T = np.select([above & (slope > 0.02), above & (slope >= -0.02), above, (~above) & (slope >= -0.02)],
                  [0, 1, 2, 3], default=4)
    T = pd.DataFrame(T, index=close.index, columns=close.columns).where(ma200.notna(), 1)
    taper = pd.DataFrame(TAPER[T.to_numpy(dtype=int)], index=close.index, columns=close.columns)
    pos = (g1 * volcap * taper).clip(0.0, 1.0).where(ma200.notna(), 0.0)
    return dict(g1=g1, volcap=volcap, taper=taper, T=T, pos=pos)


def triggers(dstr: str, px: dict[str, float] | None = None, min_delta: float = 0.05,
             max_rows: int = 8) -> list[dict]:
    close = load_close()
    px = px or {}
    ts = pd.Timestamp(dstr)
    if (close.index == ts).any():          # 缓存里已有该日：截断到该日，别用后面的数据
        close = close.loc[:ts]
    elif px:                                # 当天收盘数据还没进缓存：用 14:30 快照价补一行
        row = {}
        for c in close.columns:
            v = px.get(c)
            row[c] = float(v) if v else float(close[c].iloc[-1])
        close.loc[ts] = row
        close = close.sort_index().loc[:ts]
    P = parts(close)
    i = len(close) - 1
    nm = names_map()
    out = []
    for c in close.columns:
        prev = float(P["pos"][c].iloc[i - 1]) if i >= 1 else 0.0
        now = float(P["pos"][c].iloc[i])
        d = now - prev
        if abs(d) < min_delta and not (prev > 0 and now == 0):
            continue
        t_prev = int(P["T"][c].iloc[i - 1]) if i >= 1 else int(P["T"][c].iloc[i])
        t_now = int(P["T"][c].iloc[i])
        g_prev = float(P["g1"][c].iloc[i - 1]) if i >= 1 else 1.0
        g_now = float(P["g1"][c].iloc[i])
        v_prev = float(P["volcap"][c].iloc[i - 1]) if i >= 1 else 1.0
        v_now = float(P["volcap"][c].iloc[i])

        if now == 0.0 and prev > 0.0:
            reason = "跌破20日动量且年线不强 → 清仓"
            kind = "清仓"
        elif now > 0.0 and prev == 0.0:
            reason = "重回20日动量上方 → 建仓"
            kind = "建仓"
        elif g_now != g_prev:
            reason = "跌破20日动量 → 年线上行，减半" if g_now < g_prev else "回到+1%上方 → 复原"
            kind = "减仓" if g_now < g_prev else "加仓"
        elif t_now != t_prev:
            reason = f"趋势档 T{t_prev}→T{t_now}"
            kind = "加仓" if d > 0 else "减仓"
        elif abs(v_now - v_prev) >= 0.03:
            reason = f"波动率变化 → 仓位上限 {v_prev*100:.0f}%→{v_now*100:.0f}%"
            kind = "加仓" if d > 0 else "减仓"
        else:
            reason = "仓位微调"
            kind = "加仓" if d > 0 else "减仓"
        out.append(dict(code=c, name=nm.get(c, c), prev=round(prev, 3), now=round(now, 3),
                        delta=round(d, 3), kind=kind, reason=reason, T=t_now))
    out.sort(key=lambda r: -abs(r["delta"]))
    return out[:max_rows]


def _label(name: str, code: str) -> str:
    """名称里已经带了代码（如 'AI应用(159819)'）就不再补一遍。"""
    short = code[2:] if len(code) > 2 else code
    return name if short in name else f"{name}({short})"


def top_tier(dstr: str, px: dict[str, float] | None = None, tiers=(0, 1), n: int = 3,
             pool=None) -> dict[int, list[dict]]:
    """全池按 T 档取前 n（同档按仓位降序）。pool 缺省取 px 的代码，即当日扫描池。

    返回 {0: [{'code','name','label','pos'}, ...], 1: [...]}，缺数据时给空 list。
    """
    px = px or {}
    codes = list(pool if pool is not None else px.keys())
    if not codes:
        return {}
    close = load_codes(codes, (DATA, DATA_FULL))
    if close.empty:
        return {}
    ts = pd.Timestamp(dstr)
    if (close.index == ts).any():
        close = close.loc[:ts]
    elif px:
        row = {}
        for c in close.columns:
            v = px.get(c)
            row[c] = float(v) if v else float(close[c].iloc[-1])
        close.loc[ts] = row
        close = close.sort_index().loc[:ts]
    P = parts(close)
    i = len(close) - 1
    nm = names_map()
    out: dict[int, list[dict]] = {}
    for t in tiers:
        rows = []
        for c in close.columns:
            if int(P["T"][c].iloc[i]) != t:
                continue
            pos = float(P["pos"][c].iloc[i]) if pd.notna(P["pos"][c].iloc[i]) else 0.0
            rows.append(dict(code=c, name=nm.get(c, c), label=_label(nm.get(c, c), c),
                             pos=round(pos, 2)))
        rows.sort(key=lambda r: -r["pos"])
        out[int(t)] = rows[:n]
    return out


def watch(dstr: str, px: dict[str, float] | None = None, watch_list=None,
          min_delta: float = 0.05) -> list[dict]:
    """关注板块按单支规则当前该持有的仓位（不筛变动，全列）。

    口径同 triggers()：池内的用 cache_ohlc，池外的（科创芯片、养殖）用 cache_ohlc_full。
    """
    watch_list = watch_list or WATCH
    px = px or {}
    close = load_codes([c for _, c in watch_list], (DATA, DATA_FULL))
    if close.empty:
        return []
    ts = pd.Timestamp(dstr)
    if (close.index == ts).any():
        close = close.loc[:ts]
    elif px:
        row = {}
        for c in close.columns:
            v = px.get(c)
            row[c] = float(v) if v else float(close[c].iloc[-1])
        close.loc[ts] = row
        close = close.sort_index().loc[:ts]
    P = parts(close)
    i = len(close) - 1
    out = []
    for label, c in watch_list:
        if c not in close.columns:
            out.append(dict(code=c, label=label, pos=None, prev=None, delta=None, T=None))
            continue
        now = float(P["pos"][c].iloc[i])
        prev = float(P["pos"][c].iloc[i - 1]) if i >= 1 else now
        out.append(dict(code=c, label=label, pos=round(now, 2), prev=round(prev, 2),
                        delta=round(now - prev, 3), T=int(P["T"][c].iloc[i])))
    return out
