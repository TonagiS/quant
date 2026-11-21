"""深市宽基 ETF 份额快照。

份额来源：东财 push2 stock/get 的 f84（总份额，单位「份」）——与上交所 ETF 份额公示同量纲：
510300 东财 f84 = 51,540,787,712 份 = 515.4 亿份，和上交所 11-19 的 5154078.77 万份一致。
（注意 clist 批量接口里的 f84 不是份额，别用。）

输出：data/national_team/sz_etf_shares.csv   date,code,name,price,shares,mv_yi
用法：venv\\Scripts\\python.exe work\\sz_etf_snapshot.py
"""

from __future__ import annotations

import csv
import json
import re
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "national_team" / "sz_etf_shares.csv"
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

WIDE_KEY = re.compile(r"沪深300|中证500|中证1000|中证2000|A500|创业板|深证100|科创50|上证50")
CORE = {
    "159919": "沪深300(嘉实)",
    "159922": "中证500(嘉实)",
    "159845": "中证1000(华夏)",
    "159901": "深证100(易方达)",
    "159915": "创业板(易方达)",
}


def clist_all() -> list[dict]:
    out, page = [], 1
    while True:
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=200&po=1&np=1&fltt=2&invt=2"
            "&fid=f3&fs=b:MK0021&fields=f12,f14" % page
        )
        req = urllib.request.Request(url, headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace"))
        data = d.get("data") or {}
        diff = data.get("diff") or []
        if not diff:
            break
        out.extend(diff)
        if len(out) >= (data.get("total") or 0):
            break
        page += 1
        time.sleep(0.25)
    return out


def quote(code: str) -> dict:
    url = (
        "https://push2.eastmoney.com/api/qt/stock/get?secid=0.%s&fields=f57,f58,f43,f84" % code
    )
    req = urllib.request.Request(url, headers=UA)
    d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace"))
    return d.get("data") or {}


def main() -> None:
    allsz = clist_all()
    cand = [
        (str(r["f12"]), str(r["f14"]))
        for r in allsz
        if (str(r["f12"]).startswith("15") or str(r["f12"]).startswith("16"))
        and WIDE_KEY.search(str(r["f14"]) or "")
    ]
    seen, rows = set(), []
    for code, name in cand:
        if code in seen:
            continue
        seen.add(code)
        try:
            q = quote(code)
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
            continue
        shares, price = q.get("f84"), q.get("f43")
        if not isinstance(shares, (int, float)) or shares <= 0:
            continue
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        rows.append(
            {
                "date": date.today().isoformat(),
                "code": code,
                "name": name[:24],
                "price": round(price / 100.0, 3),
                "shares": int(shares),
                "mv_yi": round(shares * price / 100.0 / 1e8, 2),
            }
        )
        time.sleep(0.15)
    rows.sort(key=lambda r: -r["mv_yi"])

    old = []
    if OUT.exists():
        with OUT.open(encoding="utf-8-sig", newline="") as fh:
            old = [r for r in csv.DictReader(fh) if r.get("date") != date.today().isoformat()]
    with OUT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "code", "name", "price", "shares", "mv_yi"])
        w.writeheader()
        for r in old:
            w.writerow({k: r.get(k, "") for k in ["date", "code", "name", "price", "shares", "mv_yi"]})
        w.writerows(rows)

    print(f"[ok] {date.today()} 深市宽基候选 {len(cand)} 只，取到 {len(rows)} 只")
    print(f"{'代码':<8}{'名称':<20}{'份额(亿份)':>10}{'价':>8}{'市值(亿)':>10}")
    for r in rows:
        print(f"{r['code']:<8}{r['name']:<20}{r['shares']/1e8:>10.2f}{r['price']:>8.3f}{r['mv_yi']:>10.1f}")
    print(f"\n[宽基合计] {sum(r['shares'] for r in rows)/1e8:.1f} 亿份 / {sum(r['mv_yi'] for r in rows):.0f} 亿元")
    print("[核心5只]", ", ".join(f"{c}={[r for r in rows if r['code']==c][0]['shares']/1e8:.1f}亿份" for c in CORE if any(r["code"] == c for r in rows)))


if __name__ == "__main__":
    main()
