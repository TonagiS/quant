"""截面广度 + 波动扩张：单支定稿策略的两个"环境层"增量。

B1 广度温和：expo × clip(0.5 + 0.5×breadth60, 0.5, 1)，breadth60 = 全池 close>MA60 占比
B2 广度强硬：expo × clip(breadth60, 0.3, 1)
B3 广度只做"复仓闸门"：mom20 从负转正时，若 breadth<40% 则只给半仓
E1 波动扩张：vol20 > 1.3×vol60 时 taper ×0.7
E2 波动扩张+广度：E1 与 B1 同时
基线 = 定稿策略 G1 × volcap(0.15,0.4) × 趋势衰减
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_final import policy  # noqa: E402
from single_asset_harness import SPLIT, TD, TRAIN_END, factors, load_universe, run_pos, stats  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TAG = __import__("os").environ.get("SA_TAG", "base")
OUT = ROOT / "runs" / f"2026-11-16_breadth_{TAG}"
OUT.mkdir(parents=True, exist_ok=True)


def breadth_series(uni, dates):
    rows = {}
    for code, (grp, df) in uni.items():
        F = factors(df)
        above = (F["close"] > F["ma60"]).astype(float)
        above[F["ma60"].isna()] = np.nan
        rows[code] = above.reindex(dates)
    M = pd.DataFrame(rows)
    return M.mean(axis=1, skipna=True)


def seg(net, ret, mask):
    a, b = stats(net[mask]), stats(ret[mask].fillna(0.0))
    return a["cagr"], a["mdd"], a["calmar"] - b["calmar"], a["calmar"]


def main():
    uni = load_universe()
    all_dates = sorted(set().union(*[set(df["date"]) for _, df in uni.values()]))
    br = breadth_series(uni, pd.DatetimeIndex(all_dates)).ffill()
    print(f"池 {len(uni)} 只，广度覆盖 {br.notna().sum()} 天，"
          f"广度中位 {br.median():.0%}，最低 {br.min():.0%}")

    rows = []
    for code, (grp, df) in uni.items():
        key = "ETF" if grp == "ETF" else "长样本"
        if len(df) < 400:
            continue
        F = factors(df)
        base, T = policy(F)
        b = br.reindex(F["close"].index).ffill().fillna(0.5)
        volr = (F["vol20"] / F["vol60"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
        variants = {
            "基线": base,
            "B1 广度温和": base * (0.5 + 0.5 * b).clip(0.5, 1.0),
            "B2 广度强硬": base * b.clip(0.3, 1.0),
            "B3 广度复仓闸门": base.where(~((F["mom20"] > 0) & (F["mom20"].shift(1) <= 0) & (b < 0.4)),
                                        base * 0.5),
            "E1 波动扩张": base * np.where(volr > 1.3, 0.7, 1.0),
            "E2 波动扩张+广度": base * np.where(volr > 1.3, 0.7, 1.0) * (0.5 + 0.5 * b).clip(0.5, 1.0),
        }
        te = (df["date"] > pd.Timestamp(TRAIN_END[key])).to_numpy()
        tr = ~te
        for nm, pos in variants.items():
            net, p = run_pos(pd.Series(pos, index=F["close"].index), F["ret"])
            ca, md, d_all, cal = seg(net, F["ret"], np.ones(len(df), bool))
            _, _, d_tr, _ = seg(net, F["ret"], tr)
            _, _, d_te, _ = seg(net, F["ret"], te)
            rows.append(dict(group=key, code=code, arm=nm, cagr=ca, mdd=md, calmar=cal,
                             d=d_all, d_tr=d_tr, d_te=d_te, avg_pos=float(np.nanmean(p))))

    r = pd.DataFrame(rows)
    r.round(4).to_csv(OUT / "breadth_variants.csv", index=False, encoding="utf-8-sig")
    print("\n== 中位（相对满仓 calmar 增量）==")
    piv = r.groupby("arm").agg(n=("code", "size"), d=("d", "median"), d_tr=("d_tr", "median"),
                               d_te=("d_te", "median"), pos=("avg_pos", "median"),
                               calmar=("calmar", "median")).round(3)
    print(piv.to_string())
    print("\n== 分组 ==")
    print(r.pivot_table(index="arm", columns="group", values="d", aggfunc="median").round(3).to_string())
    print(r.pivot_table(index="arm", columns="group", values="d_te", aggfunc="median").round(3).to_string())


if __name__ == "__main__":
    main()
