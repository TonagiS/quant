"""K3-532：三席集中 + 单支仓位层 + 银行/红利补位（2026-11-14，预登记见 work/PLAN_k3_532_20261114.md）

规则（老爹 6 步的口径，冻结）：
  1 池：data/cache_ohlc 全部 ETF（53 只），2019-01 起
  2 门槛：单支定稿仓位 pos > 0（未清仓）；S2 打分 score = mean(rank(mom20), rank(mom60), rank(mom120))，只对 close>MA60 打分
  3 前 3 名：slot = 0.5 / 0.3 / 0.2
  4 每只再乘单支仓位：w = slot × pos
  5 缺席补位：512800 银行 / 510880 红利，均分缺掉的名额；两只都不可用则留现金
  6 换人三档对照：
      dual  = 双速（仓位每日、名额事件驱动 + 5 日复审、单日最多换 1 席、被换出 5 日冷却、新进需连续 2 日在前 2）
      f10   = 每 10 个交易日检查一次名额
      fd    = 每日全量重排
      full  = 同集中度满仓版（fd 的选股，pos=1），剥离"集中度"效应
      s2vt15= S2(k5, 逆波动, reb5) + VT15，现有组合线基线

执行：t 收盘出信号，t+1 收盘成交、次日起计收益；单边 5bp；不加杠杆；现金收益 0。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "cache_ohlc"
OUT = ROOT / "runs" / "2026-11-15_k3_532"
TD, COST = 252, 0.0005
SLOT = (0.5, 0.3, 0.2)
FILLERS = ["sh512800", "sh510880"]
START, TRAIN_END = "2019-01-01", "2022-12-31"
K_VOL_COMBO = 0.15


# ---------------------------------------------------------------- 数据
def load_panel():
    meta = pd.read_csv(DATA / "_meta.csv")
    codes = [c for c in meta.loc[meta.kind == "etf", "code"] if (DATA / f"{c}.csv").exists()]
    closes = {}
    for c in codes:
        df = pd.read_csv(DATA / f"{c}.csv", parse_dates=["date"])
        closes[c] = df.set_index("date")["close"].astype(float)
    close = pd.DataFrame(closes).sort_index()
    return close[close.index >= START]


def single_asset_pos(close: pd.DataFrame):
    """单支定稿：G1 × volcap × taper（与 single_asset_final.policy 同口径，矢量化到面板）。"""
    ret = close.pct_change(fill_method=None)
    mom20 = close / close.shift(20) - 1.0
    ma200 = close.rolling(200, min_periods=200).mean()
    vol60 = ret.rolling(60, min_periods=42).std() * np.sqrt(TD)

    bull = (close > ma200) & (ma200 > ma200.shift(60))
    st = np.where(mom20 < 0.0, 0.0, np.where(mom20 > 0.01, 1.0, np.nan))
    state = pd.DataFrame(st, index=close.index, columns=close.columns).ffill().fillna(1.0)
    g1 = np.where(state.to_numpy() > 0, 1.0, np.where(bull.to_numpy(), 0.5, 0.0))
    volcap = (K_VOL_COMBO / vol60).clip(0.4, 1.0).fillna(1.0).to_numpy()

    slope = ma200.pct_change(60).to_numpy()
    above = (close > ma200).to_numpy()
    T = np.select([above & (slope > 0.02), above & (slope >= -0.02), above, (~above) & (slope >= -0.02)],
                  [0, 1, 2, 3], default=4)
    taper = np.array([1.0, 1.0, 0.85, 0.6, 0.5])[T]
    taper = np.where(np.isnan(slope), 1.0, taper)
    pos = np.clip(g1 * volcap * taper, 0.0, 1.0)
    pos = np.where(ma200.notna().to_numpy(), pos, 0.0)   # 上市不足 200 日不开仓
    return pd.DataFrame(pos, index=close.index, columns=close.columns)


def s2_panel(close: pd.DataFrame):
    ret = close.pct_change(fill_method=None)
    vol20 = ret.rolling(20, min_periods=15).std()
    mom = {n: close / close.shift(n) - 1.0 for n in (20, 60, 120)}
    ma60 = close.rolling(60, min_periods=60).mean()
    bars = close.notna().cumsum()
    valid = (bars >= 150) & close.notna() & mom[120].notna() & vol20.notna()
    rk = {n: mom[n].where(valid).rank(axis=1, pct=True) for n in (20, 60, 120)}
    score = (rk[20] + rk[60] + rk[120]) / 3.0
    cand = valid & score.notna() & (close > ma60)
    return dict(ret=ret, vol20=vol20, score=score, cand=cand, close=close)


# ---------------------------------------------------------------- 组合驱动
class Book:
    def __init__(self):
        self.slots = [None, None, None]
        self.cooldown = {}
        self.below = {}
        self.top2 = {}

    def drop(self, k, i):
        c = self.slots[k]
        if c is not None:
            self.slots[k] = None
            self.cooldown[c] = i + 5
            self.below.pop(c, None)


def weight_of(P, i, codes_idx, slots):
    """按当前席位生成目标权重 dict。缺席用 512800 / 510880 均分补。"""
    w = {}
    miss = 0.0
    for k, c in enumerate(slots):
        if c is None:
            miss += SLOT[k]
            continue
        v = float(P[i, codes_idx[c]])
        if v > 0:
            w[c] = SLOT[k] * v
    if miss > 1e-12:
        usable = [f for f in FILLERS if f in codes_idx and float(P[i, codes_idx[f]]) > 0
                  and f not in slots]
        if usable:
            each = miss / len(usable)
            for f in usable:
                w[f] = w.get(f, 0.0) + each * float(P[i, codes_idx[f]])
    return w


def run_combo(P: np.ndarray, R: np.ndarray, C: np.ndarray, codes, dates, mode: str, pos_mult: bool = True):
    """mode: dual / f10 / fd / full"""
    idx = {c: j for j, c in enumerate(codes)}
    n_days, n_codes = P.shape
    W = np.zeros((n_days, n_codes))
    bk = Book()
    review_every = {"dual": 5, "f10": 10, "fd": 1, "full": 1}[mode]
    P_use = P if pos_mult else (P > 0).astype(float)

    for i in range(n_days):
        cand_i = C[i]
        rank_i = R[i]
        # 连续在前 2 / 跌出前 6 的计数
        for j, c in enumerate(codes):
            if not cand_i[j] or not np.isfinite(rank_i[j]):
                bk.top2[c] = 0
                bk.below[c] = 0 if bk.slots.count(c) == 0 else bk.below.get(c, 0)
                continue
            bk.top2[c] = bk.top2.get(c, 0) + 1 if rank_i[j] <= 2 else 0
            if c in bk.slots:
                bk.below[c] = bk.below.get(c, 0) + 1 if rank_i[j] > 6 else 0

        swaps = 0
        if mode in ("dual",):
            for k, c in enumerate(list(bk.slots)):
                if c is None:
                    continue
                j = idx[c]
                if P[i, j] <= 0:                       # ① 单支清仓
                    bk.drop(k, i)
                elif bk.below.get(c, 0) >= 2:          # ② 排名两天跌出前 6
                    bk.drop(k, i)

        do_review = (i % review_every == 0)
        if do_review:
            if mode in ("f10", "fd", "full"):
                order = np.argsort(-np.where(cand_i & (P_use[i] > 0), rank_i, -np.inf))
                picked = [codes[j] for j in order[:3] if np.isfinite(rank_i[j])
                          and cand_i[j] and P_use[i, j] > 0]
                bk.slots = (picked + [None, None, None])[:3]
            else:  # dual 的 5 日复审：只做"名次明显落后"的换人
                for k, c in enumerate(list(bk.slots)):
                    if c is None or swaps >= 1:
                        continue
                    j = idx[c]
                    if not np.isfinite(rank_i[j]) or rank_i[j] < 4:
                        continue
                    best = best_candidate(idx, cand_i, rank_i, P_use[i], bk, i)
                    if best is not None and rank_i[idx[best]] <= 2:
                        bk.drop(k, i)
                        bk.slots[k] = best
                        swaps += 1

        while swaps < 1 and any(s is None for s in bk.slots):     # 补人（每日最多 1 席）
            best = best_candidate(idx, cand_i, rank_i, P_use[i], bk, i)
            if best is None:
                break
            k = bk.slots.index(None)
            bk.slots[k] = best
            swaps += 1

        w = weight_of(P_use, i, idx, bk.slots)
        for c, v in w.items():
            W[i, idx[c]] = v
    return pd.DataFrame(W, index=dates, columns=codes)


def best_candidate(idx, cand_i, rank_i, p_i, bk, i):
    best, best_r = None, np.inf
    held = [c for c in bk.slots if c]
    for c, j in idx.items():
        if c in held:
            continue
        if bk.cooldown.get(c, -1) > i:
            continue
        if bk.top2.get(c, 0) < 2:
            continue
        if not cand_i[j] or p_i[j] <= 0 or not np.isfinite(rank_i[j]):
            continue
        if rank_i[j] < best_r:
            best, best_r = c, rank_i[j]
    return best


def s2_vt15(close, cand, score, vol20):
    codes, dates = list(close.columns), close.index
    P = cand.to_numpy()
    S = score.to_numpy()
    V = vol20.to_numpy()
    W = np.zeros((len(dates), len(codes)))
    slots = []
    for i in range(len(dates)):
        if i % 5 == 0:
            order = np.argsort(-np.where(P[i], S[i], -np.inf))
            slots = [codes[j] for j in order[:5] if P[i, j] and np.isfinite(S[i, j])]
        if slots:
            iv = np.array([1.0 / max(V[i, codes.index(c)], 1e-4) for c in slots])
            iv = np.where(np.isfinite(iv), iv, np.nanmean(iv))
            for c, x in zip(slots, iv / iv.sum()):
                W[i, codes.index(c)] = x
    Wd = pd.DataFrame(W, index=dates, columns=codes)
    ret = close.pct_change(fill_method=None).fillna(0.0)
    gross = (Wd.shift(1).fillna(0.0) * ret).sum(axis=1)
    sig = gross.rolling(60, min_periods=40).std() * np.sqrt(TD)
    expo = (K_VOL_COMBO / sig).clip(0.0, 1.0).fillna(1.0)
    return Wd.mul(expo.shift(1).fillna(1.0), axis=0)


def perf(net: pd.Series, W: pd.DataFrame):
    net = net.fillna(0.0)
    nav = (1.0 + net).cumprod()
    n = len(net)
    cagr = nav.iloc[-1] ** (TD / n) - 1.0 if n > 0 else np.nan
    mdd = float((nav / nav.cummax() - 1.0).min())
    calmar = cagr / abs(mdd) if mdd < 0 else np.nan
    sharpe = float(net.mean() / net.std() * np.sqrt(TD)) if net.std() > 0 else np.nan
    expo = float(W.sum(axis=1).mean())
    turn = float((W.diff().abs().sum(axis=1).sum()) / (n / TD))
    return dict(cagr=cagr, mdd=mdd, calmar=calmar, sharpe=sharpe, avg_expo=expo, turnover=turn)


def evaluate(W: pd.DataFrame, close: pd.DataFrame):
    ret = close.pct_change(fill_method=None).fillna(0.0)
    gross = (W.shift(1).fillna(0.0) * ret).sum(axis=1)
    turn = W.diff().abs().sum(axis=1).fillna(0.0)
    net = gross - COST * turn.shift(1).fillna(0.0)
    return net, perf(net, W), gross


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    close = load_panel()
    F = s2_panel(close)
    pos = single_asset_pos(close)
    codes = list(close.columns)
    P = pos.to_numpy()
    R = F["score"].rank(axis=1, ascending=False).to_numpy()   # 1 = 最强（只在 cand 内有值）
    C = F["cand"].to_numpy()

    warm = close.rolling(200, min_periods=200).mean().notna().sum(axis=1)
    start = int(np.argmax(warm >= 30))
    print(f"池 {len(codes)} 只，交易日 {len(close)}，组合起点 {close.index[start].date()}")

    variants = {}
    for name, mode, mult in (("dual", "dual", True), ("f10", "f10", True), ("fd", "fd", True),
                             ("full", "fd", False), ("dual_full", "dual", False)):
        W = run_combo(P, R, C, codes, close.index, mode, pos_mult=mult)
        variants[name] = W
    variants["s2vt15"] = s2_vt15(close, F["cand"], F["score"], F["vol20"])

    rows, per_year, daily = [], [], {}
    tr_end = pd.Timestamp(TRAIN_END)
    for name, W in variants.items():
        W = W.copy()
        W.iloc[:start] = 0.0
        net, m, gross = evaluate(W, close)
        daily[name] = net
        rec = dict(variant=name, **{k: round(v, 4) for k, v in m.items()})
        rows.append(rec)
        for tag, mask in (("train", net.index <= tr_end), ("test", net.index > tr_end)):
            mm = perf(net[mask], W.loc[mask])
            rows.append(dict(variant=f"{name}|{tag}", **{k: round(v, 4) for k, v in mm.items()}))
        yr = net.groupby(net.index.year).apply(lambda s: (1 + s).prod() - 1)
        for y, v in yr.items():
            per_year.append(dict(variant=name, year=int(y), ret=round(float(v), 4)))

    S = pd.DataFrame(rows)
    S.to_csv(OUT / "summary.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(per_year).to_csv(OUT / "by_year.csv", index=False, encoding="utf-8-sig")
    d = pd.DataFrame(daily)
    nav = (1 + d).cumprod()
    nav.to_csv(OUT / "nav_daily.csv", encoding="utf-8-sig")
    for name in ("dual", "f10", "fd", "full", "dual_full", "s2vt15"):
        variants[name].to_csv(OUT / f"weights_{name}.csv", encoding="utf-8-sig")

    # 当前信号
    i = len(close) - 1
    cur = sorted([(F["score"].iloc[i, j], codes[j], float(P[i, j])) for j in range(len(codes))
                  if C[i, j] and P[i, j] > 0], reverse=True)[:8]
    sig = pd.DataFrame([dict(score=round(s, 4), code=c, pos=round(p, 4)) for s, c, p in cur])
    sig.to_csv(OUT / "live_signal.csv", index=False, encoding="utf-8-sig")
    (OUT / "meta.json").write_text(json.dumps(dict(
        pool=len(codes), start=str(close.index[start].date()), end=str(close.index[-1].date()),
        slot=SLOT, filler=FILLERS, cost=COST, delay=1, td=TD,
        pos_layer="G1×volcap(0.15/vol60,0.4) ×taper(T0/1=1.0,T2=.85,T3=.6,T4=.5)",
        score="mean(rank(mom20),rank(mom60),rank(mom120)) ; close>MA60 ; pos>0",
        variants=["dual", "f10", "fd", "full", "s2vt15"]), ensure_ascii=False, indent=2), encoding="utf-8")

    pd.set_option("display.width", 160)
    print(S.round(3).to_string(index=False))
    print("\n== 当前前 8 名（score / pos）==")
    print(sig.to_string(index=False))
    print(f"\n产物：{OUT}")


if __name__ == "__main__":
    main()
