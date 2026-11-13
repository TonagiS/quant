"""回补沪市宽基 ETF 每日份额 + 指数日线，供「历史对比」用。

* ETF 份额：上交所 COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L + STAT_DATE 参数
  （一个日期一次请求，返回当日全市场沪市 ETF，约 900 行，只保留 9 只宽基）
* 指数日线：东方财富 push2his kline（sh000001 / sh000300 / sh000905）

输出
----
data/national_team/etf_shares_hist.csv   date,code,name,shares_wan
data/national_team/index_daily.csv       date,code,open,close,high,low,volume,amount

幂等：已存在的 (date,code) 不重复写入。

用法
----
venv\\Scripts\\python.exe tools\\nt_shares_hist.py --start 2026-05-15
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "national_team"
SHARES_CSV = OUT / "etf_shares_hist.csv"
INDEX_CSV = OUT / "index_daily.csv"

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
SSE_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "http://www.sse.com.cn/"}
EM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
SINA_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}

INDEXES = {"sh000001": "1.000001", "sh000300": "1.000300", "sh000905": "1.000905"}


def http_json(url: str, headers: dict, tries: int = 4) -> dict:
    """GET -> json，带重试（东财偶尔掐连接）。"""
    last = ""
    for i in range(tries):
        try:
            req = requests.get(url, headers=headers, timeout=30)
            return req.json()
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def fetch_index_sina(beg: str, end: str, datalen: int = 400) -> list[dict]:
    """新浪日线（东财 push2his 常掐连接，这里用新浪兜底）。"""
    rows = []
    for code in INDEXES:
        url = (
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol={code}&scale=240&ma=no&datalen={datalen}"
        )
        d = http_json(url, SINA_HEADERS)
        for r in d:
            if not (beg <= r["day"] <= end):
                continue
            rows.append(
                {
                    "date": r["day"],
                    "code": code,
                    "open": r["open"],
                    "close": r["close"],
                    "high": r["high"],
                    "low": r["low"],
                    "volume": r.get("volume", ""),
                    "amount": "",
                }
            )
    return rows


def fetch_index_daily(beg: str, end: str) -> list[dict]:
    try:
        rows = []
        for code, secid in INDEXES.items():
            url = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
                "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
                "&klt=101&fqt=0&beg={beg}&end={end}&lmt=2000"
            ).format(secid=secid, beg=beg.replace("-", ""), end=end.replace("-", ""))
            d = http_json(url, EM_HEADERS, tries=2)
            for line in (d.get("data") or {}).get("klines") or []:
                f = line.split(",")
                rows.append(
                    {
                        "date": f[0],
                        "code": code,
                        "open": f[1],
                        "close": f[2],
                        "high": f[3],
                        "low": f[4],
                        "volume": f[5],
                        "amount": f[6],
                    }
                )
        if rows:
            return rows
    except Exception as exc:
        print(f"[warn] 东财不可用，改用新浪：{exc}", file=sys.stderr)
    return fetch_index_sina(beg, end)


def fetch_sse_day(stat_date: str) -> tuple[str, dict[str, float]]:
    url = SSE_BASE + "&STAT_DATE=" + stat_date
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=SSE_HEADERS)
            body = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
            d = json.loads(body)
            rows = d.get("result") or []
            out = {}
            for r in rows:
                code = str(r.get("SEC_CODE") or "").strip()
                if code not in WIDE:
                    continue
                try:
                    out[code] = float(r.get("TOT_VOL") or 0)
                except (TypeError, ValueError):
                    continue
            return stat_date, out
        except Exception as exc:  # 网络抖动就重试
            if attempt == 3:
                print(f"[warn] {stat_date}: {exc}", file=sys.stderr)
                return stat_date, {}
            time.sleep(1.5 * (attempt + 1))
    return stat_date, {}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def append_rows(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-05-15")
    ap.add_argument("--end", default=time.strftime("%Y-%m-%d"))
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    idx = fetch_index_daily(args.start, args.end)
    old_idx = {(r["date"], r["code"]) for r in read_csv(INDEX_CSV)}
    new_idx = [r for r in idx if (r["date"], r["code"]) not in old_idx]
    append_rows(INDEX_CSV, ["date", "code", "open", "close", "high", "low", "volume", "amount"], new_idx)
    print(f"[ok] 指数 {len(idx)} 行，新增 {len(new_idx)}")

    trading_days = sorted({r["date"] for r in idx if r["code"] == "sh000001"})
    have = {r["date"] for r in read_csv(SHARES_CSV)}
    todo = [d for d in trading_days if d not in have]
    print(f"[ok] 交易日 {len(trading_days)} 天，已有 {len(have)}，待抓 {len(todo)}")

    rows = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for stat_date, shares in pool.map(fetch_sse_day, todo):
            if not shares:
                print(f"[miss] {stat_date}", file=sys.stderr)
                continue
            for code, wan in sorted(shares.items()):
                rows.append(
                    {"date": stat_date, "code": code, "name": WIDE[code], "shares_wan": round(wan, 2)}
                )
            print(f"[ok] {stat_date} {len(shares)} 只")
    append_rows(SHARES_CSV, ["date", "code", "name", "shares_wan"], rows)
    print(f"[done] 写入 {SHARES_CSV.name} {len(rows)} 行；{INDEX_CSV.name}")


if __name__ == "__main__":
    main()
