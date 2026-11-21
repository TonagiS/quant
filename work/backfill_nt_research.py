"""研究用份额回补：把沪市宽基 ETF 份额往前补到 2013 年（不碰生产文件）。

输出 work/nt_shares_ext.csv = 生产文件副本 + 2013-01-02 ~ 2018-12-28 的历史行。
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
SRC = ROOT / "data" / "national_team" / "etf_shares_hist.csv"
OUT = ROOT / "work" / "nt_shares_ext.csv"
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


def fetch_day(d: str):
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
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                print(f"[warn] {d}: {exc}", flush=True)
                return d, {}
            time.sleep(1.0 * (attempt + 1))
    return d, {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2013-01-01")
    ap.add_argument("--end", default="2018-12-31")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    with CAL.open(encoding="utf-8-sig", newline="") as fh:
        days = sorted({r["date"] for r in csv.DictReader(fh)
                       if a.start <= r["date"] <= a.end})
    have = set()
    if OUT.exists():
        with OUT.open(encoding="utf-8-sig", newline="") as fh:
            have = {r["date"] for r in csv.DictReader(fh)}
    else:
        OUT.write_text(SRC.read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
        with SRC.open(encoding="utf-8-sig", newline="") as fh:
            have = {r["date"] for r in csv.DictReader(fh)}
    todo = [d for d in days if d not in have]
    print(f"[plan] {a.start}~{a.end} 交易日 {len(days)}，已有 {len(have & set(days))}，待抓 {len(todo)}", flush=True)

    rows, miss, done = [], 0, 0
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for d, shares in pool.map(fetch_day, todo):
            done += 1
            if not shares:
                miss += 1
                continue
            for code, wan in sorted(shares.items()):
                rows.append({"date": d, "code": code, "name": WIDE[code], "shares_wan": round(wan, 2)})
            if done % 100 == 0:
                print(f"[ok] {d} {done}/{len(todo)} 缺 {miss}", flush=True)

    with OUT.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "code", "name", "shares_wan"])
        w.writerows(rows)
    print(f"[done] 追加 {len(rows)} 行，缺 {miss} 天 -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
