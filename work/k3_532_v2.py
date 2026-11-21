"""K3-532 v2：按老爹 11-14 晚的意见改两处——①只在"有新标的加入"时才判名次 ②加 2% 最小调仓缓冲

v1 的问题（认账）：
  1. 计划里写了"变化 >2% 才下单"，v1 脚本没实现 → 单支 pos 的每日小抖动全成了交易
  2. 每次复审都按名次重排 5/3/2 → 名次互换一次就挪 20pp 权重
  3. 汇报里把 fd 的成本说成 9.5%/年，实际是 5bp × 103.5 = 5.2%/年（双算了单边）

variants：
  nt        = 只在新标触发时换人（无 5 日复审、无排名跌出前 6 退出；退出只有"单支清仓"）+ 2% 缓冲 + 席位随标的
  nt_fill   = nt，且单支清仓后立刻用银行/红利补位（不再等新人）
  dual_buf  = v1 双速 + 2% 缓冲（看缓冲本身值多少）
  fd_buf    = 每日重排 + 2% 缓冲（看缓冲能不能救每日重排）
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from k3_532_backtest import (COST, FILLERS, OUT, SLOT, TD, TRAIN_END, evaluate, load_panel,
                             s2_panel, s2_vt15, single_asset_pos)


def weight_of(P, i, idx, slots, wslot):
    w, miss = {}, 0.0
    for k, c in enumerate(slots):
        if c is None:
            miss += wslot[k]
            continue
        v = float(P[i, idx[c]])
        if v > 0:
            w[c] = wslot[k] * v
    if miss > 1e-12:
        usable = [f for f in FILLERS if f in idx and float(P[i, idx[f]]) > 0 and f not in slots]
        if usable:
            for f in usable:
                w[f] = w.get(f, 0.0) + miss / len(usable) * float(P[i, idx[f]])
    return w


def run(P, R, C, codes, dates, kind="nt", buffer=0.02):
    idx = {c: j for j, c in enumerate(codes)}
    n_days, n_codes = P.shape
    W = np.zeros((n_days, n_codes))
    prev = np.zeros(n_codes)
    slots = [None, None, None]
    wslot = list(SLOT)
    cd, top2 = {}, {}

    for i in range(n_days):
        cand_i, rank_i = C[i], R[i]
        for j, c in enumerate(codes):
            ok = cand_i[j] and np.isfinite(rank_i[j])
            top2[c] = (top2.get(c, 0) + 1) if (ok and rank_i[j] <= 2) else 0

        swaps = 0
        # 退出：任何模式下"单支清仓"都必须走（这是风控层）
        for k, c in enumerate(list(slots)):
            if c is not None and P[i, idx[c]] <= 0:
                slots[k] = None
                cd[c] = i + 5
                swaps += 0  # 退出不算换人名额，补位另算

        if kind == "nt_fill":
            pass  # 清仓后席位空着 → 下面 weight_of 自动用银行红利补

        if kind == "dual_buf" and i % 5 == 0:
            for k, c in enumerate(list(slots)):
                if c is None or swaps >= 1:
                    continue
                j = idx[c]
                if np.isfinite(rank_i[j]) and rank_i[j] >= 4:
                    b = pick(cand_i, rank_i, P[i], idx, slots, cd, top2, i)
                    if b is not None and rank_i[idx[b]] <= 2:
                        slots[k] = b
                        cd[c] = i + 5
                        swaps += 1

        if kind == "fd_buf" and i % 1 == 0:
            order = np.argsort(-np.where(cand_i & (P[i] > 0), rank_i, -np.inf))
            picked = [codes[j] for j in order[:3] if cand_i[j] and np.isfinite(rank_i[j]) and P[i, j] > 0]
            new = (picked + [None, None, None])[:3]
            for c in slots:
                if c is not None and c not in new:
                    cd[c] = i + 5
            slots = new
            swaps = 1

        # 新人触发：有新标进前 2（连续 2 日）且比场上最弱的名次更好 → 挤掉最弱的
        if kind in ("nt", "nt_fill") and all(s is not None for s in slots):
            b = pick(cand_i, rank_i, P[i], idx, slots, cd, top2, i)
            if b is not None and rank_i[idx[b]] <= 2:
                held_ranks = {k: (rank_i[idx[c]] if np.isfinite(rank_i[idx[c]]) else 9.9)
                              for k, c in enumerate(slots)}
                worst_k = max(held_ranks, key=held_ranks.get)
                if rank_i[idx[b]] < held_ranks[worst_k] - 0.5:   # 名次真领先才换（有缓冲）
                    cd[slots[worst_k]] = i + 5
                    slots[worst_k] = b
                    swaps = 1

        while swaps < 1 and any(s is None for s in slots):
            b = pick(cand_i, rank_i, P[i], idx, slots, cd, top2, i)
            if b is None:
                break
            k = slots.index(None)
            slots[k] = b
            swaps += 1

        tgt = weight_of(P, i, idx, slots, wslot)
        row = np.zeros(n_codes)
        for c, v in tgt.items():
            row[idx[c]] = v
        # 2% 缓冲：偏离不足 2% 不动；目标归零（清仓）必须走
        for j in range(n_codes):
            if row[j] == 0.0 and prev[j] > 0.0:
                prev[j] = 0.0
            elif abs(row[j] - prev[j]) > buffer:
                prev[j] = row[j]
        W[i] = prev
    return pd.DataFrame(W, index=dates, columns=codes)


def pick(cand_i, rank_i, p_i, idx, slots, cd, top2, i):
    held = [c for c in slots if c]
    best, br = None, np.inf
    for c, j in idx.items():
        if c in held or cd.get(c, -1) > i or top2.get(c, 0) < 2:
            continue
        if not cand_i[j] or p_i[j] <= 0 or not np.isfinite(rank_i[j]):
            continue
        if rank_i[j] < br:
            best, br = c, rank_i[j]
    return best


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

    variants = {}
    for name, kind, buf in (("nt", "nt", 0.02), ("nt_fill", "nt_fill", 0.02),
                            ("dual_buf", "dual_buf", 0.02), ("fd_buf", "fd_buf", 0.02),
                            ("nt_nobuf", "nt", 0.0)):
        variants[name] = run(P, R, C, codes, close.index, kind, buf)
    variants["s2vt15"] = s2_vt15(close, F["cand"], F["score"], F["vol20"])

    tr_end = pd.Timestamp(TRAIN_END)
    rows = []
    for name, W in variants.items():
        W = W.copy()
        W.iloc[:start] = 0.0
        net, m, gross = evaluate(W, close)
        gn = (1 + gross.fillna(0)).prod() ** (TD / len(gross)) - 1
        rows.append(dict(variant=name, cagr=round(m["cagr"], 4), mdd=round(m["mdd"], 4),
                         calmar=round(m["calmar"], 4), sharpe=round(m["sharpe"], 3),
                         avg_expo=round(m["avg_expo"], 3), turnover=round(m["turnover"], 1),
                         cost_drag=round(gn - m["cagr"], 4)))
        for tag, mask in (("train", net.index <= tr_end), ("test", net.index > tr_end)):
            mm = evaluate(W.loc[mask], close.loc[mask])[1]
            rows.append(dict(variant=f"{name}|{tag}", cagr=round(mm["cagr"], 4), mdd=round(mm["mdd"], 4),
                             calmar=round(mm["calmar"], 4), sharpe=round(mm["sharpe"], 3),
                             avg_expo=round(mm["avg_expo"], 3), turnover=round(mm["turnover"], 1)))
    S = pd.DataFrame(rows)
    S.to_csv(OUT / "summary_v2.csv", index=False, encoding="utf-8-sig")
    for name, W in variants.items():
        W.to_csv(OUT / f"weights2_{name}.csv", encoding="utf-8-sig")
    (OUT / "meta_v2.json").write_text(json.dumps(dict(
        changes=["2% 最小调仓缓冲（v1 漏实现）", "nt: 只在新标触发时换人，无 5 日复审/无排名退出",
                 "席位权重随标的，不按名次重排", "成本口径修正：5bp × 双边换手"], buffer=0.02,
        variants=list(variants)), ensure_ascii=False, indent=2), encoding="utf-8")
    pd.set_option("display.width", 170)
    print(S.round(3).to_string(index=False))
    print(f"\n产物：{OUT}\\summary_v2.csv")


if __name__ == "__main__":
    main()
