import urllib.request, json, csv
from pathlib import Path

OUT = Path(r"E:\Code\quant\data\national_team")
codes = {"sh000001": "sh000001", "sh000300": "sh000300", "sh000905": "sh000905"}
rows = {}
for code in codes:
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,2019-01-01,2026-11-16,2000,qfq" % code)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace"))
    kl = d["data"][code].get("day") or d["data"][code].get("qfqday")
    print(code, len(kl), kl[0][0], kl[-1][0], kl[-1][:3])
    for k in kl:
        rows[(k[0], code)] = float(k[2])

with (OUT / "index_close.csv").open("w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["date", "code", "close"])
    for (dt, code), close in sorted(rows.items()):
        w.writerow([dt, code, round(close, 3)])
print("rows", len(rows))
