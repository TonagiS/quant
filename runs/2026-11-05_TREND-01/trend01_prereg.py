"""TREND-01 预登记检验：趋势转换信号（V-A / V-B）。

纪律：只读复用 indicators/code 下的引擎；先登记（registry）再跑；不碰 decision_1430、shadow v1/v2。
信号只用 t 日及以前数据：V-A 用 MA60 与 5 日前 MA60；V-B 用前 40 日最高/最低收盘。
事件研究：同标的同类事件 20 日内只留第一个；超额 = 事件后 h 日收益 - 当日同池均值。
增量：把 S2 的 MA60 过滤换成“底部后允许、顶部后清仓”的状态机，与 S2、S2+MA120 闸门对照。
"""
import json
import os
import sys
import datetime

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CODE = os.path.join(ROOT, "indicators", "code")
sys.path.insert(0, CODE)
sys.path.insert(0, ROOT)

import backtest_composite as bc  # noqa: E402
from macd_structure import ETF_POOL  # noqa: E402
from pools import get_pool, load_one  # noqa: E402

CACHE = os.path.join(ROOT, "data", "cache2019")
START = "20190101"
MIN_NAMES = 8
K, REB, DELAY = 5, 5, 1
COST = 0.0005
GRID = [0.0005, 0.001, 0.002, 0.003, 0.004, 0.006]
SEG = [("2019-2022", "2019-01-01", "2022-12-31"), ("2023-2026", "2023-01-01", "2026-12-31")]
S2 = dict(mom=1, trend=True, invvol=True)


# ---------------- 信号 ----------------
def signals(close, variant):
    ma60 = close.rolling(60, min_periods=60).mean()
    if variant == "A":
        top = (close < ma60) & (ma60 < ma60.shift(5))
        bot = (close > ma60) & (ma60 > ma60.shift(5))
    else:
        lo = close.rolling(40, min_periods=40).min().shift(1)
        hi = close.rolling(40, min_periods=40).max().shift(1)
        top = close < lo
        bot = close > hi
    return top.fillna(False).to_numpy(), bot.fillna(False).to_numpy()


def state_series(bot, top):
    st = np.zeros(len(bot), dtype=np.int8)
    on = 0
    for i in range(len(bot)):
        if bot[i]:
            on = 1
        elif top[i]:
            on = 0
        st[i] = on
    return st


def test_no_lookahead(close):
    """截断未来数据后，t 日信号/状态必须不变。"""
    bad = 0
    checked = 0
    for variant in ("A", "B"):
        top, bot = signals(close, variant)
        st = np.column_stack([state_series(bot[:, j], top[:, j]) for j in range(close.shape[1])])
        for j in range(0, close.shape[1], 7):
            col = close.iloc[:, j]
            for t in range(120, len(close) - 2, 137):
                sub = close.iloc[: t + 1].copy()
                t2, b2 = signals(sub, variant)
                s2 = state_series(b2[:, j], t2[:, j])
                checked += 1
                if bool(t2[-1, j]) != bool(top[t, j]) or bool(b2[-1, j]) != bool(bot[t, j]) or int(s2[-1]) != int(st[t, j]):
                    bad += 1
    return {"checked": checked, "mismatch": bad, "passed": bad == 0}


# ---------------- 事件研究 ----------------
def event_days(mask_col, gap=20):
    idx = np.flatnonzero(mask_col)
    out, last = [], -10 ** 9
    for t in idx:
        if t - last >= gap:
            out.append(t)
            last = t
    return out


