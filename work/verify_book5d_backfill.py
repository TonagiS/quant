"""验证修好的 paper_book5d.backfill：在临时目录重跑一遍，和修好的真账本对账。"""
import os
import shutil
import sys

import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
sys.path.insert(0, r"E:\Code\quant\position\live")
import paper_account as pa  # noqa: E402
import paper_book5d as b5  # noqa: E402

TMP = r"E:\Code\quant\work\_paper_test5d"
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(os.path.join(TMP, "vt15_5d"))
pa.BOOKS_DIR = TMP
b5.backfill("2026-11-03")

REAL = r"E:\Code\quant\position\live\paper\books\vt15_5d"
new = pd.read_csv(os.path.join(TMP, "vt15_5d", "ledger.csv"), encoding="utf-8-sig")
old = pd.read_csv(os.path.join(REAL, "ledger.csv"), encoding="utf-8-sig")
key = ["date", "phase"]
m = old.merge(new, on=key, suffixes=("_old", "_new"))
bad = []
for _, r in m.iterrows():
    if abs(float(r["nav_old"]) - float(r["nav_new"])) > 0.02:
        bad.append((r["date"], r["phase"], r["nav_old"], r["nav_new"]))
print(f"对齐 {len(m)} 行，净值不一致 {len(bad)} 行")
for b in bad:
    print("  MISMATCH", b)
d_old = pd.to_numeric(m["day_ret_old"], errors="coerce")
d_new = pd.to_numeric(m["day_ret_new"], errors="coerce")
dd = (d_old - d_new).abs()
print("day_ret 最大差", None if dd.isna().all() else round(float(dd.max()), 6))

tr_new = pd.read_csv(os.path.join(TMP, "vt15_5d", "trades.csv"), encoding="utf-8-sig")
tr_old = pd.read_csv(os.path.join(REAL, "trades.csv"), encoding="utf-8-sig")
print("成交笔数 new/old", len(tr_new), len(tr_old),
      "金额一致", abs(tr_new["amount"].sum() - tr_old["amount"].sum()) < 0.05)
print(new.tail(4).to_string(index=False))
