"""T1 整理脚本：run 目录瘦身、旧 run 打包、frozen 冻结、registry 补 code_sha256。"""
import csv
import hashlib
import json
import os
import shutil
import zipfile

ROOT = r"E:\Code\quant"
RUNS = os.path.join(ROOT, "runs")

CORE = {
    "2026-11-02b_long": ["composite_meta.json", "composite_rules.csv", "composite_portfolio.csv",
                         "composite_volmanaged.csv", "composite_structure_overlay.csv", "console_long.log"],
    "2026-11-02b_long+stocks": ["composite_meta.json", "composite_rules.csv", "composite_portfolio.csv",
                                "composite_volmanaged.csv", "composite_structure_overlay.csv",
                                "console_long+stocks.log"],
    "2026-11-02b_stocks": ["composite_meta.json", "composite_rules.csv", "composite_portfolio.csv",
                           "composite_volmanaged.csv", "composite_structure_overlay.csv", "console_stocks.log"],
    "2026-11-02_P1-ROBUST-01": ["console.log", "robust_meta.json", "robust_prereg.py", "registry.csv",
                                "robust_combos_base.csv", "robust_combos_ext.csv", "robust_summary_v2.csv"],
    "2026-11-02_P1-GATE-01": ["console.log", "gate_meta.json", "gate_prereg.py", "registry.csv",
                              "gate_pools.csv", "gate_sensitivity.csv"],
}

for name, core in CORE.items():
    d = os.path.join(RUNS, name)
    extra_dir = os.path.join(d, "extra")
    files = [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]
    extra = [f for f in files if f not in core]
    if extra:
        os.makedirs(extra_dir, exist_ok=True)
        for f in extra:
            shutil.move(os.path.join(d, f), os.path.join(extra_dir, f))
    print(f"{name}: core={len([f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))])} moved={len(extra)}")

old = ["2026-10-30_base_reference", "2026-11-02_base", "2026-11-02_ext",
       "2026-11-02_long", "2026-11-02_long+stocks"]
zp = os.path.join(RUNS, "_archive_pre-fix_20261102.zip")
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    for r in old:
        for root, _, fs in os.walk(os.path.join(RUNS, r)):
            for f in fs:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, RUNS))
print(f"zip {zp} {os.path.getsize(zp)/1024:.1f} KB")

fz = os.path.join(ROOT, "frozen")
os.makedirs(fz, exist_ok=True)
for s in ("r10_prereg.py", "robust_prereg.py", "gate_prereg.py"):
    dst = os.path.join(fz, s)
    shutil.copy2(os.path.join(ROOT, s), dst)
    os.chmod(dst, 0o444)
    print("frozen", s, hashlib.sha256(open(dst, "rb").read()).hexdigest()[:16])


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


deps = ["backtest_composite.py", "backtest_structure.py", "macd_structure.py", "pools.py"]
dep_hash = hashlib.sha256("".join(sha(os.path.join(ROOT, d)) for d in deps).encode()).hexdigest()[:16]


def code_hash(script):
    return hashlib.sha256((sha(os.path.join(ROOT, script)) + dep_hash).encode()).hexdigest()[:16]


reg = os.path.join(ROOT, "registry.csv")
rows = list(csv.DictReader(open(reg, encoding="utf-8-sig")))
fields = list(rows[0].keys())
if "code_sha256" not in fields:
    fields.append("code_sha256")
for r in rows:
    if r["id"] == "P1-ROBUST-01":
        r["code_sha256"] = code_hash("robust_prereg.py")
    elif r["id"] == "P1-GATE-01":
        r["code_sha256"] = code_hash("gate_prereg.py")
    else:
        r.setdefault("code_sha256", "")
with open(reg, "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
print("deps_hash", dep_hash)
for r in rows:
    print(r["id"], r.get("code_sha256"))
