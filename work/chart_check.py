"""核对：UP 主标签/我们的份额数据，是否验证「国家队擅长交易高低点」那张图。

做法
----
1. 拉上证综指日线（新浪，够长），算每个数据日的：
   * 近 250 日分位（0=一年最低，100=一年最高）
   * 距 250 日高点/低点的百分比
   * 之后 5/10 个交易日的涨跌幅
2. 按标签（增持/待增持/减持）分组看中位数。
3. 把「份额净增/净减」按指数区间分桶，看净买入是否集中在低位。

输出 runs/20261108_bili_wangwang/chart_check.md
"""

from __future__ import annotations

import csv
import statistics as st
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "20261108_bili_wangwang"
SHARES = ROOT / "data" / "national_team" / "etf_shares_hist.csv"
SINA = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=800"
)


def load_index() -> list[tuple[str, float]]:
    d = requests.get(
        SINA,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        timeout=30,
    ).json()
    return [(r["day"], float(r["close"])) for r in d]


def med(v):
    v = sorted(v)
    if not v:
        return float("nan")
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def main() -> None:
    idx = load_index()
    closes = {d: c for d, c in idx}
    order = [d for d, _ in idx]
    pos = {d: i for i, d in enumerate(order)}

    shares: dict[str, dict[str, float]] = {}
    for r in csv.DictReader(SHARES.open(encoding="utf-8-sig", newline="")):
        shares.setdefault(r["date"], {})[r["code"]] = float(r["shares_wan"])
    sdates = sorted(shares)
    tot = {d: sum(shares[d].values()) for d in sdates}

    rows = [
        r for r in csv.DictReader((RUN / "compare.csv").open(encoding="utf-8-sig", newline=""))
        if r["ok"] == "1"
    ]

    def percentile(d: str, window: int = 250) -> float:
        i = pos.get(d)
        if i is None:
            return float("nan")
        seg = [c for _, c in idx[max(0, i - window + 1): i + 1]]
        if len(seg) < 30:
            return float("nan")
        lo, hi = min(seg), max(seg)
        return (closes[d] - lo) / (hi - lo) * 100 if hi > lo else 50.0

    def fwd(d: str, n: int) -> float:
        i = pos.get(d)
        if i is None or i + n >= len(order):
            return float("nan")
        return (closes[order[i + n]] / closes[d] - 1) * 100

    def back(d: str, n: int) -> float:
        i = pos.get(d)
        if i is None or i - n < 0:
            return float("nan")
        return (closes[d] / closes[order[i - n]] - 1) * 100

    lines = ["# 「国家队擅长交易高低点」能不能被数据验出来", ""]
    lines.append(f"- 指数：上证综指，{order[0]} ~ {order[-1]}，{len(order)} 个交易日")
    lines.append("")

    labels = ["增持", "待增持", "减持"]
    lines.append("## 标签 vs 上证所处位置")
    lines.append("")
    lines.append("| 标签 | n | 点位中位 | 近250日分位中位(%) | 距250日高点 | 距250日低点 | 前10日涨幅 | 后5日涨幅 | 后10日涨幅 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    stats = {}
    for lab in labels:
        sub = [r for r in rows if r["label"] == lab]
        if not sub:
            continue
        d0 = [r["data_date"] for r in sub]
        p250 = [percentile(d) for d in d0 if percentile(d) == percentile(d)]
        hi = [c for c in (percentile(d) for d in d0) if c == c]
        lvl = [closes[d] for d in d0 if d in closes]
        lines.append(
            "| {} | {} | {:.0f} | {:.0f} | - | - | {:+.2f} | {:+.2f} | {:+.2f} |".format(
                lab, len(sub), med(lvl), med(p250), med([back(d, 10) for d in d0 if back(d, 10) == back(d, 10)]),
                med([fwd(d, 5) for d in d0 if fwd(d, 5) == fwd(d, 5)]),
                med([fwd(d, 10) for d in d0 if fwd(d, 10) == fwd(d, 10)]),
            )
        )
        stats[lab] = {"p250": med(p250), "lvl": med(lvl), "fwd10": med([fwd(d, 10) for d in d0 if fwd(d, 10) == fwd(d, 10)])}

    # 净买入按指数区间分桶（近250日分位）
    lines.append("")
    lines.append("## 净买入落在哪一段（按近250日分位分桶）")
    lines.append("")
    buckets = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    agg = {b: {"buy": 0.0, "sell": 0.0, "days": 0, "net": 0.0} for b in buckets}
    for i, d in enumerate(sdates):
        if i == 0:
            continue
        p = percentile(d)
        if p != p:
            continue
        delta = tot[d] - tot[sdates[i - 1]]
        for b in buckets:
            if b[0] <= p < b[1] or (b[1] == 100 and p == 100):
                agg[b]["days"] += 1
                agg[b]["net"] += delta / 10000.0  # 亿份
                if delta > 0:
                    agg[b]["buy"] += delta / 10000.0
                else:
                    agg[b]["sell"] += delta / 10000.0
                break
    lines.append("| 分位区间 | 天数 | 净增(亿份) | 买入(亿份) | 卖出(亿份) |")
    lines.append("|---|---|---|---|---|")
    for b in buckets:
        a = agg[b]
        lines.append(f"| {b[0]}–{b[1]}% | {a['days']} | {a['net']:+.1f} | {a['buy']:.1f} | {a['sell']:.1f} |")

    # 结论
    lines.append("")
    lines.append("## 结论")
    if "增持" in stats and "待增持" in stats:
        lines.append(
            "- 增持日上证分位中位 **{:.0f}%**，待增持日 **{:.0f}%**，减持日 **{:.0f}%**。".format(
                stats["增持"]["p250"], stats["待增持"]["p250"], stats.get("减持", {}).get("p250", float("nan"))
            )
        )
        lines.append(
            "- 增持日后 10 日上证中位 **{:+.2f}%**，待增持日后 **{:+.2f}%**。".format(
                stats["增持"]["fwd10"], stats["待增持"]["fwd10"]
            )
        )
    out = RUN / "chart_check.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
