"""冻结版「国家队减持」防守信号 · 影子日志（只记录，不交易）。

冻结参数（2026-11-08 冻结，等级：证据不足）：
    n=14（14 个交易日的累计份额变化）
    m=-10.0%（累计 ≤ -10% 触发）
    l=5（信号后第 5 个交易日才生效）
    k=20（生效后维持 20 个交易日低仓位）
    level=0.0（低仓位 = 0，即空仓）

本脚本只做三件事：
1. 用 data/national_team/etf_shares_hist.csv 算最近一个交易日的 cum14；
2. 判断当前是否处于"防守窗口"，以及防守到哪天；
3. 追加一行到 data/national_team/shadow_signal_log.csv。

**不产生任何交易指令**，也不写 paper/shadow 账户。

用法：venv\\Scripts\\python.exe tools\\nt_signal_shadow.py [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARES = ROOT / "data" / "national_team" / "etf_shares_hist.csv"
LOG = ROOT / "data" / "national_team" / "shadow_signal_log.csv"

FROZEN = {
    "frozen_on": "2026-11-08",
    "n": 14,
    "m": -10.0,
    "l": 5,
    "k": 20,
    "level": 0.0,
    "verdict": "未通过（G4b/H5）",
    "evidence_level": "证据不足",
    "usage": "仅影子日志记录，不据此调仓",
}

FIELDS = ["run_date", "data_date", "cum14", "signal", "state", "defense_from", "defense_until", "note"]


def load_shares() -> tuple[list[str], list[float]]:
    by_date: dict[str, dict[str, float]] = {}
    with SHARES.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            by_date.setdefault(r["date"], {})[r["code"]] = float(r["shares_wan"])
    dates = sorted(by_date)
    cum = []
    for i, d in enumerate(dates):
        if i < FROZEN["n"]:
            cum.append(float("nan"))
            continue
        total = 0.0
        ok = True
        for j in range(i - FROZEN["n"] + 1, i + 1):
            p, c = by_date[dates[j - 1]], by_date[dates[j]]
            common = [x for x in c if x in p]
            if not common:
                ok = False
                break
            total += (sum(c[x] for x in common) / sum(p[x] for x in common) - 1) * 100
        cum.append(total if ok else float("nan"))
    return dates, cum


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="按指定数据日评估（默认用最新一天）")
    args = ap.parse_args([] if argv is None else argv)

    dates, cum = load_shares()
    if args.date:
        if args.date not in dates:
            print(f"[warn] {args.date} 不在份额历史里，用最新一天")
        else:
            idx = dates.index(args.date)
            dates, cum = dates[: idx + 1], cum[: idx + 1]

    # 复算防守窗口：对每个触发日 i，窗口是 [i+l, i+l+k)
    state_by_i = ["正常"] * len(dates)
    until_by_i = [""] * len(dates)
    for i, v in enumerate(cum):
        if v != v or v > FROZEN["m"]:
            continue
        lo = i + FROZEN["l"]
        hi = min(len(dates), lo + FROZEN["k"])
        for j in range(lo, hi):
            state_by_i[j] = "防守"
        if lo < len(dates) and hi - 1 < len(dates):
            until_by_i[lo] = dates[hi - 1]

    i = len(dates) - 1
    row = {
        "run_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": dates[i],
        "cum14": f"{cum[i]:.2f}" if cum[i] == cum[i] else "",
        "signal": "1" if (cum[i] == cum[i] and cum[i] <= FROZEN["m"]) else "0",
        "state": state_by_i[i],
        "defense_from": dates[i] if state_by_i[i] == "防守" else "",
        "defense_until": until_by_i[i],
        "note": "shadow-only, no trade",
    }

    LOG.parent.mkdir(parents=True, exist_ok=True)
    exists = LOG.exists()
    with LOG.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerow(row)

    print(f"[ok] {row['data_date']} cum14={row['cum14']}% 信号={row['signal']} 状态={row['state']}"
          + (f" 防守至 {row['defense_until']}" if row["defense_until"] else ""))
    print(f"[ok] 追加到 {LOG}")
    print(f"[note] 冻结参数 {FROZEN} —— 只记录，不交易")


if __name__ == "__main__":
    import sys as _sys

    main(_sys.argv[1:])

