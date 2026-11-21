"""K3-532 v3：老爹 11-14 晚口径——S2 打分 + 单支门槛，固定 10 天一换，等权持仓

规则
  选股：S2 分数 = mean(rank mom20, rank mom60, rank mom120)，须 close>MA60 且单支目标仓位 pos>0
  频率：每 10 个交易日换一次；期间只让每只的 pos 每日微调（2% 缓冲），不判名次
  权重：等权 1/k（k=3 或 k=5），不再用 5/3/2
  补位：名单不足 k 支时，剩余名额等分给 512800 银行 / 510880 红利
       fill  = 名额空着就每天用银行红利补（总仓不掉）
       pure  = 严格固定 10 天：只在换人日补，期间空就是空（这是"固定10天"的真实代价）
  对照：同规则不乘单支仓位（满仓），以及 s2vt15 基线
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from k3_532_backtest import (COST, FILLERS, OUT, TD, TRAIN_END, evaluate, load_panel, s2_panel,
                             s2_vt15, single_asset_pos)


def run_equal(P, R, C, codes, dates, k=3, pos_mult=True, fill_mode="fill", buffer=0.02):
    idx = {c: j for j, c in enumerate(codes)}
    n_days, n_codes = P.shape
    W = np.zeros((n_days, n_codes))
    prev = np.zeros(n_codes)
    held, base = [], 1.0 / k

    for i in range(n_days):
        if i % 10 == 0:
            ok = C[i] & (P[i] > 0)
            order = np.argsort(-np.where(ok, R[i], -np.inf))
            held = [codes[j] for j in order[:k] if ok[j] and np.isfinite(R[i, j])]

        row = np.zeros(n_codes)
        if pos_mult:
            for c in held:
                j = idx[c]
                row[j] = base * max(float(P[i, j]), 0.0)
        else:
            for c in held:
                row[idx[c]] = base
        if fill_mode == "fill":
            miss = 1.0 - len(held) * base
            usable = [f for f in FILLERS if f in idx and P[i, idx[f]] > 0 and f not in held]
            if miss > 1e-12 and usable:
                for f in usable:
                    row[idx[f]] = miss / len(usable) * (max(float(P[i, idx[f]]), 0.0) if pos_mult else 1.0)
        for j in range(n_codes):
            if row[j] == 0.0 and prev[j] > 0.0:
                prev[j] = 0.0
            elif abs(row[j] - prev[j]) > buffer:
                prev[j] = row[j]
        W[i] = prev
    return pd.DataFrame(W, index=dates, columns=codes)


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

    spec = [("k3_fill", 3, True, "fill"), ("k3_pure", 3, True, "pure"),
            ("k5_fill", 5, True, "fill"), ("k5_pure", 5, True, "pure"),
            ("k3_fill_full", 3, False, "fill"), ("k5_fill_full", 5, False, "fill")]
    variants = {n: run_equal(P, R, C, codes, close.index, k, pm, fm) for n, k, pm, fm in spec}
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
                         cost=round(gn - m["cagr"], 4)))
        for tag, mask in (("train", net.index <= tr_end), ("test", net.index > tr_end)):
            mm = evaluate(W.loc[mask], close.loc[mask])[1]
            rows.append(dict(variant=f"{name}|{tag}", cagr=round(mm["cagr"], 4), mdd=round(mm["mdd"], 4),
                             calmar=round(mm["calmar"], 4), sharpe=round(mm["sharpe"], 3),
                             avg_expo=round(mm["avg_expo"], 3), turnover=round(mm["turnover"], 1)))
    S = pd.DataFrame(rows)
    S.to_csv(OUT / "summary_v3.csv", index=False, encoding="utf-8-sig")
    for name, W in variants.items():
        W.to_csv(OUT / f"weights3_{name}.csv", encoding="utf-8-sig")

    # 逐年（主版本 + 基线）
    yr = {}
    for name in ("k3_fill", "k5_fill", "k3_fill_full", "k5_fill_full", "s2vt15"):
        W = variants[name].copy()
        W.iloc[:start] = 0.0
        net, _, _ = evaluate(W, close)
        yr[name] = (1 + net).groupby(net.index.year).prod() - 1
    Y = pd.DataFrame(yr).round(4)
    Y.to_csv(OUT / "by_year_v3.csv", encoding="utf-8-sig")
    (OUT / "meta_v3.json").write_text(json.dumps(dict(
        rule="S2 score + pos>0 门槛，固定 10 日一换，等权 1/k，不足补银行/红利，2% 缓冲",
        variants=[s[0] for s in spec] + ["s2vt15"]), ensure_ascii=False, indent=2), encoding="utf-8")

    pd.set_option("display.width", 170)
    print(S.round(3).to_string(index=False))
    print("\n== 逐年 ==")
    print((Y * 100).round(1).to_string())
    print(f"\n产物：{OUT}\\summary_v3.csv")


if __name__ == "__main__":
    main()
