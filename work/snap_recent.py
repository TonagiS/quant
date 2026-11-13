import json
from pathlib import Path

base = Path(r"E:\Code\quant\data\daily")
for ds in ("20261103", "20261104"):
    data = json.loads((base / f"{ds}.json").read_text(encoding="utf-8-sig"))
    codes = data.get("codes", {})
    picked = {k: codes.get(k) for k in ("sz159516", "sh512480", "sh588810", "sh510300", "sh588000")}
    print(ds, picked)
