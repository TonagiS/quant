# -*- coding: utf-8 -*-
"""探针：关注板块的单支仓位（11-13 收盘）"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, r"E:\Code\quant\tools")
from single_asset_daily import parts  # noqa: E402

ROOT = Path(r"E:\Code\quant\data")
POOL = ROOT / "cache_ohlc"
FULL = ROOT / "cache_ohlc_full"

WATCH = [
    ("CPO/通信", "sh515050"),
    ("半导体设备", "sz159516"),
    ("科创芯片", "sh588200"),
    ("机器人", "sh562500"),
    ("传媒", "sh512980"),
    ("动漫游戏", "sz159869"),
    ("银行", "sh512800"),
    ("猪肉/养殖", "sz159865"),
]


def series(code):
    for d in (POOL, FULL):
        p = d / (code + ".csv")
        if p.exists():
            df = pd.read_csv(p, parse_dates=["date"]).set_index("date")["close"].astype(float)
            return df, d.name
    return None, None


rows = {}
for nm, code in WATCH:
    s, src = series(code)
    if s is None:
        print(f"{nm:10s} {code} 缺数据")
        continue
    if len(s) < 300:
        print(f"{nm:10s} {code} 行数不足 {len(s)}")
        continue
    rows[code] = s

close = pd.DataFrame(rows).sort_index()
close = close.loc[:"2026-11-13"]
P = parts(close)
i = len(close) - 1
print(close.index[i])
for nm, code in WATCH:
    if code not in close.columns:
        continue
    print(f"{nm:10s} {code} pos={P['pos'][code].iloc[i]:.3f} "
          f"prev={P['pos'][code].iloc[i-1]:.3f} g1={P['g1'][code].iloc[i]:.2f} "
          f"vc={P['volcap'][code].iloc[i]:.2f} T={int(P['T'][code].iloc[i])} "
          f"close={close[code].iloc[i]:.3f}")
