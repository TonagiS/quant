"""冻结参数下的事件表：每个触发的信号日 / 数据可得日 / 减仓生效日 / 20 日后市场收益。

冻结参数（FROZEN）：n=14, m=-10.0, l=5, k=20, level=0.0（减仓至 0，即空仓 20 天）
输出 runs/20261108_nt_sell_signal/events.csv 和 events.md
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "work"))

from position.engine import features, load_panel  # noqa: E402
from nt_policy_overlay import nt_series  # noqa: E402

RUN = ROOT / "runs" / "20261108_nt_sell_signal"
FROZEN = {"n": 14, "m": -10.0, "l": 5, "k": 20, "level": 0.0}


def main() -> None:
    close = load_panel("ext")
    f = features(close)
    _, arr = nt_series(close.index)
    c1 = pd.Series(arr["c1"], index=close.index)
    cum = c1.rolling(FROZEN["n"], min_periods=max(5, FROZEN["n"] // 2)).sum()
    sig = np.nan_to_num(cum.to_numpy(), nan=9.0) <= FROZEN["m"]

    px = close["sh510300"]
    idx = pd.Series(close.index, index=range(len(close)))
    dates = close.index
    pos = {d: i for i, d in enumerate(dates)}

    # 连续触发合并成 episode：两次触发间隔 < k 视为同一段
    hits = np.flatnonzero(sig)
    episodes = []
    for i in hits:
        if episodes and i - episodes[-1][-1] < FROZEN["k"]:
            episodes[-1].append(i)
        else:
            episodes.append([i])

    rows = []
    for ep in episodes:
        i0 = ep[0]
        t_sig = dates[i0]
        t_avail = dates[i0 + 1] if i0 + 1 < len(dates) else None   # 上交所次日披露
        i_eff = i0 + FROZEN["l"]
        i_end = min(i_eff + FROZEN["k"], len(dates) - 1)
        r20 = (px.iloc[i0 + 20] / px.iloc[i0] - 1) * 100 if i0 + 20 < len(dates) else float("nan")
        i_idx = pos[t_sig]
        v20 = (close["sh510300"].iloc[i_idx + 20] / close["sh510300"].iloc[i_idx] - 1) * 100 if i_idx + 20 < len(dates) else float("nan")
        r_eff20 = (px.iloc[i_eff + 20] / px.iloc[i_eff] - 1) * 100 if i_eff + 20 < len(dates) else float("nan")
        rows.append({
            "信号日": t_sig.strftime("%Y-%m-%d"),
            "触发天数": len(ep),
            "累计变动%": round(float(cum.iloc[i0]), 2),
            "数据可得日": t_avail.strftime("%Y-%m-%d") if t_avail is not None else "",
            "减仓生效日": dates[i_eff].strftime("%Y-%m-%d") if i_eff < len(dates) else "",
            "减仓结束日": dates[i_end].strftime("%Y-%m-%d"),
            "510300_20日后%": round(float(r20), 2),
            "生效后20日%": round(float(r_eff20), 2),
        })

    df = pd.DataFrame(rows)
    df.to_csv(RUN / "events.csv", index=False, encoding="utf-8-sig")

    lines = ["# 冻结参数下的触发事件", "",
             f"参数：n={FROZEN['n']} / m={FROZEN['m']}% / l={FROZEN['l']} / k={FROZEN['k']} / 减仓至 {FROZEN['level']:.0%}", "",
             f"- 触发日（信号日）共 {len(hits)} 天，合并为 {len(episodes)} 段",
             f"- 事件分布年份：" + "、".join(f"{y}年{sum(1 for r in rows if r['信号日'].startswith(str(y)))}段" for y in sorted({int(r['信号日'][:4]) for r in rows})),
             "",
             "| 信号日 | 触发天数 | 14日累计 | 数据可得日 | 减仓生效日 | 减仓结束日 | 510300 后20日 | 生效后20日 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| {信号日} | {触发天数} | {累计变动%}% | {数据可得日} | {减仓生效日} | {减仓结束日} | {510300_20日后%}% | {生效后20日%}% |".format(**r))
    (RUN / "events.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
