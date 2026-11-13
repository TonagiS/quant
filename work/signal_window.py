"""指定标的在指定区间内，逐日拆出定稿策略的三层信号（G1 / volcap / taper）与目标仓位。

用法：python signal_window.py 2026-09-01 2026-10-31 sh512760 sh512480 sh515050 sh515320
输出：控制台打印目标仓位矩阵 + 变动点；CSV 落到 runs/2026-11-15_single_asset_state/signal_window_*.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_final import policy  # noqa: E402
from single_asset_harness import factors, load_universe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "2026-11-15_single_asset_state"


def rows_for(code, df, start, end):
    F = factors(df)
    pos, T = policy(F)
    c, ma200 = F["close"], F["ma200"]
    slope = ma200.pct_change(60)
    bull = (c > ma200) & (slope > 0)
    mom20 = F["mom20"]
    vol60 = F["vol60"]
    volcap = (0.15 / vol60).clip(0.4, 1.0).fillna(1.0)
    taper_map = {0: 1.0, 1: 1.0, 2: 0.85, 3: 0.6, 4: 0.5}
    taper = T.map(taper_map).astype(float)
    # 复算 G1（与 policy 内同口径：状态 0 = 动量触发过且未复原，加仓看年线）
    reduce = (mom20 < 0).fillna(False).to_numpy()
    recover = (mom20 > 0.01).fillna(False).to_numpy()
    state = np.full(len(df), np.nan)
    state[reduce] = 0.0
    state[recover] = 1.0
    state = pd.Series(state, index=c.index).ffill().fillna(1.0)
    g1 = pd.Series(np.where(state.to_numpy() > 0, 1.0,
                            np.where(bull.to_numpy(), 0.5, 0.0)), index=c.index)
    # 全部用 numpy 对齐，避免 date 列（RangeIndex）与指标（日期索引）混排
    out = pd.DataFrame(dict(date=df["date"].to_numpy(), close=c.to_numpy(),
                            mom20=mom20.to_numpy(), ma200=ma200.to_numpy(),
                            slope60=slope.to_numpy(), above=(c > ma200).to_numpy(),
                            bull=bull.to_numpy(), vol60=vol60.to_numpy(), G1=g1.to_numpy(),
                            volcap=volcap.to_numpy(), T=T.to_numpy(), taper=taper.to_numpy(),
                            target=pos.to_numpy()))
    m = (out["date"] >= pd.Timestamp(start)) & (out["date"] <= pd.Timestamp(end))
    out = out[m].copy()
    out.insert(0, "code", code)
    return out


def main():
    start, end = sys.argv[1], sys.argv[2]
    codes = sys.argv[3:]
    uni = load_universe()
    frames = [rows_for(c, uni[c][1], start, end) for c in codes if c in uni]
    if not frames:
        print("没有可用标的")
        return
    d = pd.concat(frames, ignore_index=True)
    d["d_target"] = d.groupby("code")["target"].diff()
    csv = OUT / f"signal_window_{start}_{end}.csv"
    d.to_csv(csv, index=False, encoding="utf-8-sig", float_format="%.4f")

    piv = d.pivot_table(index="date", columns="code", values="target")
    print("== 区间表现（按信号持仓，含 5bp 单边成本）==")
    perf = []
    for code, g in d.groupby("code"):
        r = g["close"].pct_change().fillna(0.0)
        p = g["target"].shift(1).fillna(0.0)
        net = (p * r - p.diff().abs().fillna(0.0) * 0.0005).sum()
        chg = (g["target"].diff().abs().fillna(0.0) > 0.01).sum()
        perf.append(dict(code=code, ret=f"{net:+.2%}", bh=f"{r.sum():+.2%}",
                         diff=f"{net - r.sum():+.2%}", avg_pos=f"{p.mean():.2f}",
                         n_chg=int(chg)))
    print(pd.DataFrame(perf).to_string(index=False))
    print("\n== 每日目标仓位 ==")
    print(piv.round(2).to_string())
    print("\n== 变动点（G1 或 T 变、或目标变动 >5pp）==")
    g1chg = d.groupby("code")["G1"].diff().fillna(0) != 0
    tchg = d.groupby("code")["T"].diff().fillna(0) != 0
    big = d["d_target"].abs().fillna(0) > 0.05
    ch = d[g1chg | tchg | big].copy()
    cols = ["date", "code", "G1", "T", "taper", "volcap", "mom20", "target", "d_target"]
    print(ch[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"\n-> {csv}")


if __name__ == "__main__":
    main()
