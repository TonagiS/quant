# -*- coding: utf-8 -*-
"""离线验证 run_shadow v2 的两个分支（用假行情，不碰真实 state/log）。"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant\position\live\shadow")
import run_shadow as rs  # noqa: E402

tmp = tempfile.mkdtemp(prefix="shadowv2_")
rs.STATE = os.path.join(tmp, "state.json")
rs.LOG = os.path.join(tmp, "shadow_log.csv")
rs.RAW = os.path.join(tmp, "daily")
os.makedirs(rs.RAW, exist_ok=True)

codes = [f"sh5100{i:02d}" for i in range(1, 13)]
rng = np.random.default_rng(0)
dates = pd.bdate_range(end=pd.Timestamp(date.today()), periods=300)
panel = {c: pd.Series(10 * np.cumprod(1 + rng.normal(0.0006, 0.015, len(dates))), index=dates)
         for c in codes}
last = {c: float(s.iloc[-1]) for c, s in panel.items()}

rs.universe = lambda: (codes, [])
rs.px_1430 = lambda c: last[c]
rs.hist_close = lambda c, days=400: panel[c].copy()

sys.argv = ["run_shadow.py", "--phase", "1430"]


def cyc(label):
    from io import StringIO
    import contextlib
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        rs.main()
    print(f"[{label}] {buf.getvalue().strip().splitlines()[0]}")


# 1) 首次运行（无 last_rebalance）→ 应该调仓
cyc("首次")
st = json.load(open(rs.STATE, encoding="utf-8"))
print("  调仓后 last_rebalance =", st.get("last_rebalance"), " 持仓数 =", len(st.get("last_targets", {})))
print("  due 字段 notes =", pd.read_csv(rs.LOG, encoding="utf-8-sig")["notes"].iloc[-1] or "(空=调仓)")

# 2) 同一天再跑 → 非调仓日
cyc("同日再跑")
row = pd.read_csv(rs.LOG, encoding="utf-8-sig").iloc[-1]
print("  notes =", row["notes"])
print("  targets 与上次一致 =", json.loads(row["targets_json"]) == st["last_targets"])
print("  orders 全为 hold =", all(o["side"] == "hold" for o in json.loads(row["orders_json"])))

# 3) 把上次调仓改成 20 天前 → 又该调仓
st["last_rebalance"] = str(date.today() - timedelta(days=20))
json.dump(st, open(rs.STATE, "w", encoding="utf-8"), ensure_ascii=False)
cyc("20天后")
row = pd.read_csv(rs.LOG, encoding="utf-8-sig").iloc[-1]
print("  notes =", row["notes"] or "(空=调仓)")

# 4) 价格全挂 → 不崩、保持上次目标
rs.px_1430 = lambda c: (_ for _ in ()).throw(ValueError("no bar today"))
cyc("行情全挂")
row = pd.read_csv(rs.LOG, encoding="utf-8-sig").iloc[-1]
print("  notes =", row["notes"])
print("  targets 仍保留 =", len(json.loads(row["targets_json"])) > 0)
print("\nOK: 四个分支都跑通")
