"""P2-TURN-01 预登记检验：降低换手能否把 S2 的盈亏平衡成本推到 >=30bp，且不损失超额/卡玛。

变体（固定 3 个）：
  V0 = 现行 S2（k5 reb5 delay1 cost 网格）
  V1 = 调仓周期 x2（reb=10）
  V2 = 缓冲带：持仓排名掉出前 k+2 才换出（reb=5）
  V3 = V1 + V2（reb=10 + 缓冲带）
判据（某变体通过 = base、ext 两池同时满足 C1~C4）：
  C1 盈亏平衡成本 >= 30bp（网格与 P2-COST-01 相同，线性插值）
  C2 5bp 下年化超额 >= V0 - 1.0pp 且 卡玛 >= V0 - 0.05
  C3 年化换手较 V0 下降 >= 30%
  C4 delay=1/2、剔除前 3 只后，超额仍 > 0
选择规则：V1 > V2 > V3，取最先通过的；全不通过则如实判未通过。

实现说明：V0/V1 直接调用 backtest_composite.run_portfolio；V2/V3 需要缓冲带，用本脚本内的
复制版 run_portfolio_buf（buffer_n=None 时必须与 run_portfolio 完全一致，脚本启动时自检）。
"""
import json
import os
import sys
import datetime

import numpy as np
import pandas as pd

import backtest_composite as bc
from macd_structure import ETF_POOL
from pools import get_pool, load_one

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
CACHE_2019 = os.path.join(ROOT, "data", "cache2019")
MIN_NAMES, LOOKBACK = 8, 120
START = "20190101"
K, REB0, DELAY = 5, 5, 1
GRID = [0.0005, 0.001, 0.002, 0.003, 0.004, 0.006]
S2 = dict(mom=1, trend=True, invvol=True)


def load_pool(pool_name, start, cache):
    data = {}
    for code, (name, kind) in get_pool(pool_name, ETF_POOL).items():
        df, _ = load_one(code, kind, start, cache)
        data[code] = df
    return data


def prep(f):
    valid = f["valid"]
    rk = {k: bc.xs_rank(f[k], valid, MIN_NAMES) for k in ("mom5", "mom20", "mom60", "mom120")}
    rk["vol_ratio"] = bc.xs_rank(f["vol_ratio"], f["valid_vol"], MIN_NAMES)
    rk["crowd"] = bc.xs_rank(f["crowd"], f["valid_vol"], MIN_NAMES)
    rm = (rk["mom20"] + rk["mom60"] + rk["mom120"]) / 3
    enough = rm.notna().any(axis=1).to_numpy()
    start = int(np.argmax(enough))
    bench = f["ret"].where(valid).mean(axis=1).fillna(0).to_numpy().copy()
    bench[:start] = 0.0
    return rm, start, bench


def run_portfolio_buf(ret, score, cand, vol, lim_up, lim_dn, k, reb, cost, delay, start,
                      buffer_n=None, invvol=True):
    """backtest_composite.run_portfolio 的复制版，仅增加 buffer_n（保留排名在 top buffer_n 内的持仓）。"""
    n, _ = ret.shape
    ret0 = np.nan_to_num(ret, nan=0.0)
    na = np.isnan(ret)
    w, sched, port, turn = {}, {}, np.zeros(n), []
    for i in range(n):
        day = sum(wt * ret0[i, c] for c, wt in w.items())
        if i >= start and (i - start) % reb == 0:
            ok = np.flatnonzero(cand[i] & ~np.isnan(score[i]))
            order = [int(c) for c in ok[np.argsort(-score[i, ok])]]
            if buffer_n is None:
                target = order[:k]
            else:
                keep = [c for c in w if c in set(order[:buffer_n])]
                target = keep[:k]
                for c in order:
                    if len(target) >= k:
                        break
                    if c not in target:
                        target.append(c)
                target = target[:k]
            sched[i + delay] = (target, vol[i].copy())
        if i in sched:
            target, vrow = sched.pop(i)
            tset = set(target)
            buy = [c for c in target if not lim_up[i, c] and not na[i, c]]
            stuck = [c for c in w if c not in tset and (lim_dn[i, c] or na[i, c])]
            names = buy + [c for c in stuck if c not in buy]
            new_w = {}
            if names:
                if invvol:
                    iv = np.array([1.0 / max(vrow[c], 1e-4) if vrow[c] == vrow[c] else np.nan for c in names])
                    iv = np.where(np.isnan(iv), np.nanmean(iv) if (~np.isnan(iv)).any() else 1.0, iv)
                    wts = iv / iv.sum()
                else:
                    wts = np.full(len(names), 1.0 / len(names))
                new_w = dict(zip(names, wts))
            traded = sum(abs(new_w.get(c, 0.0) - w.get(c, 0.0)) for c in set(w) | set(new_w))
            day -= cost * traded
            turn.append(traded)
            w = new_w
        port[i] = day
    return port, (np.mean(turn) if turn else 0.0)


