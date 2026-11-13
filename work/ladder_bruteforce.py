"""分层(10层)仓位 + 纠错机制 暴力网格。

机制（所有指标共用一套骨架）：
  healthy 指标为真 → 满层 10（层=0.1 仓位）
  指标转假（下穿/转负）→ 一次减 K 层
  仍处假状态 → 每 N 个交易日再补减 1 层（补减总数上限 M 层）
  指标转真 → 直接回满 10 层（带各自缓冲，防抖）
  N=0 表示不做时间补减（纯分层阈值，对照组）

仓位 = 层数 × 0.1，不加 volcap（先看机制本身）；成本单边 5bp；t 收盘算 t+1 生效。

评估：每标的 train/test 的 d_calmar（vs 满仓）与 d_fixed（vs 同平均仓位固定仓），
然后跨标的取中位 + 单样本 t。组合数多，Bonferroni 门槛按组合数换算。

用法：python ladder_bruteforce.py [--limit N] [--quick]
输出：runs/2026-11-16_ladder/ladder_grid.csv
"""
from __future__ import annotations

import os
import sys
import time
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import SPLIT, TD, TRAIN_END, factors, load_universe, run_pos, stats  # noqa: E402
from single_asset_final import policy as final_policy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "2026-11-16_ladder"
OUT.mkdir(parents=True, exist_ok=True)
LAYERS = 10
COST = 0.0005


def healthy_set(F):
    """返回 {指标名: bool 数组}，True=健康(满层)。全部用已 shift 的因子，收盘即可算。"""
    c = F["close"]
    H = {
        # 动量族
        "mom20>0": F["mom20"] > 0,
        "mom60>0": F["mom60"] > 0,
        "mom120>0": F["mom120"] > 0,
        # 均线族
        "close>MA20": c > F["ma20"],
        "close>MA30": c > F["ma30"],
        "close>MA60": c > F["ma60"],
        "close>MA120": c > F["ma120"],
        "close>MA200": c > F["ma200"],
        # 通道/突破族
        "close>唐奇安20低": c > F["dn20"],
        "close>唐奇安55低": c > F["dn55"],
        "close>布林中轨": c > F["bb_mid"],
        "close>ATR吊灯3x": c > (F["hi20c"] - 3 * F["atr14"]),
        # 回撤族
        "dd>-8%": F["dd"] > -0.08,
        "dd>-15%": F["dd"] > -0.15,
        "dd>-25%": F["dd"] > -0.25,
        # 波动/量能族
        "volpct<0.80": F["volpct"] < 0.80,
        "额比>0.70": F["amtratio"] > 0.70,
        # 周线族
        "周close>周MA20": F["w_close"] > F["w_ma20"],
    }
    out = {}
    for k, v in H.items():
        if not isinstance(v, pd.Series):
            v = pd.Series(v, index=c.index)
        # 周线等低频指标 forward fill 到日频
        v = v.reindex(c.index, method="ffill") if len(v) != len(c) else v
        arr = v.to_numpy(dtype=float)
        arr = np.where(np.isnan(arr), 1.0, arr).astype(bool)  # 暖机期算健康
        out[k] = arr
    return out


def run_ladder(h: np.ndarray, K: int, N: int, M: int):
    """骨架状态机。返回各日层数(int)。"""
    n = len(h)
    layers = np.empty(n, dtype=np.int16)
    L = LAYERS
    off = False
    since = 0
    decayed = 0
    for i in range(n):
        if not h[i]:
            if not off:
                L = max(L - K, 0)
                off, since, decayed = True, 0, 0
            else:
                since += 1
                if N > 0 and since % N == 0 and decayed < M:
                    L = max(L - 1, 0)
                    decayed += 1
        else:
            if off:
                L = LAYERS
                off = False
        layers[i] = L
    return layers


def combine(layers: np.ndarray, volcap: np.ndarray | None = None) -> np.ndarray:
    pos = layers.astype(float) / LAYERS
    if volcap is not None:
        pos = pos * volcap
    return np.clip(pos, 0, 1)


def run_band(pos: np.ndarray, ret: pd.Series, band: float = 0.0):
    """带调仓阈值：目标仓位与当前持仓差 > band 才动手（band=0 即逐日对齐）。"""
    tgt = np.nan_to_num(pos.astype(float))
    held = np.empty(len(tgt))
    h = 0.0
    for i, t in enumerate(tgt):
        if abs(t - h) > band + 1e-12:
            h = t
        held[i] = h
    p = pd.Series(held, index=ret.index).shift(1).fillna(0.0)
    gross = p * ret.fillna(0.0)
    turn = p.diff().abs().fillna(p.abs())
    return gross - turn * COST, p, pd.Series(held, index=ret.index)


def eval_one(pos, ret, dates, key, mask_train, band=0.0):
    net, p, _ = run_band(pos, ret, band)
    rec = {}
    avg = float(np.nanmean(pos))
    for split, m in (("train", mask_train), ("test", ~mask_train), ("all", np.ones(len(pos), bool))):
        s = stats(net[m])
        b = stats(ret[m].fillna(0.0))
        fx = stats(avg * ret[m].fillna(0.0))          # 同平均仓位的固定仓（无成本）
        rec[f"{split}_d"] = s["calmar"] - b["calmar"]
        rec[f"{split}_dfix"] = s["calmar"] - fx["calmar"]
        rec[f"{split}_calmar"] = s["calmar"]
    rec["avg_pos"] = avg
    rec["turn"] = float(np.abs(p.diff()).fillna(0).sum() / (len(p) / TD))
    return rec


