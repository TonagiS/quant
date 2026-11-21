"""K3-532 v4：老爹 11-14 深夜"都搞搞"——两条线一起验

① S2 事件驱动版（把"别老判名次"搬到主线）
   S2 原版：k=5、逆波动权重、MA60 过滤、**每 5 日强制重排**、VT15 波动率目标
   事件驱动版：取消 5 日重排；退出 = 连续 2 日不满足（跌破 MA60 或排名掉出前 10）；
              进入 = 排名进前 3 且连续 2 日、冷却 5 日、最多 5 席；仍是逆波动权重 + VT15
   权重另加 2% 缓冲（偏离不足 2% 不动）

② 卫星组合
   主仓 S2VT15 + 卫星 K3-nt（v2 的事件驱动集中版），按 70/30 与 80/20 两张

判据：风险型（卡玛、最大回撤），年化与换手副报；与 s2vt15 同窗口对比。
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from k3_532_backtest import (COST, FILLERS, OUT, TD, TRAIN_END, evaluate, load_panel, s2_panel,
                             s2_vt15, single_asset_pos)
from k3_532_v2 import run as run_k3


def s2_event(F, close, cap=5, entry_rank=3, drop_rank=10, buffer=0.02, vt=True):
    codes = list(close.columns)
    idx = {c: j for j, c in enumerate(codes)}
    S = F["score"]
    C = F["cand"].to_numpy()
    V = F["vol20"].to_numpy()
    R = S.rank(axis=1, ascending=False).to_numpy()
    n_days, n_codes = C.shape
    W = np.zeros((n_days, n_codes))
    prev = np.zeros(n_codes)
    held, cd, ok2, bad = [], {}, {}, {}

    for i in range(n_days):
        for j, c in enumerate(codes):
            good = C[i, j] and np.isfinite(R[i, j])
            ok2[c] = (ok2.get(c, 0) + 1) if (good and R[i, j] <= entry_rank) else 0
            if c in held:
                bad[c] = (bad.get(c, 0) + 1) if (not good or R[i, j] > drop_rank) else 0

        # 退出
        for c in list(held):
            if bad.get(c, 0) >= 2:
                held.remove(c)
                cd[c] = i + 5
                bad.pop(c, None)

        # 进入（每次最多补 1 席；首日建仓补满）
        fills = cap if i < 5 else 1
        while len(held) < cap and fills > 0:
            best, br = None, np.inf
            for c, j in idx.items():
                if c in held or cd.get(c, -1) > i or ok2.get(c, 0) < 2:
                    continue
                if not C[i, j] or not np.isfinite(R[i, j]) or R[i, j] > entry_rank:
                    continue
                if R[i, j] < br:
                    best, br = c, R[i, j]
            if best is None:
                break
            held.append(best)
            fills -= 1

        row = np.zeros(n_codes)
        if held:
            iv = np.array([1.0 / max(V[i, idx[c]], 1e-4) if np.isfinite(V[i, idx[c]]) else np.nan
                           for c in held])
            iv = np.where(np.isnan(iv), np.nanmean(iv), iv)
            for c, x in zip(held, iv / iv.sum()):
                row[idx[c]] = x
        for j in range(n_codes):
            if row[j] == 0.0 and prev[j] > 0.0:
                prev[j] = 0.0
            elif abs(row[j] - prev[j]) > buffer:
                prev[j] = row[j]
        W[i] = prev

    Wd = pd.DataFrame(W, index=close.index, columns=codes)
    if not vt:
        return Wd
    ret = close.pct_change(fill_method=None).fillna(0.0)
    gross = (Wd.shift(1).fillna(0.0) * ret).sum(axis=1)
    sig = gross.rolling(60, min_periods=40).std() * np.sqrt(TD)
    expo = (0.15 / sig).clip(0.0, 1.0).fillna(1.0)
    return Wd.mul(expo.shift(1).fillna(1.0), axis=0)


def row(name, W, close, net, m, gross):
    gn = (1 + gross.fillna(0)).prod() ** (TD / len(gross)) - 1
    return dict(variant=name, cagr=round(m["cagr"], 4), mdd=round(m["mdd"], 4),
                calmar=round(m["calmar"], 4), sharpe=round(m["sharpe"], 3),
                avg_expo=round(m["avg_expo"], 3), turnover=round(m["turnover"], 1),
                cost=round(gn - m["cagr"], 4))


def main():
    close = load_panel()
    F = s2_panel(close)
    pos = single_asset_pos(close)
    codes = list(close.columns)
    P = pos.to_numpy()
    R = F["score"].rank(axis=1, ascending=False).to_numpy()
    C = F["cand"].to_numpy()
    warm = close.rolling(200, min_periods=200).mean().notna().sum(axis=1)
    start = int(np.argmax(warm >= 30))

    base = s2_vt15(close, F["cand"], F["score"], F["vol20"])          # 现行 S2+VT15（5 日重排）
    ev = s2_event(F, close)                                          # 事件驱动 S2 + VT15
    ev_novt = s2_event(F, close, vt=False)                           # 事件驱动 S2，无 VT15
    k3 = run_k3(P, R, C, codes, close.index, "nt", 0.02)             # 卫星：K3-nt
    blends = {"blend70_30": 0.7, "blend80_20": 0.8}

    variants = {"s2vt15(baseline)": base, "s2_event": ev, "s2_event_novt": ev_novt, "k3_nt": k3}
    for nm, a in blends.items():
        variants[nm] = base * a + k3 * (1 - a)

    tr_end = pd.Timestamp(TRAIN_END)
    rows = []
    for name, W in variants.items():
        W = W.copy()
        W.iloc[:start] = 0.0
        net, m, gross = evaluate(W, close)
        rows.append(row(name, W, close, net, m, gross))
        for tag, mask in (("train", net.index <= tr_end), ("test", net.index > tr_end)):
            mm = evaluate(W.loc[mask], close.loc[mask])[1]
            rows.append(dict(variant=f"{name}|{tag}", cagr=round(mm["cagr"], 4), mdd=round(mm["mdd"], 4),
                             calmar=round(mm["calmar"], 4), sharpe=round(mm["sharpe"], 3),
                             avg_expo=round(mm["avg_expo"], 3), turnover=round(mm["turnover"], 1)))
    S = pd.DataFrame(rows)
    S.to_csv(OUT / "summary_v4.csv", index=False, encoding="utf-8-sig")
    for name, W in variants.items():
        W.to_csv(OUT / f"weights4_{name.replace('(', '').replace(')', '')}.csv", encoding="utf-8-sig")

    yr = {}
    for name, W in variants.items():
        W = W.copy()
        W.iloc[:start] = 0.0
        net, _, _ = evaluate(W, close)
        yr[name] = (1 + net).groupby(net.index.year).prod() - 1
    Y = pd.DataFrame(yr).round(4)
    Y.to_csv(OUT / "by_year_v4.csv", encoding="utf-8-sig")

    # 事件驱动 S2 的持仓特征
    hc = (ev > 1e-9).sum(axis=1)
    info = dict(median_holdings=int(hc.median()), days_below5=int((hc < 5).sum()),
                days_total=int((ev.index >= close.index[start]).sum()),
                changes=int((ev.diff().abs().sum(axis=1) > 1e-6).sum()))
    (OUT / "meta_v4.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    pd.set_option("display.width", 180)
    print(S.round(3).to_string(index=False))
    print("\n== 逐年 ==")
    print((Y * 100).round(1).to_string())
    print("\n事件驱动S2持仓特征:", info)
    print(f"\n产物：{OUT}\\summary_v4.csv")


if __name__ == "__main__":
    main()
