import hashlib
import json
import os
import zipfile

root = r"E:\Code\quant"
frozen = os.path.join(root, "indicators", "frozen")
shadow = os.path.join(root, "position", "live", "shadow")
os.makedirs(frozen, exist_ok=True)

with zipfile.ZipFile(os.path.join(root, "work", "pre_reorg_20261103.zip")) as z:
    for src, dst in [("shadow/run_shadow.py", "run_shadow_v1.py"),
                     ("shadow/RULES_v1.md", "RULES_v1.md")]:
        data = z.read(src)
        open(os.path.join(frozen, dst), "wb").write(data)
        print("extracted", dst, len(data))

# universe snapshot copy (same file as current; v1 used it)
import shutil
shutil.copy2(os.path.join(shadow, "universe_v1.csv"), os.path.join(frozen, "universe_v1.csv"))

def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()

v1_files = ["run_shadow_v1.py", "RULES_v1.md", "universe_v1.csv"]
v1 = {
    "frozen_at": "2026-11-03",
    "archived_at": "2026-11-04",
    "note": "v1 runner 从 work/pre_reorg_20261103.zip 归档；当前 shadow/run_shadow.py 已是 v2，见 FROZEN_v2.json",
    "files": {f: sha(os.path.join(frozen, f)) for f in v1_files},
}
open(os.path.join(frozen, "FROZEN_v1.json"), "w", encoding="utf-8").write(
    json.dumps(v1, ensure_ascii=False, indent=2))

v2_files = ["RULES_v2.md", "rules_v2.json", "run_shadow.py", "engine.py", "universe_v1.csv"]
v2 = {
    "frozen_at": "2026-11-04",
    "note": "v2：14 自然日调仓 + 场外分档赎回费；run_shadow.py 路径/调仓逻辑改动后重新冻结",
    "files": {f: sha(os.path.join(shadow, f)) for f in v2_files},
}
open(os.path.join(shadow, "FROZEN_v2.json"), "w", encoding="utf-8").write(
    json.dumps(v2, ensure_ascii=False, indent=2))
print(json.dumps({"v1": v1, "v2": v2}, ensure_ascii=False, indent=2))
