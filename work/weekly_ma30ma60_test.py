"""探索性检验（非预登记）：周线上行确认 + 日线 MA30 半仓 / MA60 清仓。

用户问题：在 S2+VT15 之上，对持仓单支加「周线全上行确认 → 跌破 MA30 半仓 → 跌破 MA60 清仓」
这类优先层，是否一律没用？同时看 2012 年起算是否改变结论。

三组口径：
  U1  周线确认才允许满仓；未确认 → 0.5；日线 < MA30 → 0.5；日线 < MA60 → 0
  U2  周线确认 → 满仓（不管日线）；未确认 → 按日线 MA30 半 / MA60 清
  U0  无周线（纯日线 MA30 半 / MA60 清），做对照
"""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "indicators" / "code"))

TD = 252
COST = 0.0005
OUT = ROOT / "runs" / "2026-11-15_weekly_ma_ladder"
OUT.mkdir(parents=True, exist_ok=True)
DATA = ROOT / "data"


# ---------------- 数据 ----------------
def load_series(path: Path) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["date"])
    s = df.set_index("date")["close"].astype(float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def weekly_up(close: pd.Series) -> pd.Series:
    """周线上行确认：周收在 20 周线上方，且 20 周线比 4 周前高。返回日频布尔（周信息前向填充，滞后 1 日）。"""
    w = close.resample("W-FRI").last().dropna()
    ma20w = w.rolling(20, min_periods=10).mean()
    ok = (w > ma20w) & (ma20w > ma20w.shift(4))
    ok = ok.reindex(w.index).fillna(False)
    daily = ok.reindex(close.index, method="ffill").fillna(False)
    return daily.shift(1).fillna(False)  # 只用上一交易日及之前的信息


def ladder_pos(close: pd.Series, kind: str) -> pd.Series:
    ma30 = close.rolling(30, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=30).mean()
    d30 = (close < ma30).shift(1).fillna(False)
    d60 = (close < ma60).shift(1).fillna(False)
    wu = weekly_up(close)
    if kind == "U0":
        pos = np.ones(len(close))
        pos = np.where(d30.values, 0.5, pos)
        pos = np.where(d60.values, 0.0, pos)
    elif kind == "U1":
        pos = np.ones(len(close))
        pos = np.where(d30.values, 0.5, pos)
        pos = np.where(d60.values, 0.0, pos)
        pos = np.minimum(pos, np.where(wu.values, 1.0, 0.5))
    elif kind == "U2":
        pos = np.where(wu.values, 1.0, np.where(d30.values, 0.0, 0.5))
        pos = np.where(d60.values, 0.0, pos)
    else:
        raise ValueError(kind)
    return pd.Series(pos, index=close.index)


def pos_vt(ret: pd.Series, target=0.15, win=60, reb=5) -> pd.Series:
    vol = ret.rolling(win, min_periods=40).std() * np.sqrt(TD)
    raw = (target / vol).clip(0, 1)
    mask = np.arange(len(raw)) % reb == 0
    return raw.where(mask).ffill().fillna(0.0)


def held(pos: pd.Series, ret: pd.Series):
    p = pos.shift(1).fillna(0.0)
    return p * ret.fillna(0.0) - p.diff().abs().fillna(p.abs()) * COST, p


def stats(net: pd.Series) -> dict:
    net = net.fillna(0.0).to_numpy()
    n = len(net)
    if n < 120:
        return dict(cagr=np.nan, mdd=np.nan, calmar=np.nan, sharpe=np.nan)
    eq = np.cumprod(1 + net)
    cagr = eq[-1] ** (TD / n) - 1
    mdd = (eq / np.maximum.accumulate(eq) - 1).min()
    sd = net.std()
    return dict(cagr=cagr, mdd=mdd,
                calmar=cagr / abs(mdd) if mdd < 0 else np.nan,
                sharpe=net.mean() / sd * np.sqrt(TD) if sd > 0 else np.nan)


# ---------------- 单标的 ----------------
def single_asset() -> pd.DataFrame:
    rows = []
    groups = [
        ("ETF", sorted(glob.glob(str(DATA / "cache2019" / "*.csv")))),
        ("指数", sorted(glob.glob(str(DATA / "cache2007" / "idx_*.csv")))),
        ("蓝筹", sorted(glob.glob(str(DATA / "cache2007" / "stk_*.csv")))),
    ]
    for grp, paths in groups:
        for p in paths:
            if os.path.basename(p).startswith(("sh510300", "000300")):
                continue
            c = load_series(Path(p))
            if len(c) < 300:
                continue
            name = os.path.basename(p)[:-4].replace("_20070101", "")
            for start, tag in [("1990", "全样本"), ("2012", "2012起")]:
                sub = c[c.index >= f"{start}-01-01"]
                if len(sub) < 400:
                    continue
                ret = sub.pct_change()
                cands = {
                    "满仓": pd.Series(1.0, index=sub.index),
                    "VT15": pos_vt(ret),
                    "MA60清仓": pd.Series(np.where((sub < sub.rolling(60, min_periods=30).mean()).shift(1).fillna(False), 0.0, 1.0), index=sub.index),
                    "MA30半+MA60清": ladder_pos(sub, "U0"),
                    "周线A": ladder_pos(sub, "U1"),
                    "周线B": ladder_pos(sub, "U2"),
                }
                for k, pos in cands.items():
                    net, p = held(pos, ret)
                    st = stats(net)
                    rows.append(dict(group=grp, name=name, window=tag, rule=k,
                                     avg_pos=float(p.mean()), **st))
    return pd.DataFrame(rows)


# ---------------- 组合（S2 + VT15 上叠单支层） ----------------
def portfolio_overlay() -> pd.DataFrame:
    from position.engine import Config, VolTarget, features, load_panel, simulate

    close = load_panel("ext")
    f = features(close)
    cfg = Config(reb=5, k=5, delay=1, cost=COST, cap=1.0,
                 rule=VolTarget(target=0.15, window=60, every=5))
    res = simulate(close, f, cfg, keep_weights=True)
    start = f["start"]
    dates = close.index
    ret = np.nan_to_num(f["ret"].to_numpy(), nan=0.0)
    wh = res["w_hist"]

    cols = list(close.columns)
    idx = {c: j for j, c in enumerate(cols)}

    # 单支倍数矩阵（用 i-1 的信息）
    mult = {}
    for tag, kind in [("MA30半+MA60清", "U0"), ("周线A", "U1"), ("周线B", "U2")]:
        m = {}
        for c in cols:
            pos = ladder_pos(close[c].dropna(), kind)
            pos = pos.reindex(dates).ffill().fillna(1.0)
            m[c] = pos.shift(1).fillna(1.0).to_numpy()
        mult[tag] = m

    rows = []
    T = len(close)

    def run(tag, use_mult):
        port = np.zeros(T)
        turn_a = np.zeros(T)
        expo_a = np.zeros(T)
        prev = {}
        for i in range(start + 1, T):
            # w_hist[i-1] 才是赚到 ret[i] 的权重；用 wh[i] 会前视（2026-11-14 修）
            w = {cols[c]: v for c, v in wh[i - 1].items()}
            new = {}
            for cname, v in w.items():
                vv = v if not use_mult else v * mult[tag][cname][i - 1]
                if vv > 1e-9:
                    new[cname] = vv
            gross = sum(new.get(cname, 0.0) * ret[i, idx[cname]] for cname in new)
            turn = sum(abs(new.get(c, 0.0) - prev.get(c, 0.0)) for c in set(new) | set(prev))
            port[i] = gross - turn * COST
            turn_a[i] = turn
            expo_a[i] = sum(new.values())
            prev = new
        r = port[start:]
        eq = np.cumprod(1 + r)
        cagr = eq[-1] ** (TD / len(r)) - 1
        mdd = (eq / np.maximum.accumulate(eq) - 1).min()
        rows.append(dict(arm=tag, cagr=cagr, mdd=mdd, calmar=cagr / abs(mdd) if mdd < 0 else np.nan,
                         sharpe=r.mean() / r.std() * np.sqrt(TD),
                         turn_two_way=turn_a[start:].sum() / (len(r) / TD),
                         avg_expo=expo_a[start:].mean()))

    run("S2+VT15（基线）", None)
    for tag in ["MA30半+MA60清", "周线A", "周线B"]:
        run(tag, True)
    return pd.DataFrame(rows)


def main():
    sa = single_asset()
    sa.to_csv(OUT / "single_asset.csv", index=False, encoding="utf-8-sig")
    print("== 单标的：各规则卡玛中位（相对满仓的增量）==")
    for (g, win), sub in sa.groupby(["group", "window"]):
        piv = sub.pivot_table(index="name", columns="rule", values="calmar")
        if "满仓" not in piv:
            continue
        d = piv.sub(piv["满仓"], axis=0)
        line = f"{g:<3}{win:<6} n={len(piv):<3}"
        for r in ["VT15", "MA60清仓", "MA30半+MA60清", "周线A", "周线B"]:
            if r in d:
                line += f"  {r}:{d[r].median():+.3f}({(d[r] > 0).mean():.0%})"
        print(line)
        print(f"      满仓卡玛中位 {piv['满仓'].median():.3f}")

    pf = portfolio_overlay()
    pf.to_csv(OUT / "portfolio_overlay.csv", index=False, encoding="utf-8-sig")
    print("\n== 组合层（ext 52 只，S2+VT15 之上叠单支层）==")
    print(pf.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