def event_study(close, valid, variant, dates):
    top, bot = signals(close, variant)
    T, N = close.shape
    close_np = close.to_numpy()
    rows = []
    for side, mask, sign in (("top", top, -1), ("bottom", bot, 1)):
        for j, code in enumerate(close.columns):
            for t in event_days(mask[:, j]):
                rows.append({"side": side, "code": code, "t": t, "sign": sign})
    ev = pd.DataFrame(rows)
    if ev.empty:
        return pd.DataFrame(), pd.DataFrame()
    out = []
    for h in (5, 10, 20):
        fwd = np.full((T, N), np.nan)
        fwd[:-h] = close_np[h:] / close_np[:-h] - 1
        cnt = np.where(valid, np.isfinite(fwd), False).sum(1)
        ssum = np.where(valid & np.isfinite(fwd), fwd, 0).sum(1)
        bench = np.full(T, np.nan)
        ok = cnt >= 3
        bench[ok] = ssum[ok] / cnt[ok]
        for side in ("top", "bottom"):
            sub = ev[ev["side"] == side].copy()
            if sub.empty:
                continue
            r = fwd[sub["t"].to_numpy(), [close.columns.get_loc(c) for c in sub["code"]]]
            b = bench[sub["t"].to_numpy()]
            exc = r - b
            good = np.isfinite(exc)
            sub, exc = sub[good], exc[good]
            if len(sub) < 10:
                continue
            by_day = pd.DataFrame({"t": sub["t"].to_numpy(), "exc": exc}).groupby("t")["exc"].mean()
            idx = by_day.index.to_numpy()
            x = by_day.to_numpy()
            se = bc.nw_se(idx, x, T, h)
            t_nw = x.mean() / se if se > 0 else np.nan
            th = bc.thin(idx, max(h, 20))
            full = pd.Series(np.nan, index=range(T))
            full.loc[idx] = x
            xth = full.loc[th].dropna().to_numpy()
            t_no = xth.mean() / (xth.std(ddof=1) / np.sqrt(len(xth))) if len(xth) >= 8 else np.nan
            # 回吐幅度（信号日相对前 60 日高/低）
            c = close_np[:, [close.columns.get_loc(cc) for cc in sub["code"]]]
            tt = sub["t"].to_numpy()
            hh = np.array([np.nanmax(close_np[max(0, t - 60):t + 1, j]) for t, j in zip(tt, [close.columns.get_loc(cc) for cc in sub["code"]])])
            ll = np.array([np.nanmin(close_np[max(0, t - 60):t + 1, j]) for t, j in zip(tt, [close.columns.get_loc(cc) for cc in sub["code"]])])
            px = np.array([close_np[t, j] for t, j in zip(tt, [close.columns.get_loc(cc) for cc in sub["code"]])])
            retr = px / hh - 1 if side == "top" else px / ll - 1
            out.append({"侧": side, "h": h, "事件数": len(sub), "事件日数": len(idx),
                        "超额均值%": exc.mean() * 100, "NW_t": t_nw, "不重叠n": len(xth),
                        "不重叠t": t_no, "回吐中位数%": np.nanmedian(retr) * 100,
                        "方向符合": bool(t_nw * sub["sign"].iloc[0] > 0)})
    return ev, pd.DataFrame(out)


# ---------------- 组合 ----------------
def prep(f):
    valid = f["valid"]
    rk = {k: bc.xs_rank(f[k], valid, MIN_NAMES) for k in ("mom20", "mom60", "mom120")}
    rm = (rk["mom20"] + rk["mom60"] + rk["mom120"]) / 3
    enough = rm.notna().any(axis=1).to_numpy()
    start = int(np.argmax(enough))
    bench = f["ret"].where(valid).mean(axis=1).fillna(0).to_numpy().copy()
    bench[:start] = 0.0
    return rm, start, bench


def run(close, f, rm, start, cand, cost=COST, delay=DELAY):
    ret = f["ret"].to_numpy()
    vol = f["vol20"].to_numpy()
    rm_fill = np.nan_to_num((rm - 0.5).to_numpy(), nan=0.0)
    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_up, lim_dn = ret >= lim * 0.98, ret <= -lim * 0.98
    p, turn = bc.run_portfolio(ret, rm_fill, cand, vol, lim_up, lim_dn, K, REB, cost, delay,
                               start, invvol=True)
    return p, turn


def gate_returns(p, bench, start, delay=1, cost=COST, window=120):
    ew = bench.copy()
    lvl = np.cumprod(1.0 + ew)
    sma = pd.Series(lvl).rolling(window, min_periods=window).mean().to_numpy()
    sig = np.where(np.isnan(sma), 1.0, np.where(lvl < sma, 0.0, 1.0))
    sh = delay + 1
    pos = np.ones_like(sig)
    pos[sh:] = sig[:-sh]
    dpos = np.abs(np.diff(pos, prepend=pos[0]))
    return pos * p - cost * dpos, pos


