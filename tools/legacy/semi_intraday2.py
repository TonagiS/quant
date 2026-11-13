"""用新浪行情接口取实时价（东财接口今晚不稳定）。"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(r"E:\Code\quant")
CACHE = ROOT / "data" / "cache2019"
OUT = ROOT / "runs" / "20261105_semi_sector_snapshot"

SYMBOLS = {
    "sz159516": "半导体设备",
    "sh512480": "半导体",
    "sh588810": "科创芯片",
    "sh510300": "沪深300",
}


def hist_closes(prefix_code: str) -> list[float]:
    path = CACHE / f"{prefix_code}.csv"
    out = []
    with path.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(float(row["close"]))
            except Exception:  # noqa: BLE001
                continue
    return out


def main() -> int:
    url = "https://hq.sinajs.cn/list=" + ",".join(SYMBOLS)
    resp = requests.get(
        url,
        headers={
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=15,
    )
    resp.encoding = "gbk"
    text = resp.text
    print("status:", resp.status_code, "len:", len(text))

    rows = []
    for line in text.strip().splitlines():
        m = re.match(r'var hq_str_(\w+)="(.*)";', line.strip())
        if not m:
            continue
        sym, payload = m.group(1), m.group(2)
        parts = payload.split(",")
        if len(parts) < 32:
            print(sym, "short payload", len(parts))
            continue
        name = SYMBOLS.get(sym, parts[0])
        prev = float(parts[2])
        price = float(parts[3])
        amount = float(parts[9])
        closes = hist_closes(sym)
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        rows.append(
            {
                "sym": sym,
                "name": name,
                "price": price,
                "prev": prev,
                "pct": price / prev - 1,
                "amount_yi": amount / 1e8,
                "ma20": ma20,
                "ma60": ma60,
                "vs_ma20": price / ma20 - 1,
                "vs_ma60": price / ma60 - 1,
                "reclaim_ma20": price > ma20,
            }
        )

    rows.sort(key=lambda r: -abs(r["pct"]))
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "intraday.json").write_text(
        json.dumps({"as_of": stamp, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for r in rows:
        print(
            f"{r['name']} {r['sym']} {r['price']:.3f} {r['pct']*100:+.2f}% "
            f"额{r['amount_yi']:.2f}亿 vsMA20 {r['vs_ma20']*100:+.1f}% "
            f"vsMA60 {r['vs_ma60']*100:+.1f}% 收复MA20={'Y' if r['reclaim_ma20'] else 'N'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
