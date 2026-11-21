"""按双账本（s2 / vt15）规则回放 2026-06-01 ~ 2026-09-30：具体持哪几只 ETF、什么时候换。

规则（position/live/shadow/RULES_v2.md + rules_v2.json）：
    选股：52 只池，k=5，动量 rm（20/60/120 日排名均值），60 日线过滤，逆波动加权
    调仓：每 14 自然日 ≈ 10 交易日（reb_mode=rolling_change，目标没变就不动），T+1 收盘成交
    账本 s2  = 满仓（Fixed 1.0）
    账本 vt15 = 同持仓，敞口 = min(1, 15% / vol60)
输出：runs/<日期>_dual_book_2026h2/report.md + 控制台

用法：venv\\Scripts\\python.exe work\\dual_book_replay_2026h2.py
"""

from __future__ import annotations

import csv
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "position"))

from engine import Config, Fixed, VolTarget, features, load_panel, run_with_base  # noqa: E402

START, END = "2026-06-01", "2026-09-30"
RUN = ROOT / "runs" / f"{date.today():%Y%m%d}_dual_book_2026h2"


def names() -> dict[str, str]:
    out = {}
    with (ROOT / "position" / "live" / "shadow" / "universe_v1.csv").open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            out[r["code"]] = r["name"]
    return out


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    close = load_panel("ext")
    f = features(close)
    nm = names()

    base = Config(k=5, reb=10, buy_cost=0.0005, sell_cost=0.0005,
                  reb_mode="rolling_change", weight_mode="invvol")
    s2 = run_with_base(close, f, base, keep_weights=True)
    vt = run_with_base(close, f, replace(base, rule=VolTarget(target=0.15, window=60, min_periods=40)),
                       keep_weights=True)

    dates = s2["dates"]
    mask = (dates >= START) & (dates <= END)
    idx = np.flatnonzero(mask)
    i0, i1 = int(idx[0]), int(idx[-1])

    cols = list(close.columns)

    def lab(key) -> str:
        code = cols[key] if isinstance(key, (int, np.integer)) else key
        return nm.get(str(code), str(code))

    # 只在权重变化的日子出表
    rows = []
    prev: dict = {}
    for i in range(i0, i1 + 1):
        w = s2["w_hist"][i]
        if w != prev:
            hold = sorted(w.items(), key=lambda kv: -kv[1])
            vt_expo = float(sum(vt["w_hist"][i].values()))
            rows.append((str(dates[i].date()), hold, float(sum(w.values())), vt_expo))
        prev = w

    eq_s2 = s2["equity"]
    eq_vt = vt["equity"]
    ret_s2 = eq_s2[i1] / eq_s2[i0 - 1] - 1
    ret_vt = eq_vt[i1] / eq_vt[i0 - 1] - 1
    bench = close["sh510300"]
    ret_b = bench.iloc[i1] / bench.iloc[i0 - 1] - 1

    lines = [f"# 双账本回放 {START} ~ {END}", ""]
    lines.append(f"- 交易日 {i1 - i0 + 1} 天，换仓 {len(rows)} 次")
    lines.append(f"- s2 满仓：**{ret_s2 * 100:+.1f}%**；vt15：**{ret_vt * 100:+.1f}%**；510300 买入持有：{ret_b * 100:+.1f}%")
    lines.append("")
    lines.append("| 换仓日 | 持仓（名称 权重%） | s2 总仓 | vt15 敞口 |")
    lines.append("|---|---|---|---|")
    for d, hold, tot, expo in rows:
        hs = "<br>".join(f"{lab(c)} {w * 100:.0f}%" for c, w in hold)
        lines.append(f"| {d} | {hs} | {tot * 100:.0f}% | {expo * 100:.0f}% |")

    # 每只 ETF 在窗口内被持有过多少天
    hold_days: dict[str, int] = {}
    for i in range(i0, i1 + 1):
        for c in s2["w_hist"][i]:
            hold_days[c] = hold_days.get(c, 0) + 1
    lines.append("")
    lines.append("## 窗口内持有天数")
    for c, n in sorted(hold_days.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {lab(c)}（{c}）：{n} 天，平均权重 {np.mean([s2['w_hist'][i].get(c, 0) for i in range(i0, i1 + 1)]) * 100:.0f}%")

    (RUN / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[out] {RUN / 'report.md'}")


if __name__ == "__main__":
    main()
