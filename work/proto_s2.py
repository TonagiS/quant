"""临时原型：从 work/cache2019 重建 S2（动量+趋势+逆波动）日收益，校验与既有结论是否一致。"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
from pools import get_pool
from macd_structure import ETF_POOL

CACHE = r"E:\Code\quant\work\cache2019"


def load_close(pool_name):
    pool = get_pool(pool_name, ETF_POOL)
    closes = {}
    for code in pool:
        p = os.path.join(CACHE, f"{code}.csv")
        if not os.path.exists(p):
            print("missing", code)
            continue
        df = pd.read_csv(p, parse_dates=["date"])
        closes[code] = df.set_index("date")["close"].astype(float)
    close = pd.DataFrame(closes).sort_index()
    print(pool_name, "panel", close.shape, close.index[0].date(), close.index[-1].date())
    return close


def features(close):
    ret = close.pct_change(fill_method=None)
    vol = ret.rolling(20, min_periods=15).std()
    mom = {n: close / close.shift(n) - 1 for n in (5, 20, 60, 120)}
    ma60 = close.rolling(60, min_periods=60).mean()
    bars = close.notna().cumsum()
    valid = (bars >= 150) & close.notna() & mom[120].notna() & vol.notna()
    rk = {n: mom[n].where(valid).rank(axis=1, pct=True) for n in (20, 60, 120)}
    rm = (rk[20] + rk[60] + rk[120]) / 3
    return ret, vol, rm, ma60, valid


def simulate(close, ret, vol, rm, ma60, valid, k=5, reb=5, cost=0.0005, delay=1, min_names=8):
    dates = close.index
    T, N = close.shape
    ret0 = np.nan_to_num(ret.to_numpy(), nan=0.0)
    na = np.isnan(ret.to_numpy())
    vol_a = vol.to_numpy()
    rm_a = rm.to_numpy()
    valid_a = valid.to_numpy()
    trend = (close > ma60).to_numpy()
    cand = valid_a & ~np.isnan(rm_a) & trend
    enough = (valid_a.sum(1) >= min_names) & (~np.isnan(rm_a)).any(1)
    start = int(np.argmax(enough))
    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_up, lim_dn = ret.to_numpy() >= lim * 0.98, ret.to_numpy() <= -lim * 0.98

    w = {}
    sched = {}
    port = np.zeros(T)
    turn = []
    for i in range(T):
        day = sum(wt * ret0[i, c] for c, wt in w.items())
        if i >= start and (i - start) % reb == 0:
            ok = np.flatnonzero(cand[i] & ~np.isnan(rm_a[i]))
            target = [int(c) for c in ok[np.argsort(-rm_a[i, ok])[:k]]] if len(ok) else []
            sched[i + delay] = (target, vol_a[i].copy())
        if i in sched:
            target, vrow = sched.pop(i)
            tset = set(target)
            buy = [c for c in target if not lim_up[i, c] and not na[i, c]]
            stuck = [c for c in w if c not in tset and (lim_dn[i, c] or na[i, c])]
            names = buy + [c for c in stuck if c not in buy]
            if names:
                iv = np.array([1.0 / max(vrow[c], 1e-4) if np.isfinite(vrow[c]) else np.nan for c in names])
                iv = np.where(np.isnan(iv), np.nanmean(iv) if np.isfinite(iv).any() else 1.0, iv)
                wts = iv / iv.sum()
            else:
                wts = np.array([])
            new = dict(zip(names, wts))
            traded = sum(abs(new.get(c, 0.0) - w.get(c, 0.0)) for c in set(w) | set(new))
            day -= cost * traded
            turn.append(traded)
            w = new
        port[i] = day
    ann_turn = np.mean(turn) * 252 / reb if turn else 0
    return port, start, ann_turn


def perf(r, start):
    r = np.asarray(r[start:], float)
    eq = np.cumprod(1 + r)
    ann = eq[-1] ** (252 / len(r)) - 1
    v = r.std(ddof=1) * np.sqrt(252)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return dict(start=ann, vol=v, sharpe=r.mean() * 252 / v, mdd=dd, calmar=ann / abs(dd), n=len(r))


if __name__ == "__main__":
    for pool in ("ext", "base"):
        close = load_close(pool)
        ret, vol, rm, ma60, valid = features(close)
        p, start, turn = simulate(close, ret, vol, rm, ma60, valid)
        m = perf(p, start)
        print(pool, "start", close.index[start].date(),
              {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()},
              "turn(x/yr)", round(turn, 1))
