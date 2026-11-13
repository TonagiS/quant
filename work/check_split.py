"""核查份额跳变是「真申购」还是「份额折算/拆分」：比对同日价格是否反向跳。"""

import collections
import csv

ROOT = "E:/Code/quant"
WATCH = {
    "510310": ["2026-10-15"],
    "510050": ["2026-10-16"],
    "510500": ["2026-10-19"],
    "588000": ["2026-10-22"],
}


def load_px(code):
    try:
        rows = list(csv.DictReader(open(f"{ROOT}/data/cache2019/sh{code}.csv", encoding="utf-8-sig")))
    except FileNotFoundError:
        return {}
    return {r["date"]: float(r["close"]) for r in rows}


sh = collections.defaultdict(dict)
for r in csv.DictReader(open(f"{ROOT}/data/national_team/etf_shares_hist.csv", encoding="utf-8-sig")):
    sh[r["date"]][r["code"]] = float(r["shares_wan"])

for code, dates in WATCH.items():
    px = load_px(code)
    print(f"=== {code} ===")
    for d in dates:
        ds = sorted(x for x in sh if code in sh[x])
        i = ds.index(d)
        prev = ds[i - 1]
        share_chg = (sh[d][code] / sh[prev][code] - 1) * 100
        p0, p1 = px.get(prev), px.get(d)
        px_chg = (p1 / p0 - 1) * 100 if p0 and p1 else float("nan")
        # 折算判定：份额涨 ~x 且 价格跌 ~1/x
        implied = (1 + share_chg / 100) * (1 + px_chg / 100) - 1 if px_chg == px_chg else float("nan")
        print(f"  {d}: 份额 {sh[prev][code]/10000:.0f} → {sh[d][code]/10000:.0f} 亿份 ({share_chg:+.2f}%) | "
              f"收盘 {p0} → {p1} ({px_chg:+.2f}%) | 份额×价格变动 {implied*100:+.2f}%")
    print()