def main():
    t0 = time.time()
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    Ks = [3, 5, 10]
    Ns = [0, 1, 5, 10]
    Ms = [5, 10]
    combos = list(product(Ks, Ns, Ms))
    uni = load_universe()
    codes = list(uni.keys())
    if limit:
        codes = codes[:limit]
    print(f"标的 {len(codes)} × 组合 {len(combos)} × 指标 19", flush=True)

    rows = []
    for ci, code in enumerate(codes, 1):
        grp, df = uni[code]
        key = "ETF" if grp == "ETF" else "长样本"
        df = df[df["date"] >= SPLIT[key]].reset_index(drop=True)
        if len(df) < 500:
            continue
        F = factors(df)
        ret = F["ret"]
        dates = df["date"]
        tr = (dates <= pd.Timestamp(TRAIN_END[key])).to_numpy()
        volcap = (0.15 / F["vol60"]).clip(0.4, 1.0).fillna(1.0).to_numpy()
        H = healthy_set(F)

        # 参照：定稿策略
        fpos, _ = final_policy(F)
        r = eval_one(fpos.to_numpy(), ret, dates, key, tr, 0.0)
        r.update({f"b2_{k}": v for k, v in
                  eval_one(fpos.to_numpy(), ret, dates, key, tr, 0.02).items()})
        rows.append(dict(code=code, group=key, ind="_定稿三层", K=0, N=0, M=0, **r))

        for ind, h in H.items():
            for K, N, M in combos:
                lay = run_ladder(h, K, N, M)
                pos = combine(lay, volcap)
                r = eval_one(pos, ret, dates, key, tr, 0.0)
                r.update({f"b2_{k}": v for k, v in
                          eval_one(pos, ret, dates, key, tr, 0.02).items()})
                rows.append(dict(code=code, group=key, ind=ind, K=K, N=N, M=M, **r))
        if ci % 10 == 0:
            print(f"  {ci}/{len(codes)}  {time.time()-t0:.0f}s", flush=True)

    g = pd.DataFrame(rows)
    g.to_csv(OUT / "ladder_grid.csv", index=False, encoding="utf-8-sig", float_format="%.4f")

    print(f"\n全组合 {len(g)} 行，耗时 {time.time()-t0:.0f}s -> {OUT/'ladder_grid.csv'}")
    base = g[g.ind == "_定稿三层"]
    print("\n== 定稿参照（中位）==")
    for cc in ("", "b2_"):
        print(f"  [{ '阈值2%' if cc else '逐日调'}]", end="")
        for split in ("train", "test", "all"):
            print(f"  {split} d_fix {base[f'{cc}{split}_dfix'].median():+.3f}"
                  f"(正{(base[f'{cc}{split}_dfix']>0).mean():.0%})", end="")
        print(f"  仓位 {base['avg_pos'].median():.2f} 换手 {base[f'{cc}turn'].median():.1f}")

    def agg_of(pref):
        a = (g.groupby(["ind", "K", "N", "M"])
             .agg(test_dfix=(f"{pref}test_dfix", "median"),
                  test_d=(f"{pref}test_d", "median"),
                  all_dfix=(f"{pref}all_dfix", "median"),
                  pct=(f"{pref}test_dfix", lambda s: (s > 0).mean()),
                  t=(f"{pref}test_dfix", lambda s: s.mean() / s.std(ddof=1) * np.sqrt(len(s))),
                  pos=("avg_pos", "median"), turn=(f"{pref}turn", "median"), n=("code", "count"))
             .reset_index())
        return a[a.n >= 30].sort_values("test_dfix", ascending=False)

    agg = agg_of("")
    aggb = agg_of("b2_")
    agg.round(4).to_csv(OUT / "ladder_agg.csv", index=False, encoding="utf-8-sig")
    aggb.round(4).to_csv(OUT / "ladder_agg_band2.csv", index=False, encoding="utf-8-sig")
    print(f"\nBonferroni 参考：组合 {len(agg)} 个，|t| 门槛≈{3.0 if len(agg) > 100 else 3.5:.1f}")
    print("\n== Top 10（逐日调，按 test d_fixed 中位）==")
    print(agg.head(10).round(3).to_string(index=False))
    print("\n== Top 10（2% 调仓阈值）==")
    print(aggb.head(10).round(3).to_string(index=False))

    for nm, a in (("逐日调", agg), ("2%阈值", aggb)):
        print(f"\n== 同指标下 时间补减 的效果（{nm}，N=0 对照）==")
        tm = a.pivot_table(index=["ind", "K", "M"], columns="N", values="test_dfix")
        if 0 in tm.columns:
            for n in [c for c in tm.columns if c != 0]:
                diff = (tm[n] - tm[0]).dropna()
                print(f"  N={n}: 平均 {diff.mean():+.4f}，变好 {(diff>0).mean():.0%}，n={len(diff)}")

    print("\n== 每个指标的最优档（N=0 纯分层，2%阈值）==")
    best = (aggb[aggb.N == 0].sort_values("test_dfix", ascending=False)
            .groupby("ind").head(1).sort_values("test_dfix", ascending=False))
    print(best[["ind", "K", "test_dfix", "all_dfix", "pct", "pos", "turn"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
