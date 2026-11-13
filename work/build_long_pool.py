"""合成"长池"：cache_ohlc_long 里的 69 只 ETF（剔除货币 ETF）+ cache_ohlc 的指数/蓝筹。

货币 ETF（511990/511880/159001/159003）近似现金、波动≈0，会让 volcap 顶满、卡玛虚高，剔除。
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "data" / "cache_ohlc"
NEW = ROOT / "data" / "cache_ohlc_long"
DROP = {"sh511990", "sh511880", "sz159001", "sz159003"}

m_old = pd.read_csv(OLD / "_meta.csv")
rows = []
for _, r in m_old[m_old["kind"] != "etf"].iterrows():
    src, dst = OLD / f"{r['code']}.csv", NEW / f"{r['code']}.csv"
    if src.exists() and not dst.exists():
        shutil.copyfile(src, dst)
    rows.append(dict(code=r["code"], kind=r["kind"], source="ohlc", rows=r["rows"],
                     start=r["start"], end=r["end"], cache_added=r["cache_added"], ratio=r["ratio"]))

m_new = pd.read_csv(NEW / "_meta.csv")
m_new = m_new[~m_new["code"].isin(DROP)]
for _, r in m_new.iterrows():
    if not (NEW / f"{r['code']}.csv").exists():
        continue
    rows.append(dict(code=r["code"], kind="etf", source="long", rows=r["rows"],
                     start=r["start"], end=r["end"], cache_added=r["cache_added"], ratio=1.0))

meta = pd.DataFrame(rows).drop_duplicates("code")
meta.to_csv(NEW / "_meta.csv", index=False, encoding="utf-8-sig")
print(f"长池 {len(meta)} 只：ETF {len(meta[meta['kind'] == 'etf'])}，"
      f"指数 {len(meta[meta['kind'] == 'index'])}，蓝筹 {len(meta[meta['kind'] == 'stock'])}")
print("上市年份分布：")
print(meta[meta["kind"] == "etf"]["start"].str[:4].value_counts().sort_index().to_string())
