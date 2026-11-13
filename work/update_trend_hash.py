"""TREND-01 脚本修正后，更新已登记行的 code_sha256（仍属“运行前冻结”，登记时间不变）。"""
import csv
import hashlib
from pathlib import Path

R = Path(r"E:\Code\quant")
SCRIPT = R / "indicators/trend/code/trend01_prereg.py"
deps = ["indicators/code/backtest_composite.py", "indicators/code/backtest_structure.py",
        "indicators/code/macd_structure.py", "indicators/code/pools.py"]


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


dep_hash = hashlib.sha256("".join(sha(R / d) for d in deps).encode()).hexdigest()[:16]
code_hash = hashlib.sha256((sha(SCRIPT) + dep_hash).encode()).hexdigest()[:16]
print("new hash", code_hash)

for rel in ("registry.csv", "indicators/trend/prereg/registry_trend.csv"):
    p = R / rel
    rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
    for r in rows:
        if r["id"] == "TREND-01":
            r["备注"] = r["备注"].split("；脚本修正")[0] + "；脚本修正后重记哈希（运行前）"
            r["code_sha256"] = code_hash
    with open(p, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("updated", rel)
