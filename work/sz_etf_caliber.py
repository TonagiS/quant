"""深市宽基口径测算：能加多少、加进去对我们的百分比有什么影响。

* 深市份额：东财 push2 stock/get f84（份），价格 f43（需 /1000）——与上交所 ETF 份额公示同量纲
  （510300 东财 f84 = 51,540,787,712 份 = 515.4 亿份 = 上交所 11-19 的 5154078.77 万份，实测一致）
* 沪市份额：data/national_team/etf_shares_hist.csv（上交所公示）
* 输出：data/national_team/sz_etf_shares.csv（当日快照，按日追加）
       runs/<日期>_sz_caliber/report.md

用法：venv\\Scripts\\python.exe work\\sz_etf_caliber.py
"""

from __future__ import annotations

import csv
import json
import re
import time
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "national_team" / "sz_etf_shares.csv"
RUN = ROOT / "runs" / f"{date.today():%Y%m%d}_sz_caliber"
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

WIDE_OK = re.compile(r"^(沪深300|中证500|中证1000|中证2000|中证A500|A500|创业板50|创业板|深证100|科创50)ETF")
WIDE_BAD = re.compile(r"人工智能|算力|新能源|软件|金融科技|电池|医药|成长|价值|红利|增强|综|200|大盘|科技|金融")


def http_json(url: str, tries: int = 3) -> dict:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.6 + i)
    raise RuntimeError(str(last))


def clist_all() -> list[dict]:
    out, page = [], 1
    while True:
        u = (
            "https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=200&po=1&np=1&fltt=2&invt=2"
            "&fid=f3&fs=b:MK0021&fields=f12,f14" % page
        )
        data = http_json(u).get("data") or {}
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
    u = "https://push2.eastmoney.com/api/qt/stock/get?secid=0.%s&fields=f57,f58,f43,f84" % code
    return http_json(u).get("data") or {}


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    cand = []
    for r in clist_all():
        code, name = str(r.get("f12") or ""), str(r.get("f14") or "")
        if not (code.startswith("15") or code.startswith("16")):
            continue
        if WIDE_OK.match(name) and not WIDE_BAD.search(name):
            cand.append((code, name))

    rows = []
    for code, name in cand:
        try:
            q = quote(code)
        except Exception:  # noqa: BLE001
            continue
        shares, px = q.get("f84"), q.get("f43")
        if not isinstance(shares, (int, float)) or shares <= 0:
            continue
        if not isinstance(px, (int, float)) or px <= 0:
            continue
        price = px / 1000.0
        rows.append({"date": date.today().isoformat(), "code": code, "name": name[:22],
                     "price": round(price, 3), "shares": int(shares),
                     "mv_yi": round(shares * price / 1e8, 1)})
        time.sleep(0.12)
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

    sz_sh = sum(r["shares"] for r in rows) / 1e8
    sz_mv = sum(r["mv_yi"] for r in rows)

    h = pd.read_csv(ROOT / "data" / "national_team" / "etf_shares_hist.csv", parse_dates=["date"])
    p = h.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4
    last = p.index[-1]
    sh_sh = float(p.loc[last].sum())
    # 沪市 9 只市值：用 11-19 收盘价
    px_map = {}
    for c in p.columns:
        u = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh%s,day,%s,%s,10,qfq"
             % (c, last.date().isoformat(), last.date().isoformat()))
        try:
            d = http_json(u, 2)["data"]["sh%d" % c]
            kl = d.get("qfqday") or d.get("day") or []
            if kl:
                px_map[c] = float(kl[-1][2])
        except Exception:  # noqa: BLE001
            pass
    sh_mv = sum(float(p.loc[last, c]) * px_map.get(c, 0) for c in p.columns)

    lines = [
        f"# 深市口径测算 {date.today().isoformat()}",
        "",
        f"- 深市宽基候选 {len(cand)} 只，取到 {len(rows)} 只",
        f"- 深市宽基合计：**{sz_sh:.1f} 亿份 / {sz_mv:.0f} 亿元**（今日快照）",
        f"- 沪市 9 只合计（{last.date()}）：**{sh_sh:.1f} 亿份 / {sh_mv:.0f} 亿元**",
        f"- 加入深市后分母变为 {sh_sh + sz_sh:.1f} 亿份（+{sz_sh / sh_sh * 100:.1f}%），"
        f"我们的单日百分比会被稀释 1/{1 + sz_sh / sh_sh:.2f}",
        "",
        "| 代码 | 名称 | 份额(亿份) | 价 | 市值(亿) |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['code']} | {r['name']} | {r['shares'] / 1e8:.2f} | {r['price']:.3f} | {r['mv_yi']:.1f} |")
    (RUN / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines[:12]))
    print(f"{'代码':<8}{'名称':<22}{'份额(亿份)':>10}{'价':>8}{'市值(亿)':>9}")
    for r in rows[:14]:
        print(f"{r['code']:<8}{r['name']:<22}{r['shares']/1e8:>10.2f}{r['price']:>8.3f}{r['mv_yi']:>9.1f}")
    print(f"\n[深市合计] {sz_sh:.1f} 亿份 / {sz_mv:.0f} 亿元")
    print(f"[沪市9只] {sh_sh:.1f} 亿份 / {sh_mv:.0f} 亿元")
    print(f"[out] {RUN}")


if __name__ == "__main__":
    main()
