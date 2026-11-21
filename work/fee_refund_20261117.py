# -*- coding: utf-8 -*-
"""2026-11-17 赎回费口径更正后的差额退回。

原口径：持有 7~30 天按 0.5% 收赎回费。实际场外 C 类份额 >=7 天为 0（见 paper/fees.json）。
今天两笔红利卖出生效在新口径之前，多收的钱退回账本：
  - 差额 = 卖出金额 * 0.005，记进 books/<book>/adjustments.csv，不重写 trades.csv（保留原始成交记录）
  - 当日 close 台账行的 nav/cash/day_ret/total_ret 按退回后重算
"""
import csv
import json
from datetime import date
from pathlib import Path

BOOKS = Path(r"E:\Code\quant\position\live\paper\books")
DS = "2026-11-17"
OLD_MID = 0.005
ADJ_COLS = ["date", "book", "amount", "reason"]


def rd(p):
    with p.open(encoding="utf-8-sig", newline="") as fh:
        r = csv.DictReader(fh)
        return r.fieldnames, list(r)


def wr(p, cols, rows):
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for x in rows:
            w.writerow(x)


def main():
    for book in ("s2", "vt15"):
        b = BOOKS / book
        _, trades = rd(b / "trades.csv")
        sells = [t for t in trades if t["date"] == DS and t["side"] == "sell"]
        refund = round(sum(float(t["amount"]) * OLD_MID for t in sells), 2)
        if refund <= 0:
            print(f"[{book}] 今日无卖出，跳过")
            continue

        acc = json.load(open(b / "account.json", encoding="utf-8"))
        acc["cash"] = round(acc["cash"] + refund, 2)
        json.dump(acc, open(b / "account.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)

        ap = b / "adjustments.csv"
        rows = []
        if ap.exists():
            _, rows = rd(ap)
        rows.append({"date": DS, "book": book, "amount": round(refund, 2),
                     "reason": "赎回费更正：7-30 天按 C 类 0% 退回多收部分"})
        wr(ap, ADJ_COLS, rows)

        lcols, ledger = rd(b / "ledger.csv")
        prev = None
        for i in range(len(ledger) - 1, -1, -1):
            if ledger[i]["date"] == DS and ledger[i]["phase"] == "close":
                cur = ledger[i]
                prev = next((r for r in reversed(ledger[:i]) if r["phase"] == "close"), None)
                nav = round(float(cur["nav"]) + refund, 2)
                cur["nav"] = f"{nav}"
                cur["cash"] = f"{float(cur['cash']) + refund:.2f}"
                if prev:
                    cur["day_ret"] = round(nav / float(prev["nav"]) - 1, 6)
                cur["total_ret"] = round(nav / float(acc["initial"]) - 1, 6)
                ledger[i] = cur
                break
        wr(b / "ledger.csv", lcols, ledger)
        print(f"[{book}] 退回 {refund:,.2f}；现金 {acc['cash']:,.2f}；"
              f"今日净值 {ledger[-1]['nav']}（day {ledger[-1]['day_ret']}, total {ledger[-1]['total_ret']}）")


if __name__ == "__main__":
    main()
