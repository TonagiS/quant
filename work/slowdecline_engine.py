#!/usr/bin/env python3
"""engine.simulate 的副本（2026-11-21 slowdecline）：仅新增 stop_mode="trail"（自入场后最高收盘价回撤止损）。
其余逐行照抄 position/engine.py；stop=None 时与原引擎逐日一致（见 slowdecline.py 的自检）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "position"))
from engine import Config, _scheme_weights, _update_entries  # noqa: E402,F401


def simulate(close: pd.DataFrame, f: dict, cfg: Config, keep_weights: bool = False) -> dict:
    T = len(close)
    ret = f["ret"].to_numpy()
    ret0 = np.nan_to_num(ret, nan=0.0)
    na = np.isnan(ret)
    vol_a = f["vol20"].to_numpy()
    rm_a = f["rm"].to_numpy()
    cand_a = f["cand"].to_numpy()
    ma60_a = f["ma60"].to_numpy()
    bench = f["bench"].to_numpy()
    base_ret = f.get("base_ret")
    start = f["start"]

    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_up, lim_dn = ret >= lim * 0.98, ret <= -lim * 0.98

    rule = cfg.rule
    rule.reset()
    base_ret = f.get("base_ret")
    if base_ret is None and getattr(rule, "requires_base", False):
        base_ret = base_returns(close, f, cfg)
    w: dict[int, float] = {}
    s_target = 1.0
    entry_px: dict[int, float] = {}
    entry_i: dict[int, int] = {}
    hi: dict = {}
    sched: dict[int, tuple] = {}

    port = np.zeros(T)
    gross_a = np.zeros(T)
    cost_a = np.zeros(T)
    turn_a = np.zeros(T)
    expo_a = np.zeros(T)
    scale_a = np.ones(T)
    equity_a = np.ones(T)
    w_hist = []
    # 调仓计时状态（rolling / rolling_change 用）
    last_reb_i = start
    prev_target_names: list = []
    prev_target_w: dict = {}
    n_due = n_skip = 0

    for i in range(T):
        gross = float(sum(wt * ret0[i, c] for c, wt in w.items()))
        cost_today = 0.0
        turn_today = 0.0

        def apply(new_w: dict):
            nonlocal w, cost_today, turn_today, entry_px, entry_i
            bc = cfg.cost if cfg.buy_cost is None else cfg.buy_cost
            sc = cfg.cost if cfg.sell_cost is None else cfg.sell_cost
            cost_step = 0.0
            turn_step = 0.0
            for c in set(w) | set(new_w):
                d = new_w.get(c, 0.0) - w.get(c, 0.0)
                if d > 0:
                    cost_step += bc * d
                    turn_step += d
                elif d < 0:
                    fee = sc
                    if cfg.fee_tier:
                        # 该标的实际持有自然日 ≈ 交易日数 × 7/5
                        held_cal = (i - entry_i.get(c, i)) * 7.0 / 5.0
                        if held_cal < cfg.tier_short_days:
                            fee = bc + cfg.fee_short
                        elif held_cal < cfg.tier_long_days:
                            fee = bc + cfg.fee_mid
                        else:
                            fee = bc + cfg.fee_long
                    cost_step += fee * (-d)
                    turn_step += (-d)
            if turn_step > 0:
                cost_today += cost_step
                turn_today += turn_step
            w = new_w
            entry_px, entry_i = _update_entries(entry_px, entry_i, w, i, close)

        # 1) 调仓（信号来自 delay 天前）
        if i in sched:
            target, vrow = sched.pop(i)
            tset = set(target)
            buy = [c for c in target if not lim_up[i, c] and not na[i, c]]
            stuck = [c for c in w if c not in tset and (lim_dn[i, c] or na[i, c])]
            names = buy + [c for c in stuck if c not in buy]

            j = max(0, i - 1)
            ctx = {"rets": port, "equity": equity_a, "bench": bench, "base_rets": base_ret,
                   "start": start}
            s_new = float(np.clip(rule.scale(j, ctx), 0.0, 1.0))
            base = _scheme_weights(cfg.weight_mode, names, i, close, f, cfg.cap, vrow)
            apply({c: x * s_new for c, x in base.items()})
            s_target = s_new

        # 2) 定期/每日敞口调整（截至 i-1 的数据，收盘成交）
        if i >= start + cfg.delay and (i - start - cfg.delay) % max(rule.every, 1) == 0:
            j = max(0, i - 1)
            ctx = {"rets": port, "equity": equity_a, "bench": bench, "base_rets": base_ret,
                   "start": start}
            s_new = float(np.clip(rule.scale(j, ctx), 0.0, 1.0))
            if abs(s_new - s_target) > 1e-9 and s_target > 1e-9:
                apply({c: x * (s_new / s_target) for c, x in w.items()})
            s_target = s_new
        scale_a[i] = s_target

        # 3) 止损（收盘触发、收盘卖；跌停/停牌当天卖不掉）
        if cfg.stop is not None and w:
            to_sell = []
            for c in list(w):
                if i <= entry_i.get(c, i):
                    continue
                px = close.iat[i, c]
                if not np.isfinite(px) or lim_dn[i, c]:
                    continue
                if cfg.stop_mode == "entry":
                    ep = entry_px.get(c)
                    if ep is not None and np.isfinite(ep) and px / ep - 1.0 <= -cfg.stop:
                        to_sell.append(c)
                elif cfg.stop_mode == "trail":
                    ei = entry_i.get(c, i)
                    hv, hi_i = hi.get(c, (px, ei))
                    if hi_i != ei:
                        hv = close.iat[ei, c] if np.isfinite(close.iat[ei, c]) else px
                    hv = max(hv, px)
                    hi[c] = (hv, ei)
                    if px / hv - 1.0 <= -cfg.stop:
                        to_sell.append(c)
                elif cfg.stop_mode == "ma60":
                    m = ma60_a[i, c]
                    if np.isfinite(m) and px < m:
                        to_sell.append(c)
            if to_sell:
                apply({c: x for c, x in w.items() if c not in to_sell})

        expo_a[i] = float(sum(w.values()))
        port[i] = gross - cost_today
        gross_a[i] = gross
        cost_a[i] = cost_today
        turn_a[i] = turn_today
        equity_a[i] = (equity_a[i - 1] if i > 0 else 1.0) * (1.0 + port[i])
        if keep_weights:
            w_hist.append(dict(w))

        # 生成下一次调仓计划（当日信号，delay 天后收盘执行）
        if i >= start:
            if cfg.reb_mode == "grid":
                due = ((i - start) % cfg.reb == 0)
            else:
                due = (i - last_reb_i) >= cfg.reb
            if due:
                n_due += 1
                ok = np.flatnonzero(cand_a[i] & ~np.isnan(rm_a[i]))
                order = [int(c) for c in ok[np.argsort(-rm_a[i, ok])]]
                if cfg.buffer_n and w:
                    keep = [c for c in w if c in set(order[: cfg.buffer_n])]
                    target = keep[: cfg.k]
                    for c in order:
                        if len(target) >= cfg.k:
                            break
                        if c not in target:
                            target.append(c)
                else:
                    target = order[: cfg.k]
                do_reb = True
                if cfg.reb_mode == "rolling_change" and target == prev_target_names:
                    new_w = _scheme_weights(cfg.weight_mode, target, i, close, f, cfg.cap, vol_a[i])
                    do_reb = any(abs(prev_target_w.get(c, 0.0) - new_w.get(c, 0.0)) > cfg.reb_change_tol
                                 for c in set(prev_target_w) | set(new_w))
                if do_reb:
                    sched[i + cfg.delay] = (target, vol_a[i].copy())
                    prev_target_names = list(target)
                    prev_target_w = _scheme_weights(cfg.weight_mode, target, i, close, f, cfg.cap, vol_a[i])
                    if cfg.reb_mode != "grid":
                        last_reb_i = i
                else:
                    n_skip += 1
                    if cfg.reb_mode == "rolling":
                        last_reb_i = i

    return {"ret": port, "gross": gross_a, "cost": cost_a, "turn": turn_a,
            "expo": expo_a, "scale": scale_a, "start": start,
            "n_due": n_due, "n_skip": n_skip,
            "w_hist": w_hist, "dates": close.index, "equity": equity_a}


