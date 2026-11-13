import csv
import json
from pathlib import Path

R = Path(r"E:\Code\quant")
rows = list(csv.DictReader(open(R / "registry.csv", encoding="utf-8-sig")))
print("rows", len(rows))
tot = 0
for r in rows:
    try:
        t = int(r.get("试验数") or 0)
    except ValueError:
        t = 0
    tot += t
    print("%-22s trials=%3d  %s" % (r["id"], t, r["判定"][:40]))
print("CUMULATIVE", tot)
print("--- FROZEN_v1 ---")
print(open(R / "indicators/frozen/FROZEN_v1.json", encoding="utf-8").read())
print("--- FROZEN_v2 ---")
print(open(R / "position/live/shadow/FROZEN_v2.json", encoding="utf-8").read())
