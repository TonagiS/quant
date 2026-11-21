# -*- coding: utf-8 -*-
"""全链路体检：数据新鲜度、账本一致性、影子状态、播报标记。只读，不改任何东西。"""
import csv
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(r"E:\Code\quant")
LIVE = ROOT / "position/live"
out = []


def p(*a):
    s = " ".join(str(x) for x in a)
    out.append(s)
    print(s)


def last_date_csv(fp):
    try:
        df = pd.read_csv(fp)
        col = "date" if "date" in df.columns else df.columns[0]
        return str(df[col].iloc[-1])[:10], len(df)
    except Exception as e:
        return f"ERR {type(e).__name__}", 0


p("== 1. 快照 data/daily ==")
snaps = sorted((ROOT / "data/daily").glob("*.json"))
p("  最新:", snaps[-1].name, "| 文件数", len(snaps))
raw = json.load(open(snaps[-1], encoding="utf-8-sig"))
p("  codes", len(raw.get("codes", {})), "failed", len(raw.get("failed") or {}))

p("== 2. 缓存 cache_ohlc ==")
uni = pd.read_csv(LIVE / "shadow/universe_v1.csv", dtype={"code": str})
codes = list(uni.loc[uni["status"] == "OK", "code"])
files = sorted((ROOT / "data/cache_ohlc").glob("*.csv"))
miss = [c for c in codes if not (ROOT / "data/cache_ohlc" / f"{c}.csv").exists()]
dates = []
for f in files:
    d, _ = last_date_csv(f)
    dates.append(d)
p("  文件", len(files), "| 最新日期最大", max(dates) if dates else "-",
  "| 最旧", min(dates) if dates else "-", "| 池内缺文件", len(miss), miss[:6])
pool_dates = {}
for c in codes:
    fp = ROOT / "data/cache_ohlc" / f"{c}.csv"
    if fp.exists():
        d, _ = last_date_csv(fp)
        pool_dates[c] = d
from collections import Counter
p("  池内末日期分布:", Counter(pool_dates.values()).most_common())
bad = [c for c, d in pool_dates.items() if d < "2026-11-16"]
p("  池内落后于 11-16 的:", len(bad), bad[:10])

p("== 3. 全市场缓存 cache_ohlc_full ==")
full = sorted((ROOT / "data/cache_ohlc_full").glob("*.csv"))
fd = [last_date_csv(f)[0] for f in full[:400]]
p("  文件", len(full), "| 抽样最新", max(fd) if fd else "-", "| 抽样最旧", min(fd) if fd else "-")

p("== 4. 国家队数据 ==")
nt = ROOT / "data/national_team"
for name in ("etf_shares.csv", "etf_shares_hist.csv", "index_close.csv"):
    fp = nt / name
    if fp.exists():
        d, n = last_date_csv(fp)
        p(f"  {name}: 末行 {d}，{n} 行")
lm = nt / "latest.md"
if lm.exists():
    p("  latest.md mtime", pd.Timestamp(lm.stat().st_mtime, unit="s", tz="Asia/Shanghai").strftime("%m-%d %H:%M"))

p("== 5. shadow_log 最近 ==")
sl = pd.read_csv(LIVE / "shadow/shadow_log.csv", encoding="utf-8-sig")
for _, r in sl.tail(5).iterrows():
    fails = json.loads(r["failed"]) if isinstance(r["failed"], str) and r["failed"].startswith("{") else {}
    tg = json.loads(r["targets_json"]) if isinstance(r["targets_json"], str) and r["targets_json"] else {}
    p(f"  {r['date']} {r['time']} {r['phase']:>5} n_cand={r['n_cand']} fails={len(fails)} "
      f"targets={len(tg)} notes={r['notes']}")

p("== 6. 账本一致性 ==")
for book in ("s2", "vt15"):
    b = LIVE / "paper/books" / book
    acc = json.load(open(b / "account.json", encoding="utf-8"))
    t = pd.read_csv(b / "trades.csv", encoding="utf-8-sig")
    cash_calc = acc["initial"]
    for _, x in t.iterrows():
        if x["side"] == "buy":
            cash_calc -= float(x["amount"]) + float(x["fee"])
        else:
            cash_calc += float(x["amount"]) - float(x["fee"])
    adj = b / "adjustments.csv"
    if adj.exists():
        a = pd.read_csv(adj, encoding="utf-8-sig")
        cash_calc += float(a["amount"].sum())
    led = pd.read_csv(b / "ledger.csv", encoding="utf-8-sig")
    px = json.load(open((ROOT / "data/daily/20261117.json"), encoding="utf-8-sig"))["codes"]
    pos_val = sum(v["shares"] * px.get(c, v["cost"]) for c, v in acc["positions"].items())
    p(f"  [{book}] cash账本 {acc['cash']:,.2f} vs 重算 {cash_calc:,.2f} 差 {acc['cash']-cash_calc:+,.2f}"
      f" | 持仓 {len(acc['positions'])} 只 | ledger末行 {led.iloc[-1]['date']} {led.iloc[-1]['phase']} nav={led.iloc[-1]['nav']}")
    p(f"        持仓市值(14:30价) {pos_val:,.2f}，nav重算 {pos_val+acc['cash']:,.2f}")

p("== 7. 影子状态 ==")
st = json.load(open(LIVE / "shadow/state.json", encoding="utf-8"))
p("  ", json.dumps(st, ensure_ascii=False))
rules = json.load(open(LIVE / "shadow/rules_v2.json", encoding="utf-8"))
p("  rules:", json.dumps(rules, ensure_ascii=False))
expo = LIVE / "paper/books/vt15/expo.json"
p("  vt15 expo.json:", expo.read_text(encoding="utf-8") if expo.exists() else "缺失")

p("== 8. 播报标记 ==")
sd = Path(r"E:\Code\dsh-agent\state")
for f in sorted(sd.glob("*.txt"))[-6:]:
    p("  ", f.name, f.stat().st_size, "B")

p("== 9. 费率口径 ==")
fp = LIVE / "paper/fees.json"
p("  ", json.dumps(json.load(open(fp, encoding="utf-8")), ensure_ascii=False)[:220])

Path(r"E:\Code\quant\work\audit_20261117.txt").write_text("\n".join(out), encoding="utf-8")
