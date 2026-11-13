"""口径核查：14 日累计用「逐日简单加总」还是「几何累计」，对六个事件差多少。"""

import collections
import csv

rows = list(csv.DictReader(open("data/national_team/etf_shares_hist.csv", encoding="utf-8-sig")))
by = collections.defaultdict(dict)
for r in rows:
    by[r["date"]][r["code"]] = float(r["shares_wan"])
dates = sorted(by)
idx = {d: i for i, d in enumerate(dates)}


def shares_at(d):
    return by[d]


def totals_at(d):
    cur = by[d]
    prev = by[dates[idx[d] - 1]]
    common = [c for c in cur if c in prev]
    return sum(prev[c] for c in common), sum(cur[c] for c in common)


def cum_arith(d, n=14):
    i = idx[d]
    s = 0.0
    for j in range(i - n + 1, i + 1):
        a, b = totals_at(dates[j])
        s += (b / a - 1) * 100
    return s


def cum_geom(d, n=14):
    i = idx[d]
    prod = 1.0
    for j in range(i - n + 1, i + 1):
        a, b = totals_at(dates[j])
        prod *= b / a
    return (prod - 1) * 100


events = ["2020-02-27", "2020-07-27", "2023-04-11", "2026-01-16", "2026-04-27", "2026-08-18"]
print("事件日      算术加总    几何累计    差(pp)")
for d in events:
    a, g = cum_arith(d), cum_geom(d)
    print(f"{d}  {a:9.2f}  {g:9.2f}  {a - g:7.2f}")

# 再统计所有阈值附近的样本：算术 ≤ -10% 的天里，几何累计的中位
hits = [d for d in dates if idx[d] >= 14 and cum_arith(d) <= -10]
gs = [cum_geom(d) for d in hits]
print()
print(f"算术 ≤ -10% 的 {len(hits)} 天里，几何累计：中位 {sorted(gs)[len(gs)//2]:.2f}%，最浅 {max(gs):.2f}%，最深 {min(gs):.2f}%")
