"""单支通用策略：一套规则跑所有标的，不按标的调参；走前验证。

候选（只用 价格/周线/波动/回撤，参数固定）
  U0  满仓
  U1  MA60 半仓
  U2  趋势评分 = mean(收盘>MA60, 收盘>MA120, 周收>周MA20)，直接当仓位
  U3  评分阈值：≥2/3 → 1，=1/3 → 0.5，0 → 0
  U4  U3 × 波动上限 clip(0.20/vol60, 0.4, 1)
  U5  U3 × 回撤闸（-20% 半仓，回 -10% 复原）
  U6  投票四件套 mean(MA60清, mom60清, 周MA20清, dd15半)
  U7  min(MA60清, dd20半)
  U8  U5 × VT12
  U9  快慢切换：年线走平/向下 → mom20清（快）；年线向上 → min(MA120清, mom60清)（慢）
  U10 min(mom20清, MA120清)
  U11 mom20 清仓（网格头名）
  U12 mom20 半仓
  U13 min(mom20清, 周MA20清)
  U14 年线向上时 mom20 半仓，否则 mom20 清仓

切分
  ETF      ：train 2019-01~2022-12 / test 2023-01~
  指数+蓝筹：train 2007-01~2015-12 / test 2016-01~
规则在 train 上选，test 只报不选。
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import (OUT, SPLIT, TD, TRAIN_END, build_rules, factors, hyst,  # noqa: E402
                                  load_universe, run_pos, stats, to_daily)

# SPLIT / TRAIN_END 由 single_asset_harness 按环境变量给出（见 SA_* 说明）


@lru_cache(maxsize=1)
def R_():
    return build_rules()


def trend_score(F):
    c = F["close"]
    w = to_daily(F["w_close"], c.index) > to_daily(F["w_ma20"], c.index)
    s = ((c > F["ma60"]).astype(float) + (c > F["ma120"]).astype(float) + w.astype(float)) / 3.0
    return s.fillna(1.0)


def bull(F):
    """长期趋势：收盘在年线上，且年线 60 日斜率 > 0。"""
    return ((F["close"] > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)


def one(F, name):
    R = R_()
    if name == "U0 满仓":
        return pd.Series(1.0, index=F["close"].index)
    if name == "U1 MA60半仓":
        return R["A_MA60半仓"](F)
    if name == "U2 评分直用":
        return trend_score(F)
    if name == "U3 评分阈值":
        s = trend_score(F)
        return pd.Series(np.where(s >= 2 / 3, 1.0, np.where(s > 0, 0.5, 0.0)), index=s.index)
    if name == "U4 阈值×波动上限":
        return one(F, "U3 评分阈值") * (0.20 / F["vol60"]).clip(0.4, 1.0).fillna(1.0)
    if name == "U5 阈值×回撤闸":
        return one(F, "U3 评分阈值") * hyst(F["dd"] < -0.20, F["dd"] > -0.10, 0.5)
    if name == "U6 投票四件套":
        parts = [R["A_MA60清仓"](F), R["B_mom60清仓"](F), R["F_周MA20清仓"](F), R["D_dd15半仓"](F)]
        return pd.concat(parts, axis=1).mean(axis=1)
    if name == "U7 minMA60dd20":
        return pd.concat([R["A_MA60清仓"](F), R["D_dd20半仓"](F)], axis=1).min(axis=1)
    if name == "U8 阈值×回撤×VT12":
        return pd.concat([one(F, "U5 阈值×回撤闸"), R["C_VT12"](F)], axis=1).min(axis=1)
    if name == "U9 快慢切换":
        fast = R["B_mom20清仓"](F)
        slow = pd.concat([R["A_MA120清仓"](F), R["B_mom60清仓"](F)], axis=1).min(axis=1)
        return fast.where(~bull(F), slow)
    if name == "U10 min快慢":
        return pd.concat([R["B_mom20清仓"](F), R["A_MA120清仓"](F)], axis=1).min(axis=1)
    if name == "U11 mom20清仓":
        return R["B_mom20清仓"](F)
    if name == "U12 mom20半仓":
        return R["B_mom20半仓"](F)
    if name == "U13 mom20×周MA20":
        return pd.concat([R["B_mom20清仓"](F), R["F_周MA20清仓"](F)], axis=1).min(axis=1)
    if name == "U14 牛慢熊快":
        return R["B_mom20清仓"](F).where(~bull(F), R["B_mom20半仓"](F))
    raise KeyError(name)


CANDS = ["U0 满仓", "U1 MA60半仓", "U2 评分直用", "U3 评分阈值", "U4 阈值×波动上限",
         "U5 阈值×回撤闸", "U6 投票四件套", "U7 minMA60dd20", "U8 阈值×回撤×VT12",
         "U9 快慢切换", "U10 min快慢", "U11 mom20清仓", "U12 mom20半仓",
         "U13 mom20×周MA20", "U14 牛慢熊快"]


def load_all():
    uni = load_universe()
    out = {}
    for code, (grp, df) in uni.items():
        key = "ETF" if grp == "ETF" else "长样本"
        df = df[df["date"] >= SPLIT[key]]
        if len(df) < 400:
            continue
        out[code] = (key, df)
    return out


def window(pos, ret, df, split, key):
    tr = (df["date"] <= pd.Timestamp(TRAIN_END[key])).to_numpy()
    mask = tr if split == "train" else ~tr
    net, p = run_pos(pos, ret)
    st = stats(net[mask])
    b = stats(ret[mask].fillna(0.0))
    fixed = stats(p[mask].mean() * ret[mask].fillna(0.0))
    return st, b, fixed, float(p[mask].mean())


def main():
    data = load_all()
    recs = []
    for name in CANDS:
        for code, (key, df) in data.items():
            F = factors(df)
            ret = F["ret"]
            pos = one(F, name).clip(0, 1)
            for split in ("train", "test"):
                st, b, fixed, avg = window(pos, ret, df, split, key)
                recs.append(dict(cand=name, group=key, code=code, split=split,
                                 calmar=st["calmar"], cagr=st["cagr"], mdd=st["mdd"],
                                 base_calmar=b["calmar"], d_calmar=st["calmar"] - b["calmar"],
                                 d_fixed=st["calmar"] - fixed["calmar"], avg_pos=avg))
    r = pd.DataFrame(recs)
    r.to_csv(OUT / "universal_candidates.csv", index=False, encoding="utf-8-sig")

    agg = (r.groupby(["cand", "split"])
             .agg(n=("code", "nunique"), med_d=("d_calmar", "median"),
                  pos_rate=("d_calmar", lambda s: (s > 0).mean()),
                  med_d_fixed=("d_fixed", "median"),
                  med_calmar=("calmar", "median"), avg_pos=("avg_pos", "median"))
             .reset_index())
    agg.round(3).to_csv(OUT / "universal_summary.csv", index=False, encoding="utf-8-sig")
    print("== 合并口径：中位 d_calmar（train / test）==")
    print(agg.round(3).pivot(index="cand", columns="split", values="med_d").to_string())
    print("\n== 分组的 train / test 中位 d_calmar ==")
    piv = r.pivot_table(index=["cand", "group"], columns="split", values="d_calmar", aggfunc="median")
    print(piv.round(3).to_string())

    # oracle：train 内每标的最优基础规则 → test 表现
    o = []
    R = R_()
    names = list(R) + ["满仓"]
    for code, (key, df) in data.items():
        F = factors(df)
        ret = F["ret"]
        cal_tr, cal_te = {}, {}
        for nm in names:
            pos = pd.Series(1.0, index=F["close"].index) if nm == "满仓" else R[nm](F).clip(0, 1)
            st_tr, _, _, _ = window(pos, ret, df, "train", key)
            st_te, b_te, _, _ = window(pos, ret, df, "test", key)
            cal_tr[nm], cal_te[nm] = st_tr["calmar"], st_te["calmar"] - b_te["calmar"]
        best = max(cal_tr, key=lambda k: cal_tr[k] if np.isfinite(cal_tr[k]) else -9)
        o.append(dict(group=key, code=code, best=best, train_calmar=cal_tr[best],
                      test_d=cal_te[best]))
    o = pd.DataFrame(o)
    o.to_csv(OUT / "universal_oracle.csv", index=False, encoding="utf-8-sig")
    print("\n== oracle（train 选最优，test 只报）==")
    print(o.groupby("group")[["train_calmar", "test_d"]].median().round(3).to_string())
    print("\nETF oracle 最常被选中的规则：")
    print(o[o.group == "ETF"]["best"].value_counts().head(8).to_string())
    print("\n长样本 oracle 最常被选中的规则：")
    print(o[o.group == "长样本"]["best"].value_counts().head(8).to_string())


if __name__ == "__main__":
    main()
