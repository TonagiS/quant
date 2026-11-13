# -*- coding: utf-8 -*-
"""调仓计时语义 A/B 对照：到点即清零 vs 换过才清零。

“变了”的判定分三档：
  names = 只认名单（前 k 名的集合）变化
  w5    = 名单变 或 单票权重漂移 > 5pp
  w1    = 名单变 或 单票权重漂移 > 1pp（最敏感）

成本按场外分档赎回费（<7 天 1.5%、7~30 天 0.5%、>=30 天 0.15%）+ 单边 5bp 佣金。
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "position"))
import engine  # noqa: E402

TD = 252
BUY = 0.0005
FEE_SHORT, FEE_MID, FEE_LONG = 0.015, 0.005, 0.0015


def targets_at(i, cand, rm, vol20, close, f, k=5):
    row = np.where(cand[i])[0]
    if len(row) == 0:
        return {}
    order = row[np.argsort(-rm[i][row])][:k]
    return engine._scheme_weights("invvol", list(order), i, close, f, 1.0, vol20[i])


def changed(old, new, mode):
    if set(old) != set(new):
        return True
    if mode == "names":
        return False
    tol = 0.05 if mode == "w5" else 0.01
    return any(abs(old[c] - new.get(c, 0.0)) > tol for c in old)


def run(close, f, cadence, reset_always, change_mode, vt=False, delay=1, k=5):
    ret = f["ret"].to_numpy()
    ret0 = np.nan_to_num(ret, nan=0.0)
    na = np.isnan(ret)
    vol20 = f["vol20"].to_numpy()
    rm = f["rm"].to_numpy()
    cand = f["cand"].to_numpy()
    start = f["start"]
    T = len(close)
    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_up, lim_dn = ret >= lim * 0.98, ret <= -lim * 0.98

    w, entry_i, sched = {}, {}, {}
    port = np.zeros(T)
    turn = np.zeros(T)
    cost = np.zeros(T)
    last_reb = None
    n_reb = n_due = n_skip = 0
    gaps = []
    cur_target = {}

    for i in range(T):
        gross = float(sum(wt * ret0[i, c] for c, wt in w.items()))
        c_today = t_today = 0.0

        if i in sched:
            tgt, expo = sched.pop(i)
            new_w = {c: x * expo for c, x in tgt.items() if not lim_up[i, c] and not na[i, c]}
            for c in [c for c in w if c not in tgt and (lim_dn[i, c] or na[i, c])]:
                new_w[c] = w[c]
            for c in set(w) | set(new_w):
                d = new_w.get(c, 0.0) - w.get(c, 0.0)
                if abs(d) < 1e-9:
                    continue
                if d > 0:
                    c_today += BUY * d
                    t_today += d
                else:
                    held_cal = (i - entry_i.get(c, i)) * 7.0 / 5.0
                    fee = BUY + (FEE_SHORT if held_cal < 7 else (FEE_MID if held_cal < 30 else FEE_LONG))
                    c_today += fee * (-d)
                    t_today += (-d)
            w = new_w
            for c in list(w):
                entry_i.setdefault(c, i)
            entry_i = {c: v for c, v in entry_i.items() if c in w}
            n_reb += 1
            if last_reb is not None:
                gaps.append(i - last_reb)
            last_reb = i

        active = i >= start + delay
        port[i] = gross - (c_today if active else 0.0)
        turn[i] = t_today if active else 0.0
        cost[i] = c_today if active else 0.0

        if i >= start and (last_reb is None or (i - last_reb) >= cadence):
            n_due += 1
            tgt = targets_at(i, cand, rm, vol20, close, f, k)
            expo = 1.0
            if vt and i >= 61:
                v60 = float(np.std(port[i - 60:i], ddof=1) * np.sqrt(TD))
                if v60 > 0:
                    expo = min(1.0, 0.15 / v60)
            if changed(cur_target, tgt, change_mode):
                sched[i + delay] = (tgt, expo)
                cur_target = tgt
            else:
                n_skip += 1
                if reset_always:
                    last_reb = i

    res = engine.perf(port[start:])
    days = T - start
    res.update({"turn": float(turn[start:].sum() / (days / TD)),
                "cost": float(cost[start:].sum() / (days / TD)),
                "n_reb": n_reb, "n_due": n_due, "n_skip": n_skip,
                "gap": float(np.mean(gaps)) if gaps else float("nan"),
                "gap_max": int(np.max(gaps)) if gaps else 0})
    return res


def main():
    rows = []
    for pool in ("ext", "base"):
        close = engine.load_panel(pool)
        f = engine.features(close)
        for cadence in (10, 14):
            for cm in ("names", "w5"):
                for mode, ra in (("A 到点即清零", True), ("B 换过才清零", False)):
                    r = run(close, f, cadence, ra, cm, vt=True)
                    rows.append({"pool": pool, "cad": cadence, "判定": cm, "模式": mode,
                                 "cagr": r["cagr"], "mdd": r["mdd"], "calmar": r["calmar"],
                                 "turn": r["turn"], "cost": r["cost"], "调仓次数": r["n_reb"],
                                 "到点": r["n_due"], "空转": r["n_skip"], "平均间隔": r["gap"],
                                 "最长间隔": r["gap_max"]})
    df = pd.DataFrame(rows)
    fmt = df.copy()
    for c in ("cagr", "mdd", "cost"):
        fmt[c] = (fmt[c] * 100).round(2)
    for c in ("calmar", "turn", "平均间隔"):
        fmt[c] = fmt[c].round(2)
    pd.set_option("display.width", 250, "display.max_columns", 30)
    print("（VT15 口径，场外分档费）")
    print(fmt.to_string(index=False))
    out = os.path.join(ROOT, "runs", "2026-11-10_reb-reset-ab")
    os.makedirs(out, exist_ok=True)
    df.to_csv(os.path.join(out, "results.csv"), index=False, encoding="utf-8-sig")
    print("\n->", os.path.join(out, "results.csv"))


if __name__ == "__main__":
    main()
