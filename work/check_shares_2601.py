import collections
import csv

rows = list(csv.DictReader(open("data/national_team/etf_shares_hist.csv", encoding="utf-8-sig")))
by = collections.defaultdict(dict)
for r in rows:
    by[r["date"]][r["code"]] = float(r["shares_wan"])

dates = [d for d in sorted(by) if "2026-01-10" <= d <= "2026-02-10"]
codes = sorted(by[dates[-1]])
print("date      " + " ".join(c[:6].rjust(8) for c in codes))
for d in dates:
    cells = []
    for c in codes:
        v = by[d].get(c)
        cells.append(("%8.0f" % (v / 10000)) if v is not None else "%8s" % "-")
    print(d + "  " + " ".join(cells))
print()
print("总份额（亿份）逐日：")
for d in dates:
    print(d, "%.0f" % (sum(by[d].values()) / 10000))
