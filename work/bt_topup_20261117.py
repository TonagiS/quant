# -*- coding: utf-8 -*-
"""回测：影子 v2 执行口径对比（2026-11-17）

问题：实盘 paper_account 只补低配、超配不减；研究回测 engine.simulate 每次全额对齐目标。
本脚本用同一套信号（k=5、20 日波动逆波动、收盘>MA60、14 自然日一调），
只换执行口径，逐日按持仓漂移记账：

  full   = 到点全额对齐目标（超配也砍）        <- 研究回测口径
  topup  = 只卖不在目标的、只补低配（超配不动、现金用完为止） <- 实盘口径

两档再各跑一版 VT15 敞口（expo=min(1,0.15/vol60)，波动用 S2 自身日收益估，调仓日更新）。
费率按场外 C 类：买 5bp；卖 5bp 佣金 + 持仓<7 自然日 1.5% 赎回费、>=7 自然日 0。
"""
from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant\position")
sys.path.insert(0, r"E:\Code\quant\indicators\code")
import engine as E  # noqa: E402

LOT = 100
BUY_COST = 0.0005
SELL_COMM = 0.0005
REDEEM_SHORT = 0.015
MIN_HOLD_DAYS = 7
K = 5
REB_CAL_DAYS = 14
TD = 252
EVAL_START = "2019-08-13"


def target_weights(close, f, j, k=K):
    cand = f["cand"].iloc[j]
    rm = f["rm"].iloc[j]
    vol = f["vol20"].iloc[j]
    ok = [c for c in close.columns if bool(cand.get(c, False)) and np.isfinite(rm.get(c, np.nan))]
    if len(ok) < 5:
        return {}
    top = sorted(ok, key=lambda c: -rm[c])[:k]
    iv = np.array([1.0 / max(float(vol[c]), 1e-4) for c in top])
    w = iv / iv.sum()
    return {c: float(x) for c, x in zip(top, w)}


