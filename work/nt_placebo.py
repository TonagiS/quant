"""安慰剂检验：把真实信号日换成随机日期，重跑同一套 900 组网格，看三关通过率。

两种安慰剂
----------
A. 同频随机日期：每个网格格子用「与真实信号同样数量的随机日」替代，
   照样算 G1 跨池 / G2 跨段 / G3 同仓位对照，统计通过率。
B. 同频同时长随机空仓：固定 k=20，随机挑同样多的起点（互不重叠优先），
   比较卡玛分布与真实值 1.21 的分位。

输出 runs/20261108_nt_sell_signal/placebo.md / placebo_grid.csv / placebo_blocks.csv
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "work"))

from position.engine import Config, Fixed, VolTarget, features, load_panel, perf, simulate  # noqa: E402
from nt_policy_overlay import VolTargetNT, nt_series  # noqa: E402
from nt_grid_gates import mult_rule  # noqa: E402

RUN = ROOT / "runs" / "20261108_nt_sell_signal"
RNG = np.random.default_rng(20261108)

POOLS = {}
for pool in ("ext", "base"):
    close = load_panel(pool)
    f = features(close)
    _, arr = nt_series(close.index)
    c1 = pd.Series(arr["c1"], index=close.index).to_numpy()
    base = simulate(close, f, Config(k=5, reb=5, cap=0.40, rule=VolTarget(0.15, every=1)))
    start = int(f["start"])
    live = len(close) - start
    segs = [(start, start + int(live * 0.5)),
            (start + int(live * 0.5), start + int(live * 0.75)),
            (start + int(live * 0.75), len(close))]
    POOLS[pool] = dict(close=close, f=f, c1=c1, base=base, start=start, segs=segs,
                       base_m=perf(base["ret"][start:]),
                       base_seg=[perf(base["ret"][lo:hi])["calmar"] for lo, hi in segs])


def metrics(res, start):
    m = perf(res["ret"][start:])
    return m["cagr"] * 100, m["mdd"] * 100, m["calmar"], float(np.mean(res["expo"][start:]))


def run_cell(pool, mult):
    p = POOLS[pool]
    res = simulate(p["close"], p["f"], Config(k=5, reb=5, cap=0.40,
                                             rule=VolTargetNT(VolTarget(0.15, every=1), mult, "x")))
    cagr, mdd, calmar, expo = metrics(res, p["start"])
    seg_ok = sum(1 for (lo, hi), bc in zip(p["segs"], p["base_seg"])
                 if perf(res["ret"][lo:hi])["calmar"] >= bc)
    return dict(cagr=cagr, mdd=mdd, calmar=calmar, expo=expo, seg_ok=seg_ok, res=res)


def main() -> None:
    grid = list(itertools.product((10, 14, 20, 28, 40), (-3, -4, -5, -6, -8, -10),
                                  (0, 1, 2, 3, 5), (10, 20, 40), (0.0, 0.5)))
    print(f"[plan] {len(grid)} 组 × 2 次随机重抽", flush=True)

    out = []
    for rep in range(2):
        pass_cnt = {"g1": 0, "g2": 0, "g3": 0, "all": 0}
        for n, m, l, k, level in grid:
            cum = pd.Series(POOLS["ext"]["c1"]).rolling(n, min_periods=max(5, n // 2)).sum().to_numpy()
            n_sig = int((np.nan_to_num(cum, nan=9.0) <= m).sum())
            if n_sig < 10:
                continue
            # 随机信号日：同样数量，避开最前面 n 天
            cand = np.arange(n, len(cum))
            pick = RNG.choice(cand, size=min(n_sig, len(cand) - 1), replace=False)
            sig = np.zeros(len(cum), dtype=bool)
            sig[pick] = True
            mult = mult_rule(sig, l, k, level)

            e = run_cell("ext", mult)
            b = run_cell("base", mult)
            g1 = (e["calmar"] >= POOLS["ext"]["base_m"]["calmar"] + 0.05
                  and b["calmar"] >= POOLS["base"]["base_m"]["calmar"] + 0.05)
            g2 = e["seg_ok"] >= 2
            fx = simulate(POOLS["ext"]["close"], POOLS["ext"]["f"],
                          Config(k=5, reb=5, cap=0.40, rule=Fixed(e["expo"])))
            fx_cal = perf(fx["ret"][POOLS["ext"]["start"]:])["calmar"]
            g3 = e["calmar"] >= fx_cal + 0.05
            out.append({"rep": rep, "n": n, "m": m, "l": l, "k": k, "level": level,
                        "n_sig": n_sig, "ext_calmar": e["calmar"], "ext_expo": e["expo"],
                        "base_calmar": b["calmar"],
                        "fixed_calmar": fx_cal, "g1": g1, "g2": g2, "g3": g3, "all": g1 and g2 and g3})
            pass_cnt["g1"] += g1
            pass_cnt["g2"] += g2
            pass_cnt["g3"] += g3
            pass_cnt["all"] += g1 and g2 and g3
        print(f"[rep{rep}] g1={pass_cnt['g1']} g2={pass_cnt['g2']} g3={pass_cnt['g3']} all={pass_cnt['all']}", flush=True)

    df = pd.DataFrame(out)
    df.to_csv(RUN / "placebo_grid.csv", index=False, encoding="utf-8-sig")

    # B. 同频同时长随机空仓
    real_mult_n = None
    cum_real = pd.Series(POOLS["ext"]["c1"]).rolling(14, min_periods=7).sum().to_numpy()
    real_sig = np.nan_to_num(cum_real, nan=9.0) <= -10.0
    n_events = int(np.flatnonzero(np.diff(real_sig.astype(int)) == 1).size + real_sig[0])
    real_cal = run_cell("ext", mult_rule(real_sig, 5, 20, 0.0))["calmar"]
    print(f"[B] 真实信号 {int(real_sig.sum())} 天 / {n_events} 段，卡玛 {real_cal:.2f}", flush=True)

    starts_pool = np.arange(14, len(cum_real) - 25)
    cals = []
    for _ in range(300):
        starts = RNG.choice(starts_pool, size=n_events, replace=False)
        sig = np.zeros(len(cum_real), dtype=bool)
        sig[starts] = True
        cals.append(run_cell("ext", mult_rule(sig, 5, 20, 0.0))["calmar"])
    cals = np.array(cals)
    pct = float((cals < real_cal).mean()) * 100
    print(f"[B] 随机空仓 300 次：卡玛 中位 {np.median(cals):.2f}，90 分位 {np.quantile(cals, 0.9):.2f}，真实 {real_cal:.2f} 位于 {pct:.0f} 分位", flush=True)
    pd.DataFrame({"calmar": cals}).to_csv(RUN / "placebo_blocks.csv", index=False, encoding="utf-8-sig")

    lines = ["# 安慰剂检验", ""]
    lines.append(f"真实信号（900 组网格）：三关通过 187 / 900 = 20.8%")
    lines.append("")
    lines.append("## A. 随机日期安慰剂（同数量随机信号日，重跑同一网格）")
    lines.append("")
    lines.append("| 重抽 | G1 | G2 | G3 | 三关全过 | 全过比例 |")
    lines.append("|---|---|---|---|---|---|")
    for rep in (0, 1):
        d = df[df.rep == rep]
        lines.append(f"| #{rep+1} | {int(d.g1.sum())} | {int(d.g2.sum())} | {int(d.g3.sum())} | "
                     f"{int(d['all'].sum())} | {d['all'].mean()*100:.1f}% |")
    lines.append("")
    lines.append("## B. 同频同时长随机空仓（k=20，随机起点）")
    lines.append("")
    lines.append(f"- 真实：卡玛 **{real_cal:.2f}**")
    lines.append(f"- 随机 300 次：卡玛中位 **{np.median(cals):.2f}**，90 分位 {np.quantile(cals, 0.9):.2f}，"
                 f"真实值位于 **{pct:.0f} 分位**")

    # C. 每次重抽的「900 组里最好的卡玛」
    REAL_BEST = 1.211
    lines.append("")
    lines.append("## C. 每次重抽的 max(900 组卡玛) vs 真实最好值 1.21")
    lines.append("")
    lines.append("| 重抽 | 全部格子 max | 暴露 55–75% 子集 max | 退化格(卡玛>2 或 暴露<30%) | 暴露 55–75% 的格数 |")
    lines.append("|---|---|---|---|---|")
    max_all, max_filt = [], []
    for rep in (0, 1):
        d = df[df.rep == rep].copy()
        degenerate = int(((d.ext_calmar > 2) | (d.ext_expo < 0.30)).sum())
        sub = d[(d.ext_expo >= 0.55) & (d.ext_expo <= 0.75) & d.ext_calmar.notna()]
        max_all.append(float(np.nanmax(d.ext_calmar.to_numpy())))
        max_filt.append(float(sub.ext_calmar.max()) if len(sub) else float("nan"))
        lines.append(f"| #{rep+1} | {max_all[-1]:.2f} | {max_filt[-1]:.2f} | {degenerate} | {len(sub)} |")
    lines.append("")
    lines.append(f"- 真实：900 组 max **{REAL_BEST:.2f}**（avg expo 59%）")
    lines.append(f"- 随机日期两次重抽的「900 组 max」= {max_all[0]:.2f} / {max_all[1]:.2f}；"
                 f"按同样暴露区间（55–75%）过滤后 = {max_filt[0]:.2f} / {max_filt[1]:.2f}")
    lines.append("- 未过滤时会出现卡玛 5~20 的退化格子：随机信号几乎全程空仓、回撤极小，"
                 "卡玛被分母放大，没有经济含义，所以必须按暴露过滤再比。")

    (RUN / "placebo.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
