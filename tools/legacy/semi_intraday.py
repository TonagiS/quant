"""盘中实时快照：取三条线 + 沪深300 的实时价，和 11-04 收盘的均线对比。"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import akshare as ak

ROOT = Path(r"E:\Code\quant")
CACHE = ROOT / "data" / "cache2019"
OUT = ROOT / "runs" / "20261105_semi_sector_snapshot"
CODES = {
    "159516": "半导体设备",
    "512480": "半导体",
    "588810": "科创芯片",
    "510300": "沪深300",
}


def hist_closes(code6: str) -> list[float]:
    prefix = "sh" if code6.startswith(("5", "6")) else "sz"
    path = CACHE / f"{prefix}{code6}.csv"
    out = []
    with path.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(float(row["close"]))
            except Exception:  # noqa: BLE001
                continue
    return out


def main() -> int:
    df = ak.fund_etf_spot_em()
    import pandas as pd

    df = df[["代码", "名称", "最新价", "涨跌幅", "成交额", "昨收"]]
    df = df[df["代码"].isin(CODES)]
    rows = []
    for _, r in df.iterrows():
        code = str(r["代码"])
        price = float(r["最新价"])
        closes = hist_closes(code)
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        amt20 = None
        rows.append(
            {
                "code": code,
                "name": CODES[code],
                "price": price,
                "pct": float(r["涨跌幅"]) / 100,
                "amount_yi": float(r["成交额"]) / 1e8,
                "prev": float(r["昨收"]),
                "ma20": ma20,
                "ma60": ma60,
                "vs_ma20": price / ma20 - 1,
                "vs_ma60": price / ma60 - 1,
                "reclaim_ma20": price > ma20,
            }
        )
    rows.sort(key=lambda x: -abs(x["pct"]))
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    (OUT / "intraday.json").write_text(
        json.dumps({"as_of": stamp, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for r in rows:
        print(
            f"{r['name']} {r['code']} {r['price']:.3f} {r['pct']*100:+.2f}% "
            f"额 {r['amount_yi']:.2f}亿 | vsMA20 {r['vs_ma20']*100:+.1f}% "
            f"vsMA60 {r['vs_ma60']*100:+.1f}% 收复MA20={'Y' if r['reclaim_ma20'] else 'N'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
