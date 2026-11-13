"""「多跑跑挑最好」到底多危险 —— 自查实验（2026-11-14）

做三件事：
  A. 真实数据：在一大把均线变体里挑样本内最好的，看它在样本外还剩下什么
  B. 排名持续性：样本内排名 vs 样本外排名的 Spearman，判断挑参有没有信息量
  C. 零假设：把真实收益做 block bootstrap（保留动量结构），在伪路径上重复同一套
     「挑最好」流程，量化选择偏差本身能刷出多少年化

输出：runs/2026-11-14_overfit_demo/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "work"))
from bt_ma5_ma20 import load_series, run, stats, target_weight  # noqa: E402

OUT = ROOT / "runs" / "2026-11-14_overfit_demo"
OUT.mkdir(parents=True, exist_ok=True)

COST = 0.0005
FAMILIES = ["full", "gate", "ma60", "cross", "hold20", "hold5"]
SHORTS = [3, 5, 8, 10, 13]
LONGS = [10, 15, 20, 30, 60]
MINUS_LEN = 60

GRID = [(f, s, l) for f in FAMILIES for s in SHORTS for l in LONGS if s < l]


def seg_stats(close: pd.Series, w: pd.Series, a: int, b: int) -> dict:
    c = close.iloc[a:b]
    ww = w.iloc[a:b]
    if len(c) < 60:
        return {"ann": np.nan, "mdd": np.nan, "calmar": np.nan, "sharpe": np.nan}
    res = run(c, ww, COST)
    st = stats(res, c.pct_change().fillna(0.0).to_numpy())
    return {k: st[k] for k in ("ann", "mdd", "calmar", "sharpe")}


def scan(close: pd.Series, split: int, grid=None) -> pd.DataFrame:
    """split 前后的两段分别评估全部组合。"""
    grid = grid or GRID
    rows = []
    for f, s, l in grid:
        w = target_weight(close, f, s, l)
        dev = seg_stats(close, w, 0, split)
        val = seg_stats(close, w, split, len(close))
        rows.append({"family": f, "short": s, "long": l,
                     **{f"dev_{k}": v for k, v in dev.items()},
                     **{f"val_{k}": v for k, v in val.items()}})
    return pd.DataFrame(rows)


def pick_and_check(df: pd.DataFrame, metric="dev_ann") -> dict:
    d = df.dropna(subset=[metric, "val_ann"]).copy()
    best = d.loc[d[metric].idxmax()]
    d["dev_rank"] = d[metric].rank(ascending=False)
    d["val_rank"] = d["val_ann"].rank(ascending=False)
    rho = d["dev_rank"].corr(d["val_rank"], method="spearman")
    return {
        "best": {k: (float(best[k]) if k not in ("family",) else best[k])
                 for k in ("family", "short", "long", metric, "val_ann", "dev_mdd", "val_mdd")},
        "val_median_ann": float(d["val_ann"].median()),
        "val_median_mdd": float(d["val_mdd"].median()),
        "best_val_rank_pct": float(d.loc[d[metric].idxmax(), "val_rank"] / len(d)),
        "spearman": float(rho),
        "n": int(len(d)),
    }


def block_bootstrap(close: pd.Series, n_paths: int, block: int, seed: int = 7) -> np.ndarray:
    """对日收益做块状重采样，生成等长的伪价格路径（保留短期动量）。"""
    r = close.pct_change().fillna(0.0).to_numpy()
    r = r[1:]
    T = len(r)
    rng = np.random.default_rng(seed)
    out = np.ones((n_paths, T + 1))
    for p in range(n_paths):
        chunks = []
        while sum(len(c) for c in chunks) < T:
            i = rng.integers(0, T - block)
            chunks.append(r[i:i + block])
        rr = np.concatenate(chunks)[:T]
        out[p, 1:] = np.cumprod(1.0 + rr)
    return out


def null_test(close: pd.Series, split: int, n_paths: int = 60) -> dict:
    """伪路径上重复「挑最好」，统计样本内/样本外的选择偏差。"""
    grid = [(f, s, l) for f in ["full", "gate", "cross", "hold20"]
            for s in [3, 5, 8, 10] for l in [10, 15, 30, 60] if s < l]
    paths = block_bootstrap(close, n_paths, 20)
    dev_best, val_of_best, val_med, dev_med = [], [], [], []
    for p in range(n_paths):
        px = pd.Series(paths[p], index=close.index[:len(paths[p])])
        rows = []
        for f, s, l in grid:
            w = target_weight(px, f, s, l)
            rows.append({**seg_stats(px, w, 0, split), "val_ann": seg_stats(px, w, split, len(px))["ann"]})
        df = pd.DataFrame(rows)
        i = df["ann"].idxmax()
        dev_best.append(df.loc[i, "ann"])
        val_of_best.append(df.loc[i, "val_ann"])
        val_med.append(df["val_ann"].median())
        dev_med.append(df["ann"].median())
    return {
        "n_paths": n_paths,
        "dev_best_median": float(np.nanmedian(dev_best)),
        "val_of_best_median": float(np.nanmedian(val_of_best)),
        "val_median_ann": float(np.nanmedian(val_med)),
        "dev_median_ann": float(np.nanmedian(dev_med)),
        "decay_median": float(np.nanmedian(np.array(dev_best) - np.array(val_of_best))),
    }


def main():
    out = {}
    # A/B 真实数据：两个市场、两段切分
    cases = [
        ("sh510300", "沪深300 ETF 2019-04~2026-11", None),
        ("sh000300", "沪深300 指数 2007~2026", None),
    ]
    for code, label, _ in cases:
        close = load_series(code).dropna().iloc[MINUS_LEN:]
        close = close / close.iloc[0]
        n = len(close)
        split = int(n * 0.5)
        df = scan(close, split)
        res = pick_and_check(df)
        res["label"] = label
        res["seg_dev"] = f"{close.index[0].date()}~{close.index[split-1].date()}"
        res["seg_val"] = f"{close.index[split].date()}~{close.index[-1].date()}"
        res["seg_years_dev"] = round(split / 252, 1)
        res["seg_years_val"] = round((n - split) / 252, 1)
        out[code] = res
        df.to_csv(OUT / f"scan_{code}.csv", index=False, encoding="utf-8-sig")
        print(f"[{code}] best dev ann={res['best']['dev_ann']:.2%} -> val ann={res['best']['val_ann']:.2%} "
              f"(val median {res['val_median_ann']:.2%}, rank pct {res['best_val_rank_pct']:.0%}, "
              f"rho={res['spearman']:.2f})")

    # C 零假设：选择偏差
    close = load_series("sh510300").dropna().iloc[MINUS_LEN:]
    close = close / close.iloc[0]
    split = int(len(close) * 0.5)
    out["null_510300"] = null_test(close, split, n_paths=60)
    print("[null]", json.dumps(out["null_510300"], ensure_ascii=False))

    (OUT / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nwritten", OUT)


if __name__ == "__main__":
    main()
