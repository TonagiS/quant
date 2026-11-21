"""按冻结版国家队规则回放 2026-06-01 ~ 2026-09-30 的操作日程（纯测算，不交易）。

规则（tools/nt_signal_shadow.py FROZEN）：
    cum14 = 最近 14 个交易日「逐日合计份额变动%」之和；cum14 <= -10% 触发
    触发后第 5 个交易日生效，空仓 20 个交易日，然后回到基础仓位
输出：runs/<日期>_nt_rule_2026h2/report.md + 控制台日程
"""

from __future__ import annotations

import csv
import json
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARES = ROOT / "data" / "national_team" / "etf_shares_hist.csv"
RUN = ROOT / "runs" / f"{date.today():%Y%m%d}_nt_rule_2026h2"

N, M, L, K = 14, -10.0, 5, 20
START, END = "2026-06-01", "2026-09-30"


def load() -> tuple[list[str], dict[str, dict[str, float]]]:
    by_date: dict[str, dict[str, float]] = {}
    with SHARES.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            by_date.setdefault(r["date"], {})[r["code"]] = float(r["shares_wan"])
    return sorted(by_date), by_date


def cum14(dates: list[str], bd: dict[str, dict[str, float]]) -> list[float]:
    out = []
    for i, _ in enumerate(dates):
        if i < N:
            out.append(float("nan"))
            continue
        tot = 0.0
        for j in range(i - N + 1, i + 1):
            p, c = bd[dates[j - 1]], bd[dates[j]]
            common = [x for x in c if x in p]
            if not common:
                tot = float("nan")
                break
            tot += (sum(c[x] for x in common) / sum(p[x] for x in common) - 1) * 100
        out.append(tot)
    return out


def close_series() -> dict[str, float]:
    u = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.510300&klt=101&fqt=1"
         "&beg=20260515&end=20261010&fields1=f1,f2&fields2=f51,f53")
    j = json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0",
                                           "Referer": "https://quote.eastmoney.com/"}),
        timeout=25).read().decode("utf-8", "replace"))
    out = {}
    for line in ((j.get("data") or {}).get("klines") or []):
        d, c = line.split(",")[:2]
        out[d] = float(c)
    return out


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    dates, bd = load()
    cum = cum14(dates, bd)
    px = close_series()

    idx = {d: i for i, d in enumerate(dates)}
    trig = [i for i, v in enumerate(cum) if v == v and v <= M and START <= dates[i] <= END]
    segs = []
    for i in trig:
        lo, hi = i + L, min(len(dates), i + L + K)
        if segs and lo <= segs[-1][1]:
            segs[-1] = (segs[-1][0], max(segs[-1][1], hi), segs[-1][2] + [i])
        else:
            segs.append((lo, hi, [i]))

    def px_at(d: str) -> float:
        return px.get(d, float("nan"))

    lines = [f"# 冻结规则回放 2026-06-01 ~ 2026-09-30（n={N}, m={M}%, l={L}, k={K}, 空仓）", ""]
    lines.append(f"- 触发信号日：**{len(trig)} 天**（{dates[trig[0]] if trig else '-'} ~ {dates[trig[-1]] if trig else '-'}）")
    lines.append(f"- 合并后防守段：**{len(segs)} 段**")
    body = 0
    for a, b, ts in segs:
        body += b - a
    lines.append(f"- 空仓交易日：**{body} 天**")
    lines.append("")
    lines.append("| # | 信号起始 | 信号最强 | 空仓起 | 空仓止 | 天数 | 买入回补日 | 510300区间 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    detail = []
    for n, (a, b, ts) in enumerate(segs, 1):
        first, worst = dates[ts[0]], dates[min(ts, key=lambda i: cum[i])]
        d_from, d_to = dates[a], dates[b - 1]
        back = dates[b] if b < len(dates) else "—"
        p0, p1 = px_at(d_from), px_at(d_to)
        worst_v = cum[min(ts, key=lambda i: cum[i])]
        lines.append(f"| {n} | {first} | {worst}({worst_v:.1f}%) | {d_from} | {d_to} | {b - a} | {back} | "
                     f"{p0:.2f}→{p1:.2f}({(p1 / p0 - 1) * 100:+.1f}%) |")
        detail.append((n, first, worst, worst_v, d_from, p0, d_to, p1, back))

    # 全区间收益对比
    c0 = px_at(dates[idx[START]])
    c1 = px_at(dates[idx[END]])
    hold = (c1 / c0 - 1) * 100
    nav, pos = 1.0, True
    for i in range(idx[START], idx[END]):
        d_next = dates[i + 1]
        state = any(a <= i < b for a, b, _ in segs)  # 当日是否空仓
        r = px_at(d_next) / px_at(dates[i]) - 1 if px_at(dates[i]) == px_at(dates[i]) else 0
        if not state:
            nav *= 1 + r
        pos = not state
    rule = (nav - 1) * 100
    lines.append("")
    lines.append(f"- 买入持有 510300：{c0:.2f} → {c1:.2f}，**{hold:+.1f}%**")
    lines.append(f"- 按规则（信号后第5日空仓20日）：**{rule:+.1f}%**，差额 {rule - hold:+.1f}pp")
    lines.append("")
    lines.append("口径：份额 = 沪市 9 只宽基 ETF 合计；cum14 = 逐日合计份额变动% 相加；价格用 510300 收盘。")
    (RUN / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    print(f"\n[out] {RUN / 'report.md'}")


if __name__ == "__main__":
    main()
