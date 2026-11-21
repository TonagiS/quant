# -*- coding: utf-8 -*-
"""复现今天 14:30 的 S2 目标：候选筛选 + rm 排名 + 逆波动权重。"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"E:\Code\quant")
sys.path.insert(0, str(ROOT / "position" / "live" / "shadow"))
import engine as E

uni = pd.read_csv(ROOT / "position/live/shadow/universe_v1.csv", dtype={"code": str})
codes = [c for c in uni.loc[uni["status"] == "OK", "code"]]
nm = dict(zip(uni["code"], uni["name"]))

raw = json.load(open(ROOT / "data/daily/20261117.json", encoding="utf-8-sig"))
px_all = raw["codes"]
print("快照只数", len(px_all), "失败", len(raw.get("failed") or {}))

closes = {}
for c in codes:
    p = ROOT / "data/cache_ohlc" / f"{c}.csv"
    if p.exists():
        df = pd.read_csv(p, parse_dates=["date"])
        closes[c] = df.set_index("date")["close"].astype(float)
close = pd.DataFrame(closes).sort_index()
ts = pd.Timestamp("2026-11-17")
close.loc[ts] = {c: float(px_all.get(c, close[c].iloc[-1])) for c in close.columns}

f = E.features(close)
i = len(close) - 1
px_row = close.iloc[i]
ma60_row = f["ma60"].iloc[i]
vol_row = f["vol20"].iloc[i]
rm_row = f["rm"].iloc[i]
rk = {n: f["rk"][n].iloc[i] for n in (20, 60, 120)}
v = f["valid"].iloc[i]
cand = v & rm_row.notna() & (px_row > ma60_row)
cl = list(cand[cand].index)
print("有效", int(v.sum()), "候选", len(cl), "候选:", cl)

print("\n%-10s %-10s %6s %6s %6s %6s %8s %8s" % ("code", "name", "rm", "rk20", "rk60", "rk120", "px/ma60", "vol20"))
for c in sorted(cl, key=lambda x: -rm_row[x]):
    print("%-10s %-10s %6.3f %6.3f %6.3f %6.3f %8.3f %7.2f%%" % (
        c, str(nm.get(c, ""))[:10], rm_row[c], rk[20][c], rk[60][c], rk[120][c],
        px_row[c] / ma60_row[c], vol_row[c] * 100))

print("\n卖出三只：")
for c in ("sh510230", "sh512800", "sh510880"):
    if c not in rm_row.index:
        print("  %-10s %-8s 不在池" % (c, nm.get(c, "")))
        continue
    print("  %-10s %-8s rm=%s 候选=%s 价/MA60=%.3f" % (
        c, nm.get(c, ""), "%.3f" % rm_row[c] if np.isfinite(rm_row[c]) else "NA",
        bool(cand.get(c, False)), px_row[c] / ma60_row[c]))

t = E.targets(close)
print("\nengine.targets:", json.dumps({k: round(x, 4) for k, x in t["targets"].items()}, ensure_ascii=False))
print("n_valid", t["n_valid"], "n_cand", len(t["cand"]))
