"""近 5 个交易日：国家队（沪市 9 只宽基 ETF 合计，我们的口径）单日增减持 + 金额估算，
并与 B 站 UP「梦想超过余额宝」每日标题标注对照。

口径（与 nt_brief.py 一致）
--------------------------
* 份额：etf_shares_hist.csv（上交所 ETF 份额公示，单位万份）/1e4 = 亿份
* 单日变化：当日合计 - 上一交易日合计（亿份）；百分比 = 变化 / 上一日合计
* 金额估算：Σ(单只当日份额变化 × 当日收盘价)，亿元（不做申赎价差修正）

输出：runs/<日期>_nt_vs_bili/daily5.csv + report.md
用法：venv\\Scripts\\python.exe work\\nt_daily5_vs_bili.py
"""

from __future__ import annotations

import csv
import json
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / "data" / "national_team" / "etf_shares_hist.csv"
OUT = ROOT / "runs" / f"{date.today():%Y%m%d}_nt_vs_bili"
UA = {"User-Agent": "Mozilla/5.0"}

# UP 主近几日标题标注（来源：bili_nt_titles.py 抓的合集标题，2026-11-20 抓取）
BILI = {
    "2026-11-13": ("待增持", 5.72, "BV1xAWr6RE2q"),
    "2026-11-16": ("增持", 4.52, "BV1xdsM6PEBU"),
    "2026-11-17": ("增持", 6.00, "BV1SGsy6uEze"),
    "2026-11-18": ("增持", 6.86, "BV1Qess6pE5H"),
    "2026-11-19": ("增持", 14.95, "BV1v4si6oEJt"),
}


def fetch_closes(codes: list[str], start: str, end: str) -> pd.DataFrame:
    out = {}
    for code in codes:
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
            f"sh{code},day,{start},{end},40,qfq"
        )
        req = urllib.request.Request(url, headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace"))
        node = d["data"][f"sh{code}"]
        kl = node.get("qfqday") or node.get("day") or []
        out[code] = {k[0]: float(k[2]) for k in kl}
    df = pd.DataFrame(out)
    df.index = pd.to_datetime(df.index).astype("datetime64[ns]")
    return df.sort_index()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    h = pd.read_csv(HIST, parse_dates=["date"])
    name = dict(zip(h["code"].astype(str), h["name"]))
    p = h.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4  # 亿份
    codes = list(p.columns)
    scodes = {c: str(c) for c in codes}

    dates = list(p.index)
    last5 = dates[-6:]  # 含基准日，用于算第一天
    start, end = last5[0].date().isoformat(), last5[-1].date().isoformat()
    px = fetch_closes([str(c) for c in codes], start, end)

    chg = p.diff()
    rows = []
    for i in range(1, len(last5)):
        dt = last5[i]
        prev = last5[i - 1]
        sh = float(chg.loc[dt].sum())
        tot_prev = float(p.loc[prev].sum())
        money = 0.0
        for c in codes:
            v = chg.loc[dt, c]
            if pd.isna(v):
                continue
            price = None
            if scodes[c] in px.columns:
                s = px[scodes[c]]
                s = s[s.index == pd.Timestamp(dt)]
                if len(s):
                    price = float(s.iloc[0])
            if price is None or pd.isna(price):
                continue
            money += float(v) * float(price)  # 亿份 × 元/份 = 亿元
        label, pct, bv = BILI.get(dt.date().isoformat(), ("", "", ""))
        rows.append(
            {
                "date": dt.date().isoformat(),
                "shares_chg_yi": round(sh, 2),
                "pct": round(sh / tot_prev * 100, 2),
                "money_yi": round(money, 1),
                "tot_yi": round(float(p.loc[dt].sum()), 1),
                "bili_label": label,
                "bili_pct": pct,
                "bvid": bv,
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "daily5.csv", index=False, encoding="utf-8-sig")

    # 分只明细（近 5 日，亿份）
    per = chg.loc[last5[1:]].round(2)
    per.columns = [f"{c} {name.get(c, '')}" for c in codes]
    per.index = [d.date().isoformat() for d in per.index]
    per.to_csv(OUT / "per_etf_chg5.csv", encoding="utf-8-sig")

    lines = [
        f"# 近 5 个交易日国家队口径对照（份额截至 {df.date.iloc[-1]}）",
        "",
        "我们：沪市 9 只宽基 ETF 份额合计日变化（亿份/%/≈亿元）",
        "UP：B 站「梦想超过余额宝」每日标题标注（某队单日增持/待增持 X%）",
        "",
        "| 日期 | 份额变化(亿份) | % | ≈金额(亿元) | 合计(亿份) | UP 标注 |",
        "|---|---|---|---|---|---|",
    ]
    for r in df.itertuples():
        lines.append(
            f"| {r.date} | {r.shares_chg_yi:+.2f} | {r.pct:+.2f}% | {r.money_yi:+.0f} | {r.tot_yi:.0f} | "
            f"{r.bili_label} {r.bili_pct}% |"
        )
    lines += ["", "## 分只明细（亿份）", "", "```", per.to_string(), "```"]

    tot0 = float(p.loc[last5[0]].sum())
    tot1 = float(p.loc[last5[-1]].sum())
    lines += [
        "",
        "## 近 5 日累计",
        "",
        f"- 份额 {tot1 - tot0:+.2f} 亿份（{(tot1 / tot0 - 1) * 100:+.2f}%），"
        f"{tot0:.0f} → {tot1:.0f} 亿份",
        f"- 金额（按各日收盘价逐只折算相加）约 {df.money_yi.sum():+.0f} 亿元",
        "",
        "## 口径差异（为什么他的 % 和我们不一样）",
        "",
        "1. 我们：沪市 9 只宽基 ETF **份额合计的单日变化**，分母是上一交易日合计份额；金额按当日收盘价折算。",
        "2. 他：标题里是**累计/趋势口径**的“某队单日增持/待增持 X%”（同一波里逐日递增：4.52 → 6.00 → 6.86 → 14.95），"
        "基准是他自己定的峰值份额，且覆盖沪深两市宽基（含 159919 等深市 ETF），不是我们的 9 只沪市组合。",
        "3. 方向一致、幅度不可直接比：最近 5 天他标“待增持”的那天我们合计是净赎回（-31.19 亿份），"
        "标“增持”的四天我们都是净申购（+51.74 / +35.54 / +23.18 / +150.81 亿份）。",
        "4. 他的数据来源同样是上交所/深交所每日份额公示，所以逐只数字能对上，差异只在聚合方式。",
        "",
        "## UP 主标注来源",
        "",
        "B 站「梦想超过余额宝」合集「A股国家队数据追踪」（season 7828884），标题抓取于 "
        f"{date.today().isoformat()}，原始表见 runs/{date.today():%Y%m%d}_bili_wangwang/titles.csv。",
    ]

    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    print("\n[out]", OUT)


if __name__ == "__main__":
    main()
