"""给 vt15_5d 账本补齐"调仓日的收盘行"。

背景：paper_book5d.backfill 在调仓日只写 14:30 成交行、不写收盘行，导致
  - 调仓日下午的涨跌没单独落一行；
  - 下一行（非调仓日 close）的 day_ret 变成"上一调仓日 14:30 → 今日收盘"，口径混了。

做法：从 trades.csv 逐日重放现金与持股，用缓存收盘价重算每天收盘净值，
调仓日补 close 行，close 行的 day_ret 一律改成"上一收盘行 → 本收盘行"。
最后把结果和账本里已有的 close 行逐行对账，对不上就报错退出（不覆盖）。
"""
import os
import shutil
import sys

import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
sys.path.insert(0, r"E:\Code\quant\position\live")
from position.engine import load_panel  # noqa: E402
import paper_account as pa  # noqa: E402

NAME = "vt15_5d"
START, END = "2026-11-03", "2026-11-20"

led_path = pa.ledger_path(NAME)
tr_path = pa.trades_path(NAME)
led = pd.read_csv(led_path, encoding="utf-8-sig")
tr = pd.read_csv(tr_path, encoding="utf-8-sig")
close = load_panel("ext")

dates = [d for d in close.index if pd.Timestamp(START) <= d <= pd.Timestamp(END)]
tr["date"] = tr["date"].astype(str)

# ---- 逐日重放：现金 + 持股（成本价与净值无关，只用股数）
cash, shares = pa.INIT, {}
nav_close, cash_hist, npos_hist = {}, {}, {}
for d in dates:
    ds = str(d.date())
    for _, t in tr[tr["date"] == ds].iterrows():
        sh = int(t["shares"])
        if t["side"] == "buy":
            cash -= float(t["amount"]) + float(t["fee"])
            shares[t["code"]] = shares.get(t["code"], 0) + sh
        else:
            cash += float(t["amount"]) - float(t["fee"])
            shares[t["code"]] = shares.get(t["code"], 0) - sh
            if shares[t["code"]] == 0:
                del shares[t["code"]]
    px = {c: float(close.loc[d, c]) for c in shares if c in close.columns and pd.notna(close.loc[d, c])}
    assert len(px) == len(shares), (ds, set(shares) - set(px))
    cash_hist[ds] = cash
    npos_hist[ds] = len(shares)
    nav_close[ds] = round(cash + sum(shares[c] * px[c] for c in shares), 2)

# ---- 终态校验：重放出来的 11-19 现金/持股必须和 account.json 一致
acc = pa.load(pa.acc_path(NAME), None)
last_ds = max(nav_close)
assert abs(cash_hist[last_ds] - acc["cash"]) < 0.01, (cash_hist[last_ds], acc["cash"])
assert shares == {c: v["shares"] for c, v in acc["positions"].items()}, (shares, acc["positions"])

# ---- 对账：报告旧 close 行（当年错误地用了 14:30 快照价）与新收盘价的差
print("旧 close 行 vs 真收盘价：")
for _, r in led[led["phase"] == "close"].iterrows():
    ds = str(r["date"])
    if ds in nav_close:
        old, new = float(r["nav"]), nav_close[ds]
        print(f"  {ds} 旧 {old:,.2f} -> 新 {new:,.2f}  ({new/old-1:+.3%})")

# ---- 重写：每个交易日先 1430 行（若有）再 close 行，day_ret 按收盘链
rows, prev_close_nav = [], None
for d in dates:
    ds = str(d.date())
    old = led[led["date"].astype(str) == ds]
    r1430 = old[old["phase"] == "1430"]
    if len(r1430):
        rows.append(r1430.iloc[0].to_dict())
    nav = nav_close[ds]
    day = round(nav / prev_close_nav - 1, 6) if prev_close_nav else ""
    rows.append({"date": ds, "time": "15:00:00", "phase": "close", "book": "VT15·5日",
                 "nav": nav, "cash": round(cash_hist[ds], 2),
                 "pos_value": round(nav - cash_hist[ds], 2), "n_pos": npos_hist[ds],
                 "day_ret": day, "total_ret": round(nav / pa.INIT - 1, 6), "exposure": "",
                 "notes": "回填（收盘价）"})
    prev_close_nav = nav

out = pd.DataFrame(rows, columns=pa.LEDGER_COLS)
bak = os.path.join(os.path.dirname(led_path), "ledger.csv.bak_20261120_closefix")
shutil.copy2(led_path, bak)
out.to_csv(led_path, index=False, encoding="utf-8-sig")
print(f"备份 -> {bak}")
print(out.to_string(index=False))
