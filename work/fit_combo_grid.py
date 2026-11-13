"""「各种指标排列组合」到底能不能搜出东西 —— 组合网格自查（2026-11-14）

把常见的四个维度做成 3×3×3：
  入场：MA5 上穿 / 20 日新高突破 / RSI14 上穿 30
  过滤：不过滤 / 只做 MA20 上方 / 只做 MA60 上方
  退出：跌破 MA20 / 跌破入场价 3% / 持有满 10 日
共 27 组，成本单边 5bp，全部次日收盘执行。

检验三件事：
  1) 27 组里挑样本内最好的，样本外还剩多少（与单指标版本对比）
  2) 27 组的日收益两两相关性 —— 如果它们高度相关，那「排列组合」只是同一件
     策略换了件衣服，挑来挑去没有信息量
  3) 验证段年化分布宽度 —— 差异是否落在噪声内

输出：runs/2026-11-14_combo_grid/
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "work"))
from bt_ma5_ma20 import load_series, run, stats  # noqa: E402

OUT = ROOT / "runs" / "2026-11-14_combo_grid"
OUT.mkdir(parents=True, exist_ok=True)

COST = 0.0005
PRE = 60
ENTRIES = ["ma5", "brk20", "rsi30"]
FILTERS = ["none", "ma20", "ma60"]
EXITS = ["ma20", "stop3", "t10"]


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def combo_weights(close: pd.Series, entry: str, filt: str, exit_mode: str) -> pd.Series:
    ma5 = close.rolling(5, min_periods=5).mean()
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    r = rsi(close)
    hh20 = close.rolling(20, min_periods=20).max().shift(1)

    if entry == "ma5":
        e = ((close > ma5) & (close.shift(1) <= ma5.shift(1))).to_numpy()
    elif entry == "brk20":
        e = (close > hh20).to_numpy()
    else:
        e = ((r > 30) & (r.shift(1) <= 30)).to_numpy()

    if filt == "ma20":
        f = (close > ma20).to_numpy()
    elif filt == "ma60":
        f = (close > ma60).to_numpy()
    else:
        f = np.ones(len(close), bool)

    c = close.to_numpy()
    m20 = ma20.to_numpy()
    w = np.zeros(len(close))
    cur = 0.0
    ep = np.nan
    ei = -1
    for i in range(len(close)):
        if not np.isfinite(m20[i]):
            w[i] = 0.0
            continue
        if cur > 0:
            if exit_mode == "ma20" and c[i] < m20[i]:
                cur = 0.0
            elif exit_mode == "stop3" and c[i] < ep * 0.97:
                cur = 0.0
            elif exit_mode == "t10" and (i - ei) >= 10:
                cur = 0.0
        if cur == 0 and e[i] and f[i]:
            cur = 1.0
            ep = c[i]
            ei = i
        w[i] = cur
    return pd.Series(w, index=close.index)


def seg(close: pd.Series, w: pd.Series, a: int, b: int) -> dict:
    c = close.iloc[a:b]
    res = run(c, w.iloc[a:b], COST)
    st = stats(res, c.pct_change().fillna(0.0).to_numpy())
    return {"ann": st["ann"], "mdd": st["mdd"], "trades": st["trades"],
            "turnover": st["turnover"], "pnl": res["pnl"]}


def scan_case(code: str, label: str, split_ratio: float = 0.5) -> dict:
    close = load_series(code).dropna().iloc[PRE:]
    close = close / close.iloc[0]
    n = len(close)
    split = int(n * split_ratio)

    rows, pnls = [], {}
    for en in ENTRIES:
        for fi in FILTERS:
            for ex in EXITS:
                w = combo_weights(close, en, fi, ex)
                d = seg(close, w, 0, split)
                v = seg(close, w, split, n)
                key = f"{en}|{fi}|{ex}"
                pnls[key] = v["pnl"]
                rows.append({"entry": en, "filter": fi, "exit": ex, "key": key,
                             "dev_ann": d["ann"], "val_ann": v["ann"],
                             "dev_mdd": d["mdd"], "val_mdd": v["mdd"],
                             "trades_yr": v["trades"] / ((n - split) / 252)})
    df = pd.DataFrame(rows)

    # 相关性：验证段日收益两两相关（剔掉从不交易的死组合，否则相关系数无定义）
    P = pd.DataFrame(pnls)
    alive = [c for c in P.columns if P[c].std() > 1e-12]
    dead = [c for c in P.columns if c not in alive]
    C = P[alive].corr()
    iu = np.triu_indices_from(C.values, k=1)
    pair_corr = C.values[iu]

    # 有效独立策略数：对日收益做 PCA，participation ratio = (Σλ)² / Σλ²
    M = P[alive].to_numpy()
    M = M - M.mean(axis=0)
    lam = np.linalg.svd(M, compute_uv=False) ** 2
    lam = lam[lam > 1e-12]
    eff_n = float((lam.sum() ** 2) / (lam ** 2).sum())

    # 与买入持有的比较
    c_val = close.iloc[split:]
    bh_ann = (c_val.iloc[-1] / c_val.iloc[0]) ** (252 / len(c_val)) - 1.0

    best = df.loc[df["dev_ann"].idxmax()]
    df["dev_rank"] = df["dev_ann"].rank(ascending=False)
    df["val_rank"] = df["val_ann"].rank(ascending=False)
    rho = df["dev_rank"].corr(df["val_rank"], method="spearman")
    best_val_rank = float(df.loc[df["dev_ann"].idxmax(), "val_rank"] / len(df))

    res = {
        "label": label,
        "dev_seg": f"{close.index[0].date()}~{close.index[split-1].date()}",
        "val_seg": f"{close.index[split].date()}~{close.index[-1].date()}",
        "best_key": best["key"],
        "best_dev_ann": float(best["dev_ann"]),
        "best_val_ann": float(best["val_ann"]),
        "best_val_rank": best_val_rank,
        "spearman": float(rho),
        "val_median": float(df["val_ann"].median()),
        "val_p10": float(df["val_ann"].quantile(0.1)),
        "val_p90": float(df["val_ann"].quantile(0.9)),
        "val_positive_share": float((df["val_ann"] > 0).mean()),
        "pair_corr_median": float(np.nanmedian(pair_corr)),
        "pair_corr_p90": float(np.nanquantile(pair_corr, 0.9)),
        "eff_n": eff_n,
        "val_bh_ann": float(bh_ann),
        "val_beat_bh_share": float((df["val_ann"] > bh_ann).mean()),
        "dead_combos": dead,
        "alive": len(alive),
        "n": int(len(df)),
    }
    df.to_csv(OUT / f"grid_{code}.csv", index=False, encoding="utf-8-sig")
    return res, df


def main():
    cases = [
        ("sh000300", "沪深300 指数 2007~2026"),
        ("sh510300", "沪深300 ETF 2019~2026"),
        ("sh510500", "中证500 ETF 2019~2026"),
    ]
    out = {}
    for code, label in cases:
        res, _ = scan_case(code, label)
        out[code] = res
        print(f"[{code}] best {res['best_key']}: dev {res['best_dev_ann']:.2%} -> val {res['best_val_ann']:.2%} "
              f"(val rank pct {res['best_val_rank']:.0%}) "
              f"| val median {res['val_median']:.2%} p10-90 [{res['val_p10']:.2%},{res['val_p90']:.2%}] "
              f"| corr median {res['pair_corr_median']:.2f} effN {res['eff_n']:.1f} "
              f"| rho {res['spearman']:.2f} | dead {len(res['dead_combos'])} "
              f"| beat bh {res['val_beat_bh_share']:.0%} (bh {res['val_bh_ann']:.2%})")

    (OUT / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 单维度贡献：固定 exit=ma20 / filter=none 时三种入场的差异
    df = pd.read_csv(OUT / "grid_sh000300.csv")
    sub = df[(df["filter"] == "ma20") & (df["exit"] == "ma20")]
    print("\n单维度对照（filter=ma20, exit=ma20）:")
    print(sub[["entry", "dev_ann", "val_ann", "dev_mdd", "val_mdd"]].to_string(index=False))
    print("\nwritten", OUT)


if __name__ == "__main__":
    main()
