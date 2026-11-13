"""A4/B3：国家队通道份额——只能做描述（沪市接口只留了近半年逐日历史）。

输出：窗口内份额变化（亿份 / %）、对应指数涨跌，判断是净申购还是净赎回。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NT = ROOT / "data" / "national_team"


def main():
    d = pd.read_csv(NT / "etf_shares_hist.csv", parse_dates=["date"])
    d = d.sort_values(["code", "date"])
    dates = sorted(d["date"].unique())
    print(f"窗口 {str(dates[0])[:10]} ~ {str(dates[-1])[:10]}，{len(dates)} 天，{d['code'].nunique()} 只")
    rows = []
    for code, g in d.groupby("code"):
        g = g.set_index("date")["shares_wan"]
        for win in (5, 20, 120):
            if len(g) > win:
                chg = g.iloc[-1] - g.iloc[-1 - win]
                rows.append(dict(code=code, name=g.name, win=win,
                                 chg_yi=chg / 1e4, pct=chg / g.iloc[-1 - win] * 100))
    r = pd.DataFrame(rows)
    piv = r.pivot_table(index=["code"], columns="win", values="pct").round(2)
    piv.columns = [f"{c}日份额%" for c in piv.columns]
    last = d.groupby("code").tail(1).set_index("code")
    piv["最新份额(亿)"] = (last["shares_wan"] / 1e4).round(1)
    piv["名称"] = last["name"]
    print(piv.sort_values("5日份额%").to_string())

    out = ROOT / "runs" / "2026-11-16_nt_shares"
    out.mkdir(parents=True, exist_ok=True)
    piv.to_csv(out / "nt_share_change.csv", encoding="utf-8-sig")


if __name__ == "__main__":
    main()
