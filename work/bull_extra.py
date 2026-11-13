"""牛市少赚的可回收空间：牛市级（T0）专属的放宽变体 + oracle 上限。

BASE    定稿三层
B1      牛市日 volcap 地板 0.4 -> 0.6
B2      牛市日 volcap 地板 0.4 -> 0.8
B3      牛市日 G1 触发改用 ma60（慢），非牛市仍 mom20
B4      牛市日仓位强制 1.0（理论上限，不进实盘）

口径：t 收盘算、t+1 生效、单边 5bp、不加杠杆；train/test 分开，test 只报不选。
2026-11-14
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import TD, factors, hyst, run_pos, stats  # noqa

K_VOL, FLOOR = 0.15, 0.4
TAPER = {0: 1.0, 1: 1.0, 2: 0.85, 3: 0.6, 4: 0.5}
OUT = Path(r"E:\Code\quant\runs\2026-11-16_bull")
OUT.mkdir(parents=True, exist_ok=True)


def bull_mask(F):
    c = F["close"]
    return ((c > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)


def build():
    def make(floor_bull=None, slow_bull=False, oracle=False):
        def pol(F):
            c = F["close"]
            bull = bull_mask(F)
            bn = bull.to_numpy()
            g1 = hyst(F["mom20"] < 0.0, F["mom20"] > 0.01, 0.0)
            if slow_bull:
                g1s = hyst(c < F["ma60"], c > F["ma60"], 0.0)
                g1 = pd.Series(np.where(bn, g1s.to_numpy(), g1.to_numpy()), index=c.index)
            g1 = pd.Series(np.where(g1.to_numpy() > 0, 1.0, np.where(bn, 0.5, 0.0)), index=c.index)
            fl = FLOOR if floor_bull is None else np.where(bn, floor_bull, FLOOR)
            volcap = (K_VOL / F["vol60"]).clip(lower=fl, upper=1.0).fillna(1.0)
            slope = F["ma200"].pct_change(60)
            above = c > F["ma200"]
            T = np.select([above & (slope > 0.02), above & (slope >= -0.02), above,
                           (~above) & (slope >= -0.02)], [0, 1, 2, 3], default=4)
            T = pd.Series(T, index=c.index).where(F["ma200"].notna(), 1)
            taper = T.map(TAPER).astype(float)
            pos = (g1 * volcap * taper).clip(0.0, 1.0)
            if oracle:
                pos = pd.Series(np.where(bn, 1.0, pos.to_numpy()), index=c.index)
            return pos
        return pol
    return {
        "BASE": make(),
        "B1 牛地板0.6": make(floor_bull=0.6),
        "B2 牛地板0.8": make(floor_bull=0.8),
        "B3 牛用MA60": make(slow_bull=True),
        "B4 oracle满仓": make(oracle=True),
    }


def load(pool_dir, start_map):
    uni = {}
    root = Path(pool_dir)
    kinds = {}
    meta = root / "_meta.csv"
    if meta.exists():
        m = pd.read_csv(meta)
        kinds = dict(zip(m["code"], m["kind"]))
    for p in sorted(root.glob("*.csv")):
        if p.stem.startswith("_"):
            continue
        code = p.stem
        df = pd.read_csv(p, parse_dates=["date"])
        kind = kinds.get(code, "etf")
        grp = {"etf": "ETF", "index": "长样本", "stock": "长样本"}.get(kind, "ETF")
        df = df[df["date"] >= start_map[grp]]
        if len(df) < 500:
            continue
        uni[code] = (grp, df.sort_values("date").reset_index(drop=True))
    return uni


def run(pool_dir, start_map, train_end, label):
    uni = load(pool_dir, start_map)
    P = build()
    rows = []
    for code, (grp, df) in uni.items():
        F = factors(df)
        ret = F["ret"].fillna(0.0)
        bull = bull_mask(F)
        tr = (df["date"] <= pd.Timestamp(train_end)).to_numpy()
        for name, pol in P.items():
            net, p = run_pos(pol(F), ret)
            for split, mask in (("train", tr), ("test", ~tr), ("all", np.ones(len(df), bool))):
                s = stats(net[mask])
                b = stats(ret[mask])
                fx = stats(p[mask].mean() * ret[mask])
                rows.append(dict(pool=label, group=grp, code=code, rule=name, split=split,
                                 cagr=s["cagr"], mdd=s["mdd"], calmar=s["calmar"],
                                 base_cagr=b["cagr"], base_calmar=b["calmar"], base_mdd=b["mdd"],
                                 cagr_fixed=fx["cagr"],
                                 avg_pos=float(p[mask].mean()),
                                 bull_pos=float(p[mask & bull.to_numpy()].mean()) if (mask & bull.to_numpy()).any() else np.nan,
                                 bull_share=float(bull.to_numpy()[mask].mean()),
                                 turn=float(p.diff().abs().sum() / (mask.sum() / TD))))
    d = pd.DataFrame(rows)
    d.to_csv(OUT / f"{label}.csv", index=False)
    return d


def report(d):
    base = d[d.rule == "BASE"].set_index(["group", "code", "split"])
    for split in ("train", "test", "all"):
        print(f"\n===== {split} =====")
        for g in ("ETF", "长样本"):
            s = d[(d.split == split) & (d.group == g)]
            if s.empty:
                continue
            print(f"  {g} n={s.code.nunique()}  牛日占比 {s.bull_share.mean():.0%}  牛日均仓 {s.bull_pos.mean():.2f}")
            for name in ["BASE", "B1 牛地板0.6", "B2 牛地板0.8", "B3 牛用MA60", "B4 oracle满仓"]:
                x = s[s.rule == name]
                dc = (x.set_index("code").cagr - base.loc[(g, slice(None), split)]["cagr"].loc[x.code].to_numpy()).median()
                dm = (x.set_index("code").mdd - base.loc[(g, slice(None), split)]["mdd"].loc[x.code].to_numpy()).median()
                dk = (x.set_index("code").calmar - base.loc[(g, slice(None), split)]["calmar"].loc[x.code].to_numpy()).median()
                print(f"    {name:<12} cagr {x.cagr.median()*100:+.2f}% (Δ{dc*100:+.2f}pp)  "
                      f"mdd {x.mdd.median()*100:.1f}% (Δ{dm*100:+.1f}pp)  "
                      f"卡玛 {x.calmar.median():.3f} (Δ{dk:+.3f})  均仓 {x.avg_pos.median():.2f}  "
                      f"换手 {x.turn.median():.1f}")


if __name__ == "__main__":
    ROOT = Path(r"E:\Code\quant")
    out = ROOT / "runs" / "2026-11-16_bull"
    out.mkdir(parents=True, exist_ok=True)
    print("### 默认池（ETF 2019+ / 长样本 2007+）")
    d1 = run(ROOT / "data" / "cache_ohlc",
             {"ETF": "2019-01-01", "长样本": "2007-01-01"},
             "2022-12-31", "默认池")
    report(d1)
    print("\n\n### 长池（2013+，train≤2018）")
    d2 = run(ROOT / "data" / "cache_ohlc_long",
             {"ETF": "2013-01-01", "长样本": "2013-01-01"},
             "2018-12-31", "长池")
    report(d2)
