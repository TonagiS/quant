import csv, os, math
d = r"E:\Code\quant\data\cache2019"
names = {}
with open(r"E:\Code\quant\shadow\universe_v1.csv", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        names[r["code"]] = r["name"]
rows = []
for fn in os.listdir(d):
    if not fn.endswith(".csv"): continue
    code = fn[:-4]
    cl = []
    with open(os.path.join(d, fn), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try: cl.append((r["date"], float(r["close"])))
            except: pass
    if len(cl) < 130: continue
    c = [x[1] for x in cl]
    def ret(n):
        return c[-1]/c[-1-n]-1 if len(c) > n else None
    ma60 = sum(c[-60:])/60
    vol60 = 0
    rr = [c[i]/c[i-1]-1 for i in range(len(c)-60, len(c))]
    m = sum(rr)/len(rr); vol60 = math.sqrt(sum((x-m)**2 for x in rr)/len(rr))*math.sqrt(252)
    rows.append((code, names.get(code, code), cl[-1][0], ret(20), ret(60), ret(120), c[-1]/ma60-1, vol60))
rows.sort(key=lambda x: (x[4] or 0), reverse=True)
print(f"{'code':10s}{'name':16s}{'last':11s}{'r20':>8s}{'r60':>8s}{'r120':>8s}{'vsMA60':>8s}{'vol60':>7s}")
for r in rows:
    print(f"{r[0]:10s}{r[1]:16s}{r[2]:11s}" + "".join(f"{v*100:8.1f}" if v is not None else "     n/a" for v in r[3:7]) + f"{r[7]*100:7.1f}")