def breakeven(grid, excess):
    for i in range(1, len(grid)):
        if excess[i] <= 0 < excess[i - 1]:
            return grid[i - 1] + excess[i - 1] / (excess[i - 1] - excess[i]) * (grid[i] - grid[i - 1])
    return None


def calmar(p, live):
    m = bc.perf(p[live])
    return m, (m["卡玛"] if np.isfinite(m["卡玛"]) else np.nan)


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("TREND-01 预登记检验：V-A(MA60方向) / V-B(40日突破)")
    out_summary = []
    detail = {}
    for pool in ("base", "ext"):
        print("\n" + "=" * 92)
        print(f"池 {pool}")
        data = {}
        failed = []
        for code, (name, kind) in get_pool(pool, ETF_POOL).items():
            try:
                df, _ = load_one(code, kind, START, CACHE)
                data[code] = df
            except Exception as e:
                failed.append((name, code, str(e)[:80]))
        close, amt = bc.build_panel(data)
        f = bc.build_features(close, amt, 120)
        rm, start, bench = prep(f)
        dates = close.index
        live = np.arange(len(dates)) >= start
        print(f"  面板 {close.shape[1]} 只，失败 {len(failed)}，实盘 {dates[start].date()} ~ {dates[-1].date()}")

        # 前视测试
        la = test_no_lookahead(close)
        print(f"  前视测试：截断重算 {la['checked']} 次，不一致 {la['mismatch']} -> {'通过' if la['passed'] else '失败'}")

        m_s2, cal_s2 = calmar(run(close, f, rm, start, (f["valid"] & rm.notna()).to_numpy() & (close > f["ma60"]).to_numpy())[0], live)
        p_s2_2, _ = run(close, f, rm, start, (f["valid"] & rm.notna()).to_numpy() & (close > f["ma60"]).to_numpy(), delay=2)
        cal_s2_2 = bc.perf(p_s2_2[live])["卡玛"]
        p_gate, _ = gate_returns(run(close, f, rm, start, (f["valid"] & rm.notna()).to_numpy() & (close > f["ma60"]).to_numpy())[0], bench, start)
        m_gate = bc.perf(p_gate[live])
        print(f"  S2 对照：年化 {m_s2['年化']*100:.2f}% 回撤 {m_s2['回撤']*100:.2f}% 卡玛 {cal_s2:.3f}")
        print(f"  S2+MA120闸门：年化 {m_gate['年化']*100:.2f}% 回撤 {m_gate['回撤']*100:.2f}% 卡玛 {m_gate['卡玛']:.3f}")

        pool_rows = []
        for variant in ("A", "B"):
            top, bot = signals(close, variant)
            st = np.column_stack([state_series(bot[:, j], top[:, j]) for j in range(close.shape[1])])
            ev, ev_tab = event_study(close, f["valid"].to_numpy(), variant, dates)
            ev_tab["池"] = pool
            ev_tab["变体"] = f"V-{variant}"
            if not ev_tab.empty:
                print(f"\n  [{variant}] 事件研究")
                print(ev_tab[["侧", "h", "事件数", "NW_t", "不重叠t", "超额均值%", "回吐中位数%", "方向符合"]].to_string(index=False))
                ev_tab.to_csv(f"trend01_events_{pool}_{variant}.csv", index=False, encoding="utf-8-sig")
            c1_row = ev_tab[(ev_tab["侧"] == "top") & (ev_tab["h"] == 20)]
            c1_row2 = ev_tab[(ev_tab["侧"] == "bottom") & (ev_tab["h"] == 20)]
            c1 = False
            if len(c1_row) and len(c1_row2):
                c1 = (abs(float(c1_row["NW_t"].iloc[0])) >= 3 and bool(c1_row["方向符合"].iloc[0])
                      and abs(float(c1_row2["NW_t"].iloc[0])) >= 3 and bool(c1_row2["方向符合"].iloc[0]))

            cand = (f["valid"] & rm.notna()).to_numpy() & (st == 1)
            p1, turn1 = run(close, f, rm, start, cand)
            m1 = bc.perf(p1[live])
            turn_ann = turn1 * (252 / REB)
            exc = []
            for c in GRID:
                pc, _ = run(close, f, rm, start, cand, cost=c)
                exc.append(bc.perf(pc[live])["年化"] - bc.perf(bench[live])["年化"])
            be = breakeven(GRID, np.array(exc))
            p2, _ = run(close, f, rm, start, cand, delay=2)
            cal2 = bc.perf(p2[live])["卡玛"]
            segs = []
            for name, a, b in SEG:
                msk = live & (dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b))
                if msk.sum() < 250:
                    segs.append((name, np.nan, np.nan, np.nan))
                    continue
                mv = bc.perf(p1[msk])["卡玛"]
                ms = bc.perf(run(close, f, rm, start, (f["valid"] & rm.notna()).to_numpy() & (close > f["ma60"]).to_numpy())[0][msk])["卡玛"]
                segs.append((name, mv, ms, mv - ms))
            s2_turn = run(close, f, rm, start, (f["valid"] & rm.notna()).to_numpy() & (close > f["ma60"]).to_numpy())[1] * (252 / REB)
            c2 = (m1["卡玛"] >= cal_s2 + 0.05) and (m1["回撤"] >= m_s2["回撤"] - 0.02)
            c3 = (be is not None and be >= 0.003) and (turn_ann <= 1.5 * s2_turn)
            c4 = (m1["卡玛"] >= cal_s2) and (cal2 >= cal_s2_2)
            signs = [np.sign(s[3]) for s in segs if np.isfinite(s[3]) and s[3] != 0]
            c5 = len(signs) == 2 and signs[0] == signs[1]
            passed = bool(c1 and c2 and c3 and c4 and c5)
            pool_rows.append({"池": pool, "变体": f"V-{variant}", "C1_事件|t|>=3": bool(c1),
                              "C2_卡玛+0.05且回撤": bool(c2), "C3_盈亏≥30bp且换手≤1.5x": bool(c3),
                              "C4_delay1/2均不低": bool(c4), "C5_两段同号": bool(c5),
                              "卡玛": round(m1["卡玛"], 3), "S2卡玛": round(cal_s2, 3),
                              "年化%": round(m1["年化"] * 100, 2), "回撤%": round(m1["回撤"] * 100, 2),
                              "盈亏bp": None if be is None else round(be * 10000),
                              "换手%": round(turn_ann * 100), "S2换手%": round(s2_turn * 100),
                              "delay2卡玛": round(cal2, 3), "S2delay2卡玛": round(cal_s2_2, 3),
                              "分段1差": round(segs[0][3], 3) if np.isfinite(segs[0][3]) else None,
                              "分段2差": round(segs[1][3], 3) if np.isfinite(segs[1][3]) else None,
                              "通过": passed})
            print(f"\n  [{variant}] 组合：年化 {m1['年化']*100:.2f}% 回撤 {m1['回撤']*100:.2f}% 卡玛 {m1['卡玛']:.3f}"
                  f" | 盈亏平衡 {None if be is None else round(be*10000)}bp | 换手 {turn_ann*100:.0f}% (S2 {s2_turn*100:.0f}%)"
                  f" | delay2 {cal2:.3f} (S2 {cal_s2_2:.3f})")
            print(f"      C1 {c1} C2 {c2} C3 {c3} C4 {c4} C5 {c5} -> {'通过' if passed else '未通过'}"
                  f" | 分段差 {segs[0][3]:+.3f}/{segs[1][3]:+.3f}")
        out_summary.extend(pool_rows)
        detail[pool] = {"lookahead": la, "rows": pool_rows}
        pd.DataFrame(pool_rows).to_csv(f"trend01_summary_{pool}.csv", index=False, encoding="utf-8-sig")

    s = pd.DataFrame(out_summary)
    s.to_csv("trend01_summary.csv", index=False, encoding="utf-8-sig")
    # 选择：两池都过；都过取 V-A
    chosen = None
    for v in ("V-A", "V-B"):
        sub = s[s["变体"] == v]
        if len(sub) == 2 and bool(sub["通过"].all()):
            chosen = v
            break
    meta = {"id": "TREND-01", "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "snapshot": "2026-11-02", "chosen": chosen, "passed": chosen is not None, "detail": detail}
    with open("trend01_meta.json", "w", encoding="utf-8") as fp:
        json.dump(meta, fp, indent=2, ensure_ascii=False, default=float)
    print("\n" + "=" * 92)
    print(s.to_string(index=False))
    print(f"\n选择结果：{chosen if chosen else '两变体均未通过'}")


if __name__ == "__main__":
    main()
