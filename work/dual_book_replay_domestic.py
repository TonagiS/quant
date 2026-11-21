"""双账本回放（2026-06-01~09-30）：去掉跨境（标普500/纳指100），并跑 5 交易日一调口径。

变体：
    domestic  = 52 只池去掉 sh513500 标普500 / sh513100 纳指100
    domestic2 = 再把 sh513050 中概互联 / sz159920 恒生 也去掉（纯 A 股）
    每个池子各跑 reb=10（14 自然日，主臂）与 reb=5（5 交易日，参考臂）

用法：venv\\Scripts\\python.exe work\\dual_book_replay_domestic.py
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

from engine import Config, VolTarget, features, load_panel, run_with_base  # noqa: E402

START, END = "2026-06-01", "2026-09-30"
RUN = ROOT / "runs" / f"{date.today():%Y%m%d}_dual_book_domestic"
CROSS_A = ["sh513500", "sh513100"]
CROSS_B = CROSS_A + ["sh513050", "sz159920"]


def names() -> dict[str, str]:
    out = {}
    with (ROOT / "position" / "live" / "shadow" / "universe_v1.csv").open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            out[r["code"]] = r["name"]
    return out


def run(close, nm, reb: int, tag: str) -> dict:
    f = features(close)
    base = Config(k=5, reb=reb, buy_cost=0.0005, sell_cost=0.0005,
                  reb_mode="rolling_change", weight_mode="invvol")
    s2 = run_with_base(close, f, base, keep_weights=True)
    vt = run_with_base(close, f, replace(base, rule=VolTarget(target=0.15, window=60, min_periods=40)),
                       keep_weights=True)
    dates = s2["dates"]
    idx = np.flatnonzero((dates >= START) & (dates <= END))
    i0, i1 = int(idx[0]), int(idx[-1])
    cols = list(close.columns)

    def lab(k) -> str:
        code = cols[k] if isinstance(k, (int, np.integer)) else k
        return nm.get(str(code), str(code))

    rows, prev = [], {}
    for i in range(i0, i1 + 1):
        w = s2["w_hist"][i]
        if w != prev:
            rows.append((str(dates[i].date()),
                         sorted(w.items(), key=lambda kv: -kv[1]),
                         float(sum(w.values())),
                         float(sum(vt["w_hist"][i].values()))))
        prev = w
    ret_s2 = s2["equity"][i1] / s2["equity"][i0 - 1] - 1
    ret_vt = vt["equity"][i1] / vt["equity"][i0 - 1] - 1
    return {"tag": tag, "rows": rows, "ret_s2": ret_s2, "ret_vt": ret_vt, "lab": lab,
            "nreb": len(rows), "days": i1 - i0 + 1, "close": close}


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    nm = names()
    close_all = load_panel("ext")
    bench = close_all["sh510300"]

    variants = [
        ("domestic", [c for c in close_all.columns if c not in CROSS_A], 10),
        ("domestic", [c for c in close_all.columns if c not in CROSS_A], 5),
        ("domestic2", [c for c in close_all.columns if c not in CROSS_B], 10),
        ("domestic2", [c for c in close_all.columns if c not in CROSS_B], 5),
    ]
    lines = [f"# 双账本回放（去跨境）{START} ~ {END}", ""]
    results = []
    for pool_tag, cols, reb in variants:
        sub = close_all.loc[:, cols]
        r = run(sub, nm, reb, f"{pool_tag}_reb{reb}")
        results.append(r)
        print(f"[done] {r['tag']}: reb {r['nreb']} 次, s2 {r['ret_s2']*100:+.1f}%, vt15 {r['ret_vt']*100:+.1f}%", flush=True)

    dates = close_all.index
    idx = np.flatnonzero((dates >= START) & (dates <= END))
    i0, i1 = int(idx[0]), int(idx[-1])
    ret_b = bench.iloc[i1] / bench.iloc[i0 - 1] - 1
    lines.append(f"- 交易日 {i1 - i0 + 1} 天；510300 买入持有 **{ret_b*100:+.1f}%**")
    lines.append("")
    lines.append("| 变体 | 换仓次数 | s2 满仓 | vt15 |")
    lines.append("|---|---|---|---|")
    for r in results:
        lines.append(f"| {r['tag']} | {r['nreb']} | {r['ret_s2']*100:+.1f}% | {r['ret_vt']*100:+.1f}% |")

    for r in results:
        lines.append("")
        lines.append(f"## {r['tag']}")
        lines.append("")
        lines.append("| 换仓日 | 持仓（名称 权重%） | s2 | vt15 敞口 |")
        lines.append("|---|---|---|---|")
        for d, hold, tot, expo in r["rows"]:
            hs = "<br>".join(f"{r['lab'](c)} {w*100:.0f}%" for c, w in hold)
            lines.append(f"| {d} | {hs} | {tot*100:.0f}% | {expo*100:.0f}% |")

    (RUN / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))
    print(f"[out] {RUN / 'report.md'}")


if __name__ == "__main__":
    main()
