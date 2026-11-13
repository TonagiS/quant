"""对 n=14 / m=-10% / l=5 / k=20 / 空仓 这一组，按 PREREG 的每条判据逐项打分。

判据来源：position/PREREG.md（H1/H2/H4 的通用口径）+ POLICY_v1 的三关（跨池/跨段/同仓位对照）。
输出 runs/20261108_nt_sell_signal/audit_n14m10l5.md
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "work"))

from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, features, load_panel, monthly, perf, simulate,
    slice_metrics, stationary_bootstrap_ci,
)
from nt_policy_overlay import VolTargetNT, nt_series  # noqa: E402
from nt_grid_gates import mult_rule  # noqa: E402

RUN = ROOT / "runs" / "20261108_nt_sell_signal"
PARAMS = {"n": 14, "m": -10.0, "l": 5, "k": 20, "level": 0.0}


def build(pool: str):
    close = load_panel(pool)
    f = features(close)
    _, arr = nt_series(close.index)
    c1 = pd.Series(arr["c1"], index=close.index)
    cum = c1.rolling(PARAMS["n"], min_periods=max(5, PARAMS["n"] // 2)).sum().to_numpy()
    sig = np.nan_to_num(cum, nan=9.0) <= PARAMS["m"]
    mult = mult_rule(sig, PARAMS["l"], PARAMS["k"], PARAMS["level"])
    cfg = Config(k=5, reb=5, cap=0.40, rule=VolTargetNT(VolTarget(0.15, every=1), mult, "nt"))
    res = simulate(close, f, cfg)
    base = simulate(close, f, Config(k=5, reb=5, cap=0.40, rule=VolTarget(0.15, every=1)))
    return close, f, res, base, sig


def main() -> None:
    out = []
    detail = {}
    for pool in ("ext", "base"):
        close, f, res, base, sig = build(pool)
        start = int(f["start"])
        live = len(close) - start
        bounds = [(start, start + int(live * 0.5)),
                  (start + int(live * 0.5), start + int(live * 0.75)),
                  (start + int(live * 0.75), len(close))]
        sm = slice_metrics(res, start, len(close))
        bm = slice_metrics(base, start, len(close))
        fx = simulate(close, f, Config(k=5, reb=5, cap=0.40, rule=Fixed(sm["avg_expo"])))
        fxm = slice_metrics(fx, start, len(close))
        seg = [(perf(res["ret"][lo:hi])["calmar"], perf(base["ret"][lo:hi])["calmar"]) for lo, hi in bounds]
        detail[pool] = dict(sm=sm, bm=bm, fxm=fxm, seg=seg, close=close, f=f, res=res, base=base, start=start)

        print(f"[{pool}] 年化 {sm['cagr']*100:.2f}% (基线 {bm['cagr']*100:.2f}%)  回撤 {sm['mdd']*100:.2f}% "
              f"(基线 {bm['mdd']*100:.2f}%)  卡玛 {sm['calmar']:.2f} (基线 {bm['calmar']:.2f})  "
              f"同仓位固定 {fxm['calmar']:.2f}", flush=True)
        print(f"[{pool}] 平均仓位 {sm['avg_expo']*100:.0f}%  双边换手 {sm['turn_two_way']:.1f}x  成本拖累 {sm['cost_drag']*100:.2f}pp/年", flush=True)
        print(f"[{pool}] 三段卡玛: " + " / ".join(f"{a:.2f} vs {b:.2f}" for a, b in seg), flush=True)

    # bootstrap：相对基线
    ci = {}
    for pool in ("ext", "base"):
        d = detail[pool]
        dates = d["close"].index[d["start"]:]
        ra = monthly(d["res"]["ret"][d["start"]:], dates, d["start"])
        rb = monthly(d["base"]["ret"][d["start"]:], dates, d["start"])
        ci[pool] = stationary_bootstrap_ci(ra, rb)
        print(f"[{pool}] bootstrap Sharpe差 下沿 {ci[pool]['sharpe_lo']:+.3f} 中位 {ci[pool]['sharpe_med']:+.3f} | "
              f"CAGR差 下沿 {ci[pool]['cagr_lo']*100:+.2f}pp 中位 {ci[pool]['cagr_med']*100:+.2f}pp", flush=True)

    # 逐年
    yearly = {}
    for pool in ("ext",):
        d = detail[pool]
        r = pd.Series(d["res"]["ret"][d["start"]:], index=d["close"].index[d["start"]:])
        b = pd.Series(d["base"]["ret"][d["start"]:], index=d["close"].index[d["start"]:])
        for y in sorted({t.year for t in r.index}):
            ry = (np.prod(1 + r[r.index.year == y].to_numpy()) - 1) * 100
            by = (np.prod(1 + b[b.index.year == y].to_numpy()) - 1) * 100
            yearly[y] = (ry, by)

    e, b = detail["ext"], detail["base"]
    gates = []

    def gate(name, ok, note):
        gates.append({"判据": name, "结果": "过" if ok else "不过", "数值": note})

    gate("G1 跨池卡玛 ≥ 基线+0.05",
         e["sm"]["calmar"] >= e["bm"]["calmar"] + 0.05 and b["sm"]["calmar"] >= b["bm"]["calmar"] + 0.05,
         f"ext {e['sm']['calmar']:.2f} vs {e['bm']['calmar']:.2f}；base {b['sm']['calmar']:.2f} vs {b['bm']['calmar']:.2f}")
    seg_ok_e = sum(1 for a, bb in e["seg"] if a >= bb)
    gate("G2 跨段 ≥2/3 段卡玛不差于基线", seg_ok_e >= 2, f"ext {seg_ok_e}/3 段")
    gate("G3 卡玛 ≥ 同平均仓位固定对照+0.05",
         e["sm"]["calmar"] >= e["fxm"]["calmar"] + 0.05,
         f"{e['sm']['calmar']:.2f} vs 固定 {e['fxm']['calmar']:.2f}")
    gate("G4a Sharpe 差 90% 下沿 > -0.05",
         ci["ext"]["sharpe_lo"] > -0.05, f"{ci['ext']['sharpe_lo']:+.3f}")
    gate("G4b CAGR 差 90% 下沿 > 0",
         ci["ext"]["cagr_lo"] > 0, f"{ci['ext']['cagr_lo']*100:+.2f}pp")
    gate("H4 年化不低于基线 2pp", e["sm"]["cagr"] >= e["bm"]["cagr"] - 0.02,
         f"{e['sm']['cagr']*100:.2f}% vs {e['bm']['cagr']*100:.2f}%")
    gate("H2 成本拖累 ≤ +0.30pp/年", e["sm"]["cost_drag"] <= e["bm"]["cost_drag"] + 0.003,
         f"{e['sm']['cost_drag']*100:.2f}pp vs {e['bm']['cost_drag']*100:.2f}pp")
    gate("H5 平均仓位 ≥ 60%", e["sm"]["avg_expo"] >= 0.60, f"{e['sm']['avg_expo']*100:.0f}%")
    worst = min(yearly.items(), key=lambda kv: kv[1][0] - kv[1][1])
    gate("逐年：最差年份不落后基线超 3pp", (worst[1][0] - worst[1][1]) >= -3.0,
         f"{worst[0]} 年 {worst[1][0]:+.1f}% vs 基线 {worst[1][1]:+.1f}%")

    df = pd.DataFrame(gates)
    df.to_csv(RUN / "audit_n14m10l5.csv", index=False, encoding="utf-8-sig")
    lines = ["# 逐条判据：n=14 / m=-10% / l=5 / k=20 / 空仓", ""]
    lines.append("| 判据 | 结果 | 数值 |")
    lines.append("|---|---|---|")
    for g in gates:
        lines.append(f"| {g['判据']} | {g['结果']} | {g['数值']} |")
    lines.append("")
    lines.append("## 逐年（ext）")
    lines.append("")
    lines.append("| 年 | 规则 | 基线 | 差 |")
    lines.append("|---|---|---|---|")
    for y, (ry, by) in sorted(yearly.items()):
        lines.append(f"| {y} | {ry:+.1f}% | {by:+.1f}% | {ry - by:+.1f}pp |")
    (RUN / "audit_n14m10l5.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print()
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
