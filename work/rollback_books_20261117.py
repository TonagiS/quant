# -*- coding: utf-8 -*-
"""回滚 2026-11-17 的“假调仓”：把两个账本恢复到 11-16 收盘后的状态。

背景：14:39 run_shadow 抓历史行情时腾讯接口大面积失败（17 只），
银行/上证180金融/红利因抓不到历史被踢出候选池，规则把剩余 4 只全额吃下，
产生 3 卖 4 买。这里把账本回滚到成交前，随后用修正后的 5 只目标重跑 --execute。
"""
import csv
import json
import os
import shutil
from pathlib import Path

ROOT = Path(r"E:\Code\quant")
PAPER = ROOT / "position/live/paper"
BOOKS = PAPER / "books"
SHADOW = ROOT / "position/live/shadow"
DS = "2026-11-17"
PRIOR = "2026-11-03"
INIT = 100000.0

# 修正后的目标（用本地缓存 11-16 收盘 + 今日 14:30 价重算 engine.targets）
TARGETS = {
    "sh512800": 0.231103,
    "sh510230": 0.317490,
    "sz159928": 0.174965,
    "sh515170": 0.159451,
    "sh512690": 0.116885,
}


def read_rows(p: Path):
    if not p.exists():
        return [], []
    with p.open(encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        return rd.fieldnames, list(rd)


def write_rows(p: Path, cols, rows):
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def rebuild(book: str) -> None:
    bdir = BOOKS / book
    cols, trades = read_rows(bdir / "trades.csv")
    keep = [t for t in trades if t["date"] < DS]
    pos, cash = {}, INIT
    for t in keep:
        amt = float(t["amount"])
        fee = float(t["fee"])
        cash -= amt + fee
        cur = pos.setdefault(t["code"], {"shares": 0, "cost": 0.0, "open_date": t["date"]})
        new_sh = cur["shares"] + int(float(t["shares"]))
        cur["cost"] = round((cur["shares"] * cur["cost"] + amt) / new_sh, 4)
        cur["shares"] = new_sh
    write_rows(bdir / "trades.csv", cols, keep)

    acc_path = bdir / "account.json"
    acc = json.load(open(acc_path, encoding="utf-8"))
    acc["cash"] = round(cash, 2)
    acc["positions"] = pos
    acc["last_rebalance"] = PRIOR
    json.dump(acc, open(acc_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    lcols, ledger = read_rows(bdir / "ledger.csv")
    lkeep = [r for r in ledger if r["date"] < DS]
    write_rows(bdir / "ledger.csv", lcols, lkeep)

    expo = bdir / "expo.json"
    if expo.exists():
        os.remove(expo)

    print(f"[{book}] 回滚完成：持仓 {len(pos)} 只，现金 {cash:,.2f}，"
          f"trades {len(trades)}->{len(keep)}，ledger {len(ledger)}->{len(lkeep)}")
    for c, v in pos.items():
        print(f"    {c} {v['shares']}股 cost {v['cost']}")


def main() -> None:
    for book in ("s2", "vt15"):
        rebuild(book)
    st_path = SHADOW / "state.json"
    st = json.load(open(st_path, encoding="utf-8"))
    st["last_targets"] = TARGETS
    st["last_date"] = DS
    st["last_rebalance"] = PRIOR      # 让 --execute 认为今天是调仓日
    json.dump(st, open(st_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print("[state] 目标已改写为修正版 5 只，last_rebalance 暂置", PRIOR)


if __name__ == "__main__":
    main()
