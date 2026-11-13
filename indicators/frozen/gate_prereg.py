"""P1-GATE-01 预登记检验：等权指数 < 120 日均线则空仓。

纪律：不修改仓库任何文件；只读复用 backtest_composite 函数。参数与判据见 registry.csv，
运行前冻结；MA60/MA250 只作描述性敏感性，不参与判定。

闸门定义（冻结）：指数 = 池内有效标的当日等权收益累乘（与本仓库 bench 同口径，实盘前为 0）；
每日收盘计算，指数 < 120 日简单均线 → 目标仓位 0，否则 1；120 日线可用前仓位为 1；
信号按 delay 位移（delay=1，信号日后第 2 个交易日起生效）；仓位变动按 cost=0.0005 计费；空仓不计息。

对象：主检验 S2（动量+趋势+逆波动）日收益；描述性对照 等权基准同样套闸门。
对照：同平均仓位固定缩放 c × 原序列（c = 闸门仓位在实盘区间的均值）。
判据：①≥4 池「卡玛 − 固定缩放卡玛」>0 ②≥4 池最大回撤改善 ≥5pp；两项同时满足才通过。
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
CACHE_2007 = os.path.join(BASE_DIR, "work", "cache")
MIN_NAMES, LOOKBACK = 8, 120
DELAY, COST = 1, 0.0005
K, REB = 5, 5
MA_PRIMARY = 120
MA_SENS = (60, 120, 250)

POOLS = [
    ("base", "20190101", CACHE_2019),
    ("ext", "20190101", CACHE_2019),
    ("long", "20070101", CACHE_2007),
    ("stocks", "20070101", CACHE_2007),
    ("long+stocks", "20070101", CACHE_2007),
]

S2 = dict(mom=1, trend=True, invvol=True)


def load_pool(pool_name, start, cache):
    pool = get_pool(pool_name, ETF_POOL)
    data, failed = {}, []
    for code, (name, kind) in pool.items():
        try:
            df, _ = load_one(code, kind, start, cache)
            data[code] = df
        except Exception as e:
            failed.append((name, code, str(e)[:120]))
    return data, failed


def prep(f):
    valid = f["valid"]
    dates = f["close"].index
    rk = {k: bc.xs_rank(f[k], valid, MIN_NAMES) for k in ("mom5", "mom20", "mom60", "mom120")}
    rk["vol_ratio"] = bc.xs_rank(f["vol_ratio"], f["valid_vol"], MIN_NAMES)
    rk["crowd"] = bc.xs_rank(f["crowd"], f["valid_vol"], MIN_NAMES)
    rm = (rk["mom20"] + rk["mom60"] + rk["mom120"]) / 3
    enough = rm.notna().any(axis=1).to_numpy()
    start = int(np.argmax(enough))
    bench = f["ret"].where(valid).mean(axis=1).fillna(0).to_numpy().copy()
    bench[:start] = 0.0
    return rm, start, bench


def sim_s2(f, rm, start):
    ret = f["ret"].to_numpy()
    vol = f["vol20"].to_numpy()
    close = f["close"]
    rm_fill = np.nan_to_num((rm - 0.5).to_numpy(), nan=0.0)
    cand = (f["valid"] & rm.notna()).to_numpy()
    cand = cand & (close > f["ma60"]).to_numpy()
    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_up, lim_dn = ret >= lim * 0.98, ret <= -lim * 0.98
    p, _ = bc.run_portfolio(ret, rm_fill, cand, vol, lim_up, lim_dn, K, REB, COST, DELAY,
                            start, invvol=True)
    return p


def gate_positions(ew, window, delay=DELAY):
    lvl = np.cumprod(1.0 + ew)
    sma = pd.Series(lvl).rolling(window, min_periods=window).mean().to_numpy()
    sig = np.where(np.isnan(sma), 1.0, np.where(lvl < sma, 0.0, 1.0))
    shift = delay + 1  # 信号日之后第 shift 个交易日起生效
    pos = np.ones_like(sig)
    pos[shift:] = sig[:-shift]
    return pos


def apply_gate(p, pos, cost=COST):
    dpos = np.abs(np.diff(pos, prepend=pos[0]))
    return pos * p - cost * dpos


def pct(x):
    return "" if x != x else f"{x * 100:.1f}%"


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("P1-GATE-01 预登记检验（MA120，delay=1，cost=5bp，空仓不计息；判据见 registry.csv）")
    rows, sens_rows = [], []
    for pool_name, start_str, cache in POOLS:
        print("\n" + "-" * 96)
        print(f"池 {pool_name}（start={start_str}）")
        data, failed = load_pool(pool_name, start_str, cache)
        close, amt = bc.build_panel(data)
        f = bc.build_features(close, amt, LOOKBACK)
        rm, start, bench = prep(f)
        dates = close.index
        live = np.arange(len(dates)) >= start
        p = sim_s2(f, rm, start)
        print(f"  标的 {close.shape[1]} 只，失败 {len(failed)}，实盘 {dates[start].date()} ~ {dates[-1].date()}"
              f"（{int(live.sum())} 日）")

        base_m = bc.perf(p[live])
        # 主检验 MA120
        pos = gate_positions(bench, MA_PRIMARY)
        gated = apply_gate(p, pos)
        gate_m = bc.perf(gated[live])
        c = float(pos[live].mean())
        fixed_m = bc.perf((c * p)[live])
        ew_m = bc.perf(bench[live])
        ew_gated_m = bc.perf(apply_gate(bench, pos)[live])
        switches = int(np.abs(np.diff(pos[live])).sum())
        rows.append({
            "池": pool_name, "实盘起": str(dates[start].date()), "实盘止": str(dates[-1].date()),
            "标的数": int(close.shape[1]), "S2年化": base_m["年化"], "S2回撤": base_m["回撤"],
            "S2夏普": base_m["夏普"], "S2卡玛": base_m["卡玛"],
            "闸门年化": gate_m["年化"], "闸门回撤": gate_m["回撤"], "闸门夏普": gate_m["夏普"], "闸门卡玛": gate_m["卡玛"],
            "平均仓位": c, "仓位变动次数": switches,
            "固定缩放年化": fixed_m["年化"], "固定缩放卡玛": fixed_m["卡玛"],
            "卡玛差": gate_m["卡玛"] - fixed_m["卡玛"],
            "回撤改善pp": (gate_m["回撤"] - base_m["回撤"]) * 100,
            "等权年化": ew_m["年化"], "等权回撤": ew_m["回撤"],
            "等权套闸门年化": ew_gated_m["年化"], "等权套闸门回撤": ew_gated_m["回撤"],
        })
        print(f"  S2 未套: 年化 {pct(base_m['年化'])} 回撤 {pct(base_m['回撤'])} 卡玛 {base_m['卡玛']:.2f}")
        print(f"  套闸门 : 年化 {pct(gate_m['年化'])} 回撤 {pct(gate_m['回撤'])} 卡玛 {gate_m['卡玛']:.2f}"
              f"（平均仓位 {c:.0%}，变动 {switches} 次）")
        print(f"  固定{c:.0%}仓: 年化 {pct(fixed_m['年化'])} 卡玛 {fixed_m['卡玛']:.2f}"
              f" → 卡玛差 {gate_m['卡玛'] - fixed_m['卡玛']:+.2f}；回撤改善 {(gate_m['回撤']-base_m['回撤'])*100:+.1f}pp")
        print(f"  等权 未套/套闸门: {pct(ew_m['年化'])}/{pct(ew_m['回撤'])} vs "
              f"{pct(ew_gated_m['年化'])}/{pct(ew_gated_m['回撤'])}（描述性，不参与判定）")

        # 敏感性（仅描述）
        for w in MA_SENS:
            pw = gate_positions(bench, w)
            gw = apply_gate(p, pw)
            mw = bc.perf(gw[live])
            cw = float(pw[live].mean())
            fw = bc.perf((cw * p)[live])
            sens_rows.append({"池": pool_name, "均线": w, "年化": mw["年化"], "回撤": mw["回撤"],
                              "夏普": mw["夏普"], "卡玛": mw["卡玛"], "平均仓位": cw,
                              "固定缩放卡玛": fw["卡玛"], "卡玛差": mw["卡玛"] - fw["卡玛"],
                              "回撤改善pp": (mw["回撤"] - base_m["回撤"]) * 100,
                              "参与判定": "是" if w == MA_PRIMARY else "否"})

    df = pd.DataFrame(rows)
    sens = pd.DataFrame(sens_rows)
    ok1 = int((df["卡玛差"] > 0).sum())
    ok2 = int((df["回撤改善pp"] >= 5).sum())
    passed = (ok1 >= 4) and (ok2 >= 4)

    print("\n" + "=" * 96)
    print("主检验（MA120）")
    show = df.assign(**{c: df[c].map(pct) for c in
                        ["S2年化", "S2回撤", "闸门年化", "闸门回撤", "固定缩放年化", "平均仓位",
                         "等权年化", "等权回撤", "等权套闸门年化", "等权套闸门回撤"]})
    show["卡玛差"] = df["卡玛差"].map(lambda x: f"{x:+.2f}")
    show["回撤改善pp"] = df["回撤改善pp"].map(lambda x: f"{x:+.1f}")
    print(show[["池", "S2年化", "S2回撤", "S2卡玛", "闸门年化", "闸门回撤", "闸门夏普", "闸门卡玛",
                "平均仓位", "仓位变动次数", "固定缩放卡玛", "卡玛差", "回撤改善pp",
                "等权年化", "等权回撤", "等权套闸门年化", "等权套闸门回撤"]].to_string(index=False))

    print("\n判据：")
    print(f"  ① 卡玛−固定同仓位 >0 的池数：{ok1}/5（需≥4） {'✓' if ok1 >= 4 else '✗'}")
    print(f"  ② 最大回撤改善 ≥5pp 的池数：{ok2}/5（需≥4） {'✓' if ok2 >= 4 else '✗'}")
    print(f"  判定：{'闸门通过' if passed else '闸门未通过'}")

    print("\n敏感性（描述性，不参与判定）")
    s = sens.assign(年化=sens["年化"].map(pct), 回撤=sens["回撤"].map(pct),
                    平均仓位=sens["平均仓位"].map(pct), 固定缩放卡玛=sens["固定缩放卡玛"].map(lambda x: f"{x:.2f}"))
    s["卡玛"] = sens["卡玛"].map(lambda x: f"{x:.2f}")
    s["卡玛差"] = sens["卡玛差"].map(lambda x: f"{x:+.2f}")
    s["回撤改善pp"] = sens["回撤改善pp"].map(lambda x: f"{x:+.1f}")
    print(s[["池", "均线", "年化", "回撤", "夏普", "卡玛", "平均仓位", "固定缩放卡玛", "卡玛差", "回撤改善pp"]].to_string(index=False))

    df.to_csv("gate_pools.csv", index=False, encoding="utf-8-sig")
    sens.to_csv("gate_sensitivity.csv", index=False, encoding="utf-8-sig")
    with open("gate_meta.json", "w", encoding="utf-8") as fp:
        json.dump({"id": "P1-GATE-01", "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "ma": MA_PRIMARY, "delay": DELAY, "cost": COST, "k": K, "reb": REB,
                   "ok1": ok1, "ok2": ok2, "passed": passed,
                   "pools": df.to_dict(orient="records")}, fp, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
