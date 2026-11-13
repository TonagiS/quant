"""把沪市宽基 ETF 份额历史一次性回补到 2019 年（上交所接口支持任意 STAT_DATE）。

交易日历取自 data/cache2019/sh510050.csv（2019-01-02 起）。

输出 data/national_team/etf_shares_hist.csv（幂等，已存在的日期跳过）
用法 venv\\Scripts\\python.exe work/backfill_shares_sse.py --start 2019-01-01
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "national_team" / "etf_shares_hist.csv"
CAL = ROOT / "data" / "cache2019" / "sh510050.csv"

WIDE = {
    "510050": "上证50ETF",
    "510300": "沪深300ETF(华泰柏瑞)",
    "510310": "沪深300ETF(易方达)",
    "510330": "沪深300ETF(华夏)",
    "510500": "中证500ETF(南方)",
    "512500": "中证500ETF(华夏)",
    "512100": "中证1000ETF(南方)",
    "588000": "科创50ETF(华夏)",
    "588080": "科创板50ETF(易方达)",
}

SSE_BASE = (
    "http://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
    "&pageHelp.pageSize=3000&pageHelp.pageNo=1&pageHelp.beginPage=1&pageHelp.cacheSize=1&pageHelp.endPage=1"
)
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "http://www.sse.com.cn/"}


def calendar(start: str) -> list[str]:
    with CAL.open(encoding="utf-8-sig", newline="") as fh:
        dates = [r["date"] for r in csv.DictReader(fh) if r["date"] >= start]
    return sorted(set(dates))


def fetch_day(d: str) -> tuple[str, dict[str, float]]:
    for attempt in range(4):
        try:
            req = urllib.request.Request(SSE_BASE + "&STAT_DATE=" + d, headers=HEADERS)
            body = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
            rows = json.loads(body).get("result") or []
            out = {}
            for r in rows:
                code = str(r.get("SEC_CODE") or "").strip()
                if code in WIDE:
                    try:
                        out[code] = float(r.get("TOT_VOL") or 0)
                    except (TypeError, ValueError):
                        pass
            return d, out
        except Exception as exc:
            if attempt == 3:
                print(f"[warn] {d}: {exc}", file=sys.stderr)
                return d, {}
            time.sleep(1.2 * (attempt + 1))
    return d, {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    days = calendar(args.start)
    have: set[str] = set()
    if OUT.exists():
        with OUT.open(encoding="utf-8-sig", newline="") as fh:
            have = {r["date"] for r in csv.DictReader(fh)}
    todo = [d for d in days if d not in have]
    print(f"[plan] 交易日 {len(days)}，已有 {len(have)}，待抓 {len(todo)}", flush=True)
    if not todo:
        return

    rows = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for d, shares in pool.map(fetch_day, todo):
            done += 1
            if not shares:
                print(f"[miss] {d} ({done}/{len(todo)})", flush=True)
                continue
            for code, wan in sorted(shares.items()):
                rows.append({"date": d, "code": code, "name": WIDE[code], "shares_wan": round(wan, 2)})
            if done % 50 == 0:
                print(f"[ok] {d} 累计 {done}/{len(todo)}", flush=True)

    exists = OUT.exists()
    with OUT.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "code", "name", "shares_wan"])
        if not exists:
            w.writeheader()
        w.writerows(rows)
    print(f"[done] 追加 {len(rows)} 行 -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
