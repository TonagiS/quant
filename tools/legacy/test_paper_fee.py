# -*- coding: utf-8 -*-
"""离线验证 paper_account 的赎回费与最短持有逻辑（不碰真实账户）。"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from io import StringIO
import contextlib

sys.path.insert(0, r"E:\Code\quant\position\live")
import paper_account as pa  # noqa: E402

tmp = tempfile.mkdtemp(prefix="paperfee_")
pa.PAPER = tmp
pa.ACC = os.path.join(tmp, "account.json")
pa.TRADES = os.path.join(tmp, "trades.csv")
pa.LEDGER = os.path.join(tmp, "ledger.csv")
pa.RAW = os.path.join(tmp, "daily")
pa.STATE = os.path.join(tmp, "state.json")
os.makedirs(pa.RAW, exist_ok=True)

today = date.today()
px = {"sh510230": 1.40, "sh512800": 0.90}
json.dump({"codes": px, "failed": {}}, open(os.path.join(pa.RAW, f"{today:%Y%m%d}.json"), "w"))
# 目标只留 512800 → 510230 该卖
json.dump({"last_targets": {"sh512800": 0.6}}, open(pa.STATE, "w", encoding="utf-8"))

base = {"initial": 10000.0, "cash": 500.0, "start": str(today - timedelta(days=30)),
        "positions": {"sh510230": {"shares": 2000, "cost": 1.30}}, "last_px": px}


def run_case(label, open_date):
    acc = json.loads(json.dumps(base))
    acc["positions"]["sh510230"]["open_date"] = str(open_date)
    json.dump(acc, open(pa.ACC, "w", encoding="utf-8"), ensure_ascii=False)
    for f in (pa.TRADES, pa.LEDGER):
        if os.path.exists(f):
            os.remove(f)
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        pa.execute()
    out = buf.getvalue()
    import pandas as pd
    sold = False
    if os.path.exists(pa.TRADES):
        t = pd.read_csv(pa.TRADES, encoding="utf-8-sig")
        sold = bool((t["code"] == "sh510230").any())
    print(f"[{label}] open_date={open_date} 持有={(today - open_date).days}天 → 卖出={sold}")
    for line in out.splitlines():
        if "顺延" in line or "赎回费" in line:
            print("   ", line.strip())
    return sold


print("== 情况1：持有 3 天（不满 7 天）应顺延，不吃 1.5%")
c1 = run_case("持有3天", today - timedelta(days=3))

print("== 情况2：持有 10 天（≥7 天）应卖出，且只收 0.5% 赎回费 + 5bp 佣金")
c2 = run_case("持有10天", today - timedelta(days=10))

amt = 2000 * 1.40
print(f"   期望费用 = 0.5%×{amt:.0f} + 0.05%×{amt:.0f} = {amt*0.0055:.2f} 元")

print("== 情况3：持有 35 天（≥30 天）应按 0.15% 档")
c3 = run_case("持有35天", today - timedelta(days=35))
print(f"   期望费用 = 0.15%×{amt:.0f} + 0.05%×{amt:.0f} = {amt*0.002:.2f} 元")
print("\n结论:", "通过" if (not c1 and c2 and c3) else "不通过")