def run(close, f, mode, expo_from=None, vt=None, reb_days=REB_CAL_DAYS,
        buy_cost=BUY_COST, sell_comm=SELL_COMM, redeem_short=REDEEM_SHORT, reb_td=None):
    dates = close.index
    cols = {c: n for n, c in enumerate(close.columns)}
    px = close.ffill().to_numpy()   # 估值用：停牌沿用最后价
    start = max(f["start"], int(close.index.searchsorted(pd.Timestamp("2019-01-01"))))
    shares, open_i = {}, {}
    cash = 100000.0
    N = len(close)
    ret = np.zeros(N)
    cost_a = np.zeros(N)
    turn_a = np.zeros(N)
    expo_a = np.zeros(N)
    drift, gap, swapped = [], [], []
    n_reb = 0
    last_reb = None
    last_nav = cash

    for i in range(start, N):
        # 1) 估值：持仓随价格漂移
        val = sum(sh * px[i, cols[c]] for c, sh in shares.items())
        nav = cash + val
        ret[i] = nav / last_nav - 1 if last_nav > 0 else 0.0
        last_nav = nav
        expo_a[i] = (nav - cash) / nav if nav > 0 else 0.0

        # 2) 到点调仓：信号取前一根 bar，当日收盘成交
        if reb_td:
            due = ((i - 1 - start) % reb_td == 0)
        else:
            due = last_reb is None or (dates[i].date() - last_reb) >= timedelta(days=reb_days)
        if not due or i - 1 < start:
            continue
        j = i - 1
        wt = target_weights(close, f, j)
        if not wt:
            continue
        n_reb += 1
        expo = 1.0
        if vt is not None:
            src = ret if expo_from is None else expo_from
            r = src[max(start, j - 59): j + 1]
            r = r[np.isfinite(r)]
            r = r[r != 0.0] if len(r) > 60 else r
            if len(r) >= 40:
                vol = float(np.std(r, ddof=1)) * np.sqrt(TD)
                expo = min(1.0, vt / vol) if vol > 1e-9 else 1.0
        tgt = {c: w * expo for c, w in wt.items()}
        last_reb = dates[i].date()

        cur_w = {c: sh * px[i, cols[c]] / nav for c, sh in shares.items()} if nav > 0 else {}
        common = set(cur_w) & set(tgt)
        drift.append(float(sum(abs(cur_w[c] - tgt[c]) for c in common)))
        gap.append(abs(sum(tgt.values()) - sum(cur_w.values())))
        swapped.append(len(set(cur_w) ^ set(tgt)) / 2.0)
        cost_today = turn_today = 0.0

        def do_sell(c):
            nonlocal cash, cost_today, turn_today
            p = float(px[i, cols[c]])
            amt = shares[c] * p
            held = (dates[i].date() - dates[open_i[c]].date()).days
            fee = sell_comm + (redeem_short if held < MIN_HOLD_DAYS else 0.0)
            cash += amt * (1 - fee)
            cost_today += amt * fee / nav
            turn_today += amt / nav
            del shares[c]
            open_i.pop(c, None)

        # 卖出：不在目标里的都卖（两档一致；<7 天顺延）
        for c in list(shares):
            if c in tgt:
                continue
            p = px[i, cols[c]]
            if not np.isfinite(p) or p <= 0:
                continue
            if (dates[i].date() - dates[open_i[c]].date()).days < MIN_HOLD_DAYS:
                continue
            do_sell(c)

        # full：超配也砍回目标
        if mode == "full":
            for c in list(shares):
                p = px[i, cols[c]]
                if not np.isfinite(p) or p <= 0:
                    continue
                need = tgt[c] * nav - shares[c] * p
                if need >= -1e-6:
                    continue
                cut = int(-need / p // LOT) * LOT
                if cut <= 0:
                    continue
                amt = cut * float(p)
                held = (dates[i].date() - dates[open_i[c]].date()).days
                fee = sell_comm + (redeem_short if held < MIN_HOLD_DAYS else 0.0)
                shares[c] -= cut
                cash += amt * (1 - fee)
                cost_today += amt * fee / nav
                turn_today += amt / nav

        # 买入：两档都只补低配（按目标权重从大到小，现金用完为止）
        for c, w in sorted(tgt.items(), key=lambda kv: -kv[1]):
            p = px[i, cols[c]]
            if not np.isfinite(p) or p <= 0:
                continue
            need = w * nav - shares.get(c, 0) * float(p)
            if need <= 0:
                continue
            buy = int(min(need, cash / (1 + buy_cost)) / float(p) // LOT) * LOT
            if buy <= 0:
                continue
            amt = buy * float(p)
            if c not in shares:
                shares[c] = 0
                open_i[c] = i
            shares[c] += buy
            cash -= amt * (1 + buy_cost)
            cost_today += amt * buy_cost / nav
            turn_today += amt / nav

        cost_a[i] = cost_today
        turn_a[i] = turn_today

    return {"ret": ret, "cost": cost_a, "turn": turn_a, "expo": expo_a,
            "drift": np.array(drift), "gap": np.array(gap), "swapped": np.array(swapped), "n_reb": n_reb, "dates": dates}


def metrics(res, lo):
    r = res["ret"][lo:]
    eq = np.cumprod(1 + r)
    years = len(r) / TD
    cagr = eq[-1] ** (1 / years) - 1
    mdd = float((eq / np.maximum.accumulate(eq) - 1).min())
    return {"cagr": cagr, "vol": float(np.std(r, ddof=1) * np.sqrt(TD)), "mdd": mdd,
            "calmar": cagr / abs(mdd), "turn": float(np.sum(res["turn"][lo:]) / years),
            "cost": float(np.sum(res["cost"][lo:]) / years),
            "expo": float(np.mean(res["expo"][lo:])), "drift": float(np.mean(res["drift"])),
            "gap": float(np.mean(res["gap"])), "swapped": float(np.mean(res["swapped"]))}


def main():
    close = E.load_panel("ext")
    f = E.features(close)
    lo = int(close.index.searchsorted(pd.Timestamp(EVAL_START)))
    print("panel %d 只，%s ~ %s，评估起点 %s" % (
        close.shape[1], close.index[0].date(), close.index[-1].date(), close.index[lo].date()))

    variants = {}
    s2_full = run(close, f, "full")
    s2_top = run(close, f, "topup")
    vt_full = run(close, f, "full", expo_from=s2_full["ret"], vt=0.15)
    vt_top = run(close, f, "topup", expo_from=s2_top["ret"], vt=0.15)
    for name, res in (("S2 满仓 full", s2_full), ("S2 满仓 topup", s2_top),
                      ("VT15 full", vt_full), ("VT15 topup", vt_top)):
        variants[name] = metrics(res, lo)

    hdr = ("%-14s %8s %7s %8s %7s %7s %7s %7s %7s" %
           ("", "CAGR", "vol", "MDD", "Calmar", "换手", "成本", "敞口", "漂移L1"))
    print("\n" + hdr)
    print("-" * len(hdr))
    for k, m in variants.items():
        print("%-14s %7.2f%% %6.2f%% %7.2f%% %7.2f %6.2fx %6.2f%% %6.1f%% %7.3f" % (
            k, m["cagr"] * 100, m["vol"] * 100, m["mdd"] * 100, m["calmar"],
            m["turn"], m["cost"] * 100, m["expo"] * 100, m["drift"]))

    for a, b in (("S2 满仓 full", "S2 满仓 topup"), ("VT15 full", "VT15 topup")):
        x, y = variants[a], variants[b]
        print("%s→%s: CAGR %+.2fpp | 换手 %+.2f 倍 | 成本 %+.2fpp | 回撤 %+.2fpp | 卡玛 %+.2f" % (
            a, b, (y["cagr"] - x["cagr"]) * 100, y["turn"] - x["turn"],
            (y["cost"] - x["cost"]) * 100, (y["mdd"] - x["mdd"]) * 100, y["calmar"] - x["calmar"]))
    print("调仓次数 full=%d topup=%d" % (s2_full["n_reb"], s2_top["n_reb"]))
    return variants


if __name__ == "__main__":
    main()



