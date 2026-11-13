"""P2-COST-01 预登记检验：S2 的成本边界（只测 base、ext）。

判据（registry.csv 已登记）：两个池的盈亏平衡成本（相对等权年化超额=0 的最小成本，线性插值）
都 ≥ 0.003（30bp 单边）才算通过；否则判「成本脆弱」。

方法：基线参数不动（k5 reb5 delay1 start20190101），cost 网格 0.0005/0.001/0.002/0.003/0.004/0.006，
每个成本跑一次 S2，与等权基准年化比较；盈亏平衡点用相邻两点线性插值。
另报 S2 年化换手（run_portfolio 返回的每次调仓 L1 已实现换手 × 252/reb，双边；单边减半）。
流动性诊断（不参与判定）：各 ETF 实盘区间日均成交额、目标持仓日落在当日成交额最低三分位的占比。
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
CACHE_2019 = os.path.join(BASE_DIR, "work", "cache2019")
MIN_NAMES, LOOKBACK = 8, 120
START = "20190101"
K, REB, DELAY = 5, 5, 1
GRID = [0.0005, 0.001, 0.002, 0.003, 0.004, 0.006]
S2 = dict(mom=1, trend=True, invvol=True)


def load_pool(pool_name, start, cache):
    data, failed = {}, []
    for code, (name, kind) in get_pool(pool_name, ETF_POOL).items():
        try:
            df, _ = load_one(code, kind, start, cache)
            data[code] = df
        except Exception as e:
            failed.append((name, code, str(e)[:100]))
    return data, failed


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
    return rk, rm, start, bench


def simulate(f, rm, start, cost, k=K, reb=REB, delay=DELAY):
    ret = f["ret"].to_numpy()
    vol = f["vol20"].to_numpy()
    close = f["close"]
    rm_fill = np.nan_to_num((rm - 0.5).to_numpy(), nan=0.0)
    cand = (f["valid"] & rm.notna()).to_numpy() & (close > f["ma60"]).to_numpy()
    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_up, lim_dn = ret >= lim * 0.98, ret <= -lim * 0.98
    p, turn = bc.run_portfolio(ret, rm_fill, cand, vol, lim_up, lim_dn, k, reb, cost, delay,
                               start, invvol=True)
    return p, turn, cand, rm_fill


def breakeven(grid, excess):
    """网格上找超额首次 <=0 的位置，线性插值；全为正返回 None（表示 > 最大网格）。"""
    for i in range(1, len(grid)):
        if excess[i] <= 0 < excess[i - 1]:
            return grid[i - 1] + excess[i - 1] / (excess[i - 1] - excess[i]) * (grid[i] - grid[i - 1])
    return None


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("P2-COST-01 预登记检验：S2 成本边界（判据见 registry.csv，运行前冻结）")
    print(f"基线 k={K} reb={REB} delay={DELAY} start={START}；cost 网格 {GRID}")
    summary, all_liquidity = [], []
    for pool_name in ("base", "ext"):
        print("\n" + "=" * 90)
        print(f"池 {pool_name}")
        data, failed = load_pool(pool_name, START, CACHE_2019)
        close, amt = bc.build_panel(data)
        f = bc.build_features(close, amt, LOOKBACK)
        rk, rm, start, bench = prep(f)
        dates = close.index
        live = np.arange(len(dates)) >= start
        mb = bc.perf(bench[live])
        print(f"  面板 {close.shape[1]} 只，实盘 {dates[start].date()} ~ {dates[-1].date()}，"
              f"等权年化 {mb['年化']*100:.2f}%")

        rows = []
        for c in GRID:
            p, turn_mean, cand, rm_fill = simulate(f, rm, start, c)
            m = bc.perf(p[live])
            rows.append({"cost": c, "年化": m["年化"], "回撤": m["回撤"], "夏普": m["夏普"],
                         "超额": m["年化"] - mb["年化"],
                         "年化双边换手": turn_mean * (252 / REB),
                         "年化单边换手": turn_mean * (252 / REB) / 2})
        grid_df = pd.DataFrame(rows)

        be = breakeven(GRID, grid_df["超额"].to_numpy())
        print(grid_df.assign(年化=lambda d: (d["年化"] * 100).map(lambda x: f"{x:.2f}%"),
                             超额=lambda d: (d["超额"] * 100).map(lambda x: f"{x:+.2f}pp"),
                             年化双边换手=lambda d: (d["年化双边换手"] * 100).map(lambda x: f"{x:.0f}%")
                             )[["cost", "年化", "回撤", "夏普", "超额", "年化双边换手"]].to_string(index=False))
        print(f"  盈亏平衡成本：{'未触及（>'+str(max(GRID))+'）' if be is None else f'{be*10000:.0f} bp 单边'}")

        # 流动性诊断（近似：调仓日目标持仓，未计涨跌停/停牌阻挡）
        amt_np = amt.to_numpy()
        vol_mean = np.nanmean(np.where(amt_np > 0, amt_np, np.nan), axis=0)  # 区间日均成交额
        pct_rank = pd.DataFrame(amt_np, index=dates).rank(axis=1, pct=True).to_numpy()
        target = np.full(close.shape[1], -1, dtype=int)
        held_low, held_tot = 0, 0
        for i in range(start, len(dates)):
            if (i - start) % REB == 0:
                ok = np.flatnonzero(cand[i] & ~np.isnan(rm_fill[i]))
                target = np.array(sorted(ok[np.argsort(-rm_fill[i, ok])[:K]]), dtype=int) if len(ok) else np.array([], dtype=int)
            for j in target:
                if np.isfinite(pct_rank[i, j]):
                    held_tot += 1
                    if pct_rank[i, j] < 1 / 3:
                        held_low += 1
        liq = pd.DataFrame({"代码": close.columns, "日均成交额亿元": vol_mean / 1e8})
        liq = liq.sort_values("日均成交额亿元")
        liq.to_csv(f"cost_liquidity_{pool_name}.csv", index=False, encoding="utf-8-sig")
        low_share = held_low / held_tot if held_tot else float("nan")
        print(f"  目标持仓落在当日成交额最低三分位的占比：{low_share:.1%}（{held_low}/{held_tot}）")
        print("  成交额最低 3 只：" + ", ".join(f"{r['代码']}({r['日均成交额亿元']:.2f}亿)" for _, r in liq.head(3).iterrows()))

        grid_df.to_csv(f"cost_grid_{pool_name}.csv", index=False, encoding="utf-8-sig")
        summary.append({"池": pool_name, "等权年化": mb["年化"],
                        "盈亏平衡成本": be, "盈亏平衡bp": None if be is None else round(be * 10000),
                        "最小超额pp": grid_df["超额"].min() * 100,
                        "年化双边换手": grid_df["年化双边换手"].iloc[0],
                        "低流动持仓占比": low_share,
                        "日均成交额最低3只": ", ".join(liq["代码"].head(3))})
        all_liquidity.append(liq.head(5).assign(池=pool_name))

    s = pd.DataFrame(summary)
    ok = (s["盈亏平衡成本"].notna() & (s["盈亏平衡成本"] >= 0.003)).all()
    s["判据"] = np.where(s["盈亏平衡成本"].notna() & (s["盈亏平衡成本"] >= 0.003), "通过", "未通过")
    s.to_csv("cost_summary.csv", index=False, encoding="utf-8-sig")
    with open("cost_meta.json", "w", encoding="utf-8") as fp:
        json.dump({"id": "P2-COST-01", "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "grid": GRID, "k": K, "reb": REB, "delay": DELAY, "start": START,
                   "breakeven": {r["池"]: r["盈亏平衡成本"] for r in summary},
                   "passed": bool(ok)}, fp, indent=2, ensure_ascii=False)
    print("\n" + "=" * 90)
    print(s.assign(等权年化=lambda d: (d["等权年化"] * 100).map(lambda x: f"{x:.2f}%"),
                   最小超额pp=lambda d: d["最小超额pp"].map(lambda x: f"{x:+.2f}pp"),
                   年化双边换手=lambda d: (d["年化双边换手"] * 100).map(lambda x: f"{x:.0f}%"),
                   低流动持仓占比=lambda d: d["低流动持仓占比"].map(lambda x: f"{x:.1%}")
                   )[["池", "等权年化", "盈亏平衡bp", "最小超额pp", "年化双边换手", "低流动持仓占比", "判据"]].to_string(index=False))
    print(f"\n判定：{'成本边界通过（两池盈亏平衡≥30bp）' if ok else '成本脆弱（至少一池盈亏平衡<30bp）'}")


if __name__ == "__main__":
    main()