def make_inputs(f, rm):
    ret = f["ret"].to_numpy()
    vol = f["vol20"].to_numpy()
    close = f["close"]
    rm_fill = np.nan_to_num((rm - 0.5).to_numpy(), nan=0.0)
    cand = (f["valid"] & rm.notna()).to_numpy() & (close > f["ma60"]).to_numpy()
    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_up, lim_dn = ret >= lim * 0.98, ret <= -lim * 0.98
    return ret, rm_fill, cand, vol, lim_up, lim_dn


def sim(ret, score, cand, vol, lim_up, lim_dn, start, reb, cost, delay, buffer_n):
    if buffer_n is None:
        p, _ = bc.run_portfolio(ret, score, cand, vol, lim_up, lim_dn, K, reb, cost, delay,
                                start, invvol=True)
        return p
    p, _ = run_portfolio_buf(ret, score, cand, vol, lim_up, lim_dn, K, reb, cost, delay, start,
                             buffer_n=buffer_n)
    return p


def breakeven(grid, excess):
    for i in range(1, len(grid)):
        if excess[i] <= 0 < excess[i - 1]:
            return grid[i - 1] + excess[i - 1] / (excess[i - 1] - excess[i]) * (grid[i] - grid[i - 1])
    return None


def subset(f, exclude):
    return {k: (v.drop(columns=[c for c in exclude if c in v.columns]) if isinstance(v, pd.DataFrame) else v)
            for k, v in f.items()}


