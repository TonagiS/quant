import hashlib
import json
from pathlib import Path

R = Path(r"E:\Code\quant")
man = json.load(open(R / "position/live/shadow/FROZEN_v2.json", encoding="utf-8"))
base = R / "position/live/shadow"
ok = True
for name, want in man["files"].items():
    p = base / name
    got = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"
    flag = "OK" if got == want else "MISMATCH"
    if got != want:
        ok = False
    print(f"{flag:9s} {name} {got[:12]} vs {want[:12]}")
print("V2_FROZEN_OK", ok)

import sys

sys.path.insert(0, str(R / "position"))
import engine as eng  # noqa: E402

print("has VolTarget:", hasattr(eng, "VolTarget"))
try:
    vt = eng.VolTarget(target=0.15, window=60)
    ctx = {}
    vt.scale(ctx)
    print("VolTarget without base_rets: NO ERROR (bad)")
except Exception as e:
    print("VolTarget without base_rets ->", type(e).__name__, str(e)[:80])
print("has _update_entries:", hasattr(eng, "_update_entries"))
