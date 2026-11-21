import csv
import pathlib

run = pathlib.Path(r"E:\Code\quant\runs\2026-11-14_fullmkt_single_asset")
bak = pathlib.Path(r"E:\Code\quant\work\_bak_20261114_fullmkt")


def load(p):
    with open(p, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


for name in ["summary_by_group.csv", "focus_results.csv", "results_long.csv"]:
    a, b = bak / name, run / name
    if not (a.exists() and b.exists()):
        print(name, "MISSING old=%s new=%s" % (a.exists(), b.exists()))
        continue
    ra, rb = load(a), load(b)
    print(name, "rows", len(ra), "->", len(rb))
    if len(ra) != len(rb):
        print("  ROW COUNT DIFF")
        continue
    diffs = 0
    keys = list(ra[0].keys())
    for x, y in zip(ra, rb):
        for k in keys:
            if x[k] != y.get(k):
                diffs += 1
                if diffs <= 5:
                    print("  diff", x.get(keys[0]), k, x[k], "->", y.get(k))
    print("  cell diffs:", diffs)