VARIANTS = [("V0 现行", REB0, None), ("V1 rebx2", 10, None),
            ("V2 缓冲带", REB0, K + 2), ("V3 rebx2+缓冲", 10, K + 2)]


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("P2-TURN-01 预登记检验（3 个变体，判据见 registry.csv，运行前冻结）")
    summary = []
    for pool in ("base", "ext"):
        print("\n" + "=" * 92)
        print(f"池 {pool}")
        data = load_pool(pool, START, CACHE_2019)
        close, amt = bc.build_panel(data)
        f = bc.build_features(close, amt, LOOKBACK)
        rm, start, bench = prep(f)
        dates = close.index
        live = np.arange(len(dates)) >= start
        ret, score, cand, vol, lu, ld = make_inputs(f, rm)
        mb = bc.perf(bench[live])
        print(f"  面板 {close.shape[1]} 只，实盘 {dates[start].date()} ~ {dates[-1].date()}，等权 {mb['年化']*100:.2f}%")

        # 自检：custom(buffer=None) 必须与 run_portfolio 完全一致
        p_bc, _ = bc.run_portfolio(ret, score, cand, vol, lu, ld, K, REB0, 0.0005, DELAY, start, invvol=True)
        p_cu, _ = run_portfolio_buf(ret, score, cand, vol, lu, ld, K, REB0, 0.0005, DELAY, start, buffer_n=None)
        chk = float(np.nanmax(np.abs(p_bc - p_cu)))
        print(f"  自检 custom vs run_portfolio 最大差 {chk:.2e}")
        assert chk < 1e-12, "复制版与 run_portfolio 不一致，停止"

        rows = []
        v0_excess = v0_calmar = v0_turn = None
        for name, reb, buf in VARIANTS:
            exc, turn = [], None
            for c in GRID:
                p = sim(ret, score, cand, vol, lu, ld, start, reb, c, DELAY, buf)
                m = bc.perf(p[live])
                exc.append(m["年化"] - mb["年化"])
                if c == 0.0005:
                    v05 = m
                    _, turn_mean = (bc.run_portfolio(ret, score, cand, vol, lu, ld, K, reb, c, DELAY, start, invvol=True)
                                    if buf is None else
                                    run_portfolio_buf(ret, score, cand, vol, lu, ld, K, reb, c, DELAY, start, buffer_n=buf))
                    turn = turn_mean * (252 / reb)
            be = breakeven(GRID, np.array(exc))
            rows.append({"变体": name, "reb": reb, "buffer": buf or 0,
                         "5bp年化": v05["年化"], "5bp超额": exc[0], "5bp卡玛": v05["卡玛"],
                         "年化双边换手": turn, "盈亏平衡成本": be,
                         "盈亏平衡bp": None if be is None else round(be * 10000)})
            if name.startswith("V0"):
                v0_excess, v0_calmar, v0_turn = exc[0], v05["卡玛"], turn
        g = pd.DataFrame(rows)

        # C4：delay=2 与 剔除前3
        full_ret = {c: close[c].dropna().iloc[-1] / close[c].dropna().iloc[0] - 1 for c in close.columns}
        top3 = set(sorted(full_ret, key=lambda c: -full_ret[c])[:3])
        f3 = subset(f, top3)
        rm3, start3, bench3 = prep(f3)
        live3 = np.arange(len(dates)) >= start3
        ret3, score3, cand3, vol3, lu3, ld3 = make_inputs(f3, rm3)
        mb3 = bc.perf(bench3[live3])
        c4 = {}
        for name, reb, buf in VARIANTS:
            p2 = sim(ret, score, cand, vol, lu, ld, start, reb, 0.0005, 2, buf)
            e2 = bc.perf(p2[live])["年化"] - mb["年化"]
            p3 = sim(ret3, score3, cand3, vol3, lu3, ld3, start3, reb, 0.0005, DELAY, buf)
            e3 = bc.perf(p3[live3])["年化"] - mb3["年化"]
            c4[name] = {"delay2超额": e2, "剔除前3超额": e3, "ok": bool(e2 > 0 and e3 > 0)}

        for _, r in g.iterrows():
            nm = r["变体"]
            c1 = r["盈亏平衡成本"] is not None and r["盈亏平衡成本"] >= 0.003
            c2 = (r["5bp超额"] >= v0_excess - 0.01) and (r["5bp卡玛"] >= v0_calmar - 0.05)
            c3 = r["年化双边换手"] <= v0_turn * 0.70
            ok = bool(c1 and c2 and c3 and c4[nm]["ok"])
            summary.append({"池": pool, "变体": nm, "C1_盈亏平衡≥30bp": bool(c1),
                            "C2_超额≥V0-1pp且卡玛≥V0-0.05": bool(c2), "C3_换手降≥30%": bool(c3),
                            "C4_delay2与剔除前3均>0": c4[nm]["ok"],
                            "盈亏平衡bp": r["盈亏平衡bp"], "5bp超额pp": round(r["5bp超额"] * 100, 2),
                            "5bp卡玛": round(r["5bp卡玛"], 2), "换手%": round(r["年化双边换手"] * 100),
                            "delay2超额pp": round(c4[nm]["delay2超额"] * 100, 2),
                            "剔除前3超额pp": round(c4[nm]["剔除前3超额"] * 100, 2),
                            "通过": ok})
        print("\n" + g.assign(**{c: g[c].map(lambda x: f"{x*100:.2f}%" if c == "5bp年化" else x)
                                 for c in ["5bp年化"]}).to_string(index=False))
        print("\n  C4 明细：" + json.dumps({k: {kk: round(vv, 4) if isinstance(vv, float) else vv
                                              for kk, vv in v.items()} for k, v in c4.items()}, ensure_ascii=False))

    s = pd.DataFrame(summary)
    s.to_csv("turn_summary.csv", index=False, encoding="utf-8-sig")
    # 选择：V1 > V2 > V3，且两池都通过
    chosen = None
    for v in ("V1 rebx2", "V2 缓冲带", "V3 rebx2+缓冲"):
        sub = s[s["变体"] == v]
        if len(sub) == 2 and bool(sub["通过"].all()):
            chosen = v
            break
    with open("turn_meta.json", "w", encoding="utf-8") as fp:
        json.dump({"id": "P2-TURN-01", "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "grid": GRID, "k": K, "base_reb": REB0, "delay": DELAY,
                   "chosen": chosen, "passed": chosen is not None}, fp, indent=2, ensure_ascii=False)
    print("\n" + s.to_string(index=False))
    print(f"\n选择结果：{chosen if chosen else '无变体通过 → S2 维持“成本脆弱”'}")


if __name__ == "__main__":
    main()
