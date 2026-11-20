# -*- coding: utf-8 -*-
"""时代切片：2013 起 / 2017 起两条净值曲线，各切「起点段」与「2019+」两段。

口径与实盘账本一致（k=5、reb=10、rolling、逆波动、5bp、delay=1；VT15=min(1,15%/vol60)）。
三条线：S2（满仓）/ VT15（S2+波动目标）/ 等权（全池等权、同 5bp、同调仓节奏）。
两个池：宽 ETF 92 只（有选池/生存偏差）、指数代理 15 只（无选池偏差，但不可直接交易）。

段内统计 = 从段首起重新归一算年化、段内最大回撤、卡玛；不回填段外信息。
2019+ 段统一标「样本内/开发期」，不再称样本外。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = r"E:\Code\quant"
OUT = os.path.join(ROOT, "runs", "2026-11-20_era_split")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "runs", "2026-11-20_portfolio_multiwindow"))

import bt  # noqa: E402
from position.engine import Config, Fixed, VolTarget, features, perf, run_with_base  # noqa: E402

COST = 0.0005
BIG_K = 10_000
SPLIT = pd.Timestamp("2019-01-01")

ARMS = {
    "S2": dict(univ="mom", wmode="invvol", rule=lambda: Fixed(1.0)),
    "VT15": dict(univ="mom", wmode="invvol", rule=lambda: VolTarget(0.15, 60, every=1)),
    "等权": dict(univ="all", wmode="equal", rule=lambda: Fixed(1.0)),
}


def f_for(f: dict, univ: str) -> dict:
    if univ == "mom":
        return f
    f2 = dict(f)
    f2["cand"] = f["valid"] & f["rm"].notna()
    return f2


def cfg_for(arm: dict) -> Config:
    return Config(k=BIG_K if arm["univ"] == "all" else 5, reb=10, reb_mode="rolling",
                  weight_mode=arm["wmode"], buy_cost=COST, sell_cost=COST,
                  min_names=8, rule=arm["rule"]())


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    etf, bad = bt.clean(bt.load_etf_panel())
    idx = bt.load_idx_panel()
    pools = [("宽ETF池92", etf), ("指数代理15", idx)]
    starts = [("2013起", "2013-01-01"), ("2017起", "2017-01-01")]

    rows = []
    for pname, close in pools:
        f = features(close, min_names=8)
        dates = close.index
        for sname, sdate in starts:
            lo = max(int(dates.searchsorted(pd.Timestamp(sdate))), int(f["start"]))
            for arm_name, arm in ARMS.items():
                res = run_with_base(close, f_for(f, arm["univ"]), cfg_for(arm))
                r, e = res["ret"], res["expo"]
                hi_split = int(dates.searchsorted(SPLIT))
                # 两段：起点→2018 年末；2019-01-01→末
                for label, a, b, attr in (
                    (f"{dates[lo].year}–2018", lo, hi_split, "样本内/开发期"),
                    ("2019+", max(lo, hi_split), len(dates),
                     "样本内/开发期（2025-01 起为留出段，仅用一次）"),
                ):
                    if b - a < 240:      # 不足一年不报
                        continue
                    m = perf(r[a:b])
                    rows.append(dict(
                        池=pname, 起点=sname, 段=label, 样本属性=attr, 线=arm_name,
                        起=str(dates[a].date()), 止=str(dates[b - 1].date()),
                        年数=round((b - a) / 252.0, 2),
                        年化=m["cagr"], 回撤=m["mdd"], 卡玛=m["calmar"], 夏普=m["sharpe"],
                        均仓=float(np.mean(e[a:b])),
                    ))
            print(f"[{pname} {sname}] 起点 {dates[lo].date()} 完成", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "era_split.csv"), index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 260)
    for pname in df["池"].unique():
        for seg in df["段"].unique():
            d = df[(df["池"] == pname) & (df["段"] == seg)]
            if d.empty:
                continue
            print(f"\n=== {pname} · {seg} ===")
            piv = d.pivot_table(index=["起点", "线"], values=["年化", "回撤", "卡玛", "夏普", "均仓"])
            print(piv.to_string(float_format=lambda x: "%.4f" % x))

    # 关键对比：S2/VT15 相对等权的卡玛差
    cmp_rows = []
    for (pname, sname, seg), g in df.groupby(["池", "起点", "段"]):
        base = g[g["线"] == "等权"]
        if base.empty:
            continue
        b = base.iloc[0]
        for line in ("S2", "VT15"):
            x = g[g["线"] == line].iloc[0]
            cmp_rows.append(dict(池=pname, 起点=sname, 段=seg, 线=line,
                                 d年化=x["年化"] - b["年化"], d卡玛=x["卡玛"] - b["卡玛"],
                                 d回撤=x["回撤"] - b["回撤"]))
    cd = pd.DataFrame(cmp_rows)
    cd.to_csv(os.path.join(OUT, "era_diff.csv"), index=False, encoding="utf-8-sig")
    print("\n=== 相对等权的差值 ===")
    print(cd.to_string(index=False, float_format=lambda x: "%.4f" % x))

    meta = dict(
        id="SPLIT-01", date="2026-11-20",
        purpose="按 2013-2018 / 2019+ 切段，检查『打不过等权』是否只出现在 2019+ 段；指数池做无选池偏差对照",
        cost_one_way_bp=5, delay=1,
        params=dict(k=5, reb=10, reb_mode="rolling", weight_mode="invvol", min_names=8,
                    vt=dict(target=0.15, window=60, every=1)),
        arms=list(ARMS),
        pools=[dict(name="宽ETF池92", source="data/cache_ohlc_fullhist", n=92, snapshot="2026-11-13"),
               dict(name="指数代理15", source="data/cache2007 idx_*", n=15, snapshot="2026-11-19")],
        starts=["2013-01-01", "2017-01-01"], split="2019-01-01",
        label_rule="2019+ 一律标『样本内/开发期』；2025-01 起为留出段、仅用过一次",
        artifacts=["era_split.csv", "era_diff.csv", "REPORT.md"],
        out_of_sample=False,
    )
    with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
