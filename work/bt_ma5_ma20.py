"""回测：上穿 MA5 加仓 / 下穿 MA20 减仓（2026-11-14）

规则（信号：当日收盘；执行：次日收盘，无前视）：
  * 上穿 MA5   → 仓位加满（1.0）
  * 下穿 MA20  → 减仓（变体决定减到几成）

解读的不确定性 → 一次跑几套口径，让数据说话：
  full    下穿 MA20 清仓（最贴近"减仓=卖出"的读法）
  half    下穿 MA20 减到半仓，之后 MA5 重新上穿补回满仓
  ma60    下穿 MA20 减到半仓，下穿 MA60 才清仓
  cross   经典双均线：MA5 上穿 MA20 买、下穿卖（对照）
  hold20  只在 MA20 上方持有（对照）
  hold5   只在 MA5 上方持有（对照，等价于每天按 MA5 择时）
  bh      买入持有（基准）

数据：data/cache2019（ETF，2019 起，可交易）+ data/cache2007（指数，2007 起，含 08/15/18 三轮熊市）
成本：单边 5bp（与 position/engine.py 默认一致），另给 0bp 毛收益看成本敏感度
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE19 = ROOT / "data" / "cache2019"
CACHE07 = ROOT / "data" / "cache2007"
OUT = ROOT / "runs" / "2026-11-14_ma5_ma20"
OUT.mkdir(parents=True, exist_ok=True)

TD = 252
COST = 0.0005
START_LOOKBACK = 60          # 所有变体统一起点（够 MA60 用）

ETF = {
    "sh510300": "沪深300",
    "sh510500": "中证500",
    "sh510050": "上证50",
    "sz159915": "创业板",
    "sh512100": "中证1000",
    "sh510880": "红利",
    "sz159901": "深证100",
    "sz159949": "创业板50",
    "sh588000": "科创50",
    "sz159920": "恒生",
    "sh513100": "纳指100",
    "sh513500": "标普500",
    "sh518880": "黄金",
    "sh512880": "证券",
    "sh512170": "医疗",
    "sh512690": "酒",
    "sh512480": "半导体",
    "sh515030": "新能源车",
}

IDX = {
    "sh000300": "沪深300(2007)",
    "sh000905": "中证500(2007)",
    "sh000016": "上证50(2007)",
    "sz399006": "创业板指(2007)",
    "sh000015": "上证红利(2007)",
}

MODES = ["full", "gate", "half", "ma60", "cross", "hold20", "hold5", "bh"]


# ---------------------------------------------------------------- 数据
def load_csv(path: Path) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["date"])
    s = df.set_index("date")["close"].astype(float).sort_index()
    return s[~s.index.duplicated(keep="last")]


def load_series(code: str) -> pd.Series:
    if code.startswith("sh000") or code.startswith("sz399"):
        return load_csv(CACHE07 / f"idx_{code}_20070101.csv")
    return load_csv(CACHE19 / f"{code}.csv")


# ---------------------------------------------------------------- 信号
def cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def cross_dn(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def target_weight(close: pd.Series, mode: str, short_n: int = 5, long_n: int = 20) -> pd.Series:
    """返回每日收盘后的目标仓位（0~1），无前视。"""
    ma_s = close.rolling(short_n, min_periods=short_n).mean()
    ma_l = close.rolling(long_n, min_periods=long_n).mean()
    ma60 = close.rolling(60, min_periods=60).mean()

    up = cross_up(close, ma_s).to_numpy()
    dn = cross_dn(close, ma_l).to_numpy()
    dn60 = cross_dn(close, ma60).to_numpy()
    above_l = (close > ma_l).to_numpy()
    above_s = (close > ma_s).to_numpy()
    above60 = (close > ma60).to_numpy()

    w = np.zeros(len(close))
    cur = 0.0
    for i in range(len(close)):
        if mode == "cross":
            # 双均线金叉/死叉
            if i > 0:
                prev = ma_s.iloc[i - 1] <= ma_l.iloc[i - 1]
                now = ma_s.iloc[i] > ma_l.iloc[i]
                if prev and now:
                    cur = 1.0
                elif (not prev) and (not now):
                    cur = 0.0
        elif mode == "hold20":
            cur = 1.0 if above_l[i] else 0.0
        elif mode == "hold5":
            cur = 1.0 if above_s[i] else 0.0
        elif mode == "bh":
            cur = 1.0
        elif mode == "gate":
            # 加仓版本：只在 MA20 上方才认可 MA5 上穿（滤掉下跌中继的反抽）
            if up[i] and above_l[i]:
                cur = 1.0
            elif dn[i]:
                cur = 0.0
        else:
            if up[i]:
                cur = 1.0
            elif dn[i]:
                if mode == "full":
                    cur = 0.0
                elif mode == "half":
                    cur = min(cur, 0.5)
                elif mode == "ma60":
                    cur = 0.5 if above60[i] else 0.0
            if mode == "ma60" and dn60[i]:
                cur = 0.0
        w[i] = cur
    return pd.Series(w, index=close.index)


# ---------------------------------------------------------------- 回测
def run(close: pd.Series, w_target: pd.Series, cost: float = COST) -> dict:
    ret = close.pct_change().fillna(0.0).to_numpy()
    wt = w_target.to_numpy()
    T = len(close)
    w = np.zeros(T)          # 当日实际持有的仓位（前一日收盘决定）
    pnl = np.zeros(T)
    dw = np.zeros(T)
    prev = 0.0
    for t in range(1, T):
        w[t] = wt[t - 1]
        dw[t] = abs(w[t] - prev)
        pnl[t] = w[t] * ret[t] - cost * dw[t]
        prev = w[t]
    eq = (1.0 + pnl).cumprod()
    return {"eq": eq, "w": w, "dw": dw, "pnl": pnl, "ret": ret}


def stats(res: dict, bench_ret: np.ndarray) -> dict:
    eq = res["eq"]
    T = len(eq)
    years = (T - 1) / TD
    ann = eq[-1] ** (1.0 / years) - 1.0
    mdd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    daily = res["pnl"][1:]
    vol = float(np.std(daily, ddof=1) * np.sqrt(TD))
    sharpe = float(np.mean(daily) / np.std(daily, ddof=1) * np.sqrt(TD)) if np.std(daily) > 0 else 0.0
    turn = float(res["dw"].sum() / years)
    trades = int((res["dw"] > 1e-9).sum())
    expo = float(np.mean(res["w"][1:]))
    return {
        "ann": ann, "mdd": mdd, "calmar": ann / abs(mdd) if mdd < 0 else float("nan"),
        "vol": vol, "sharpe": sharpe, "turnover": turn, "trades": trades,
        "expo": expo, "final": float(eq[-1]),
        "years": years,
    }


def yearly(eq: pd.Series, idx: pd.DatetimeIndex) -> dict:
    s = pd.Series(eq, index=idx)
    out = {}
    for y, g in s.groupby(s.index.year):
        base = 1.0 if y == s.index.year.min() else float(s[s.index.year < y].iloc[-1])
        out[int(y)] = float(g.iloc[-1] / base - 1.0)
    return out


# ---------------------------------------------------------------- 主流程
def analyze(code: str, name: str) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    close = load_series(code).dropna()
    close = close.iloc[START_LOOKBACK:]          # 统一去掉均线预热段
    close = close / close.iloc[0]
    idx = close.index
    bench = close.pct_change().fillna(0.0).to_numpy()

    rows = []
    detail = {}
    for mode in MODES:
        wt = target_weight(close, mode)
        res = run(close, wt, COST)
        st = stats(res, bench)
        st.update({"code": code, "name": name, "mode": mode})
        rows.append(st)
        detail[mode] = (res, wt)

    # 成本敏感度：主口径 full 在 0bp / 10bp
    for c, tag in ((0.0, "full_cost0"), (0.001, "full_cost10bp")):
        res = run(close, detail["full"][1], c)
        st = stats(res, bench)
        st.update({"code": code, "name": name, "mode": tag})
        rows.append(st)
    return pd.DataFrame(rows), detail, close.to_frame("close")


def main():
    rows = []
    yearly_tables = {}
    curves = {}
    all_trades = []

    todo = [(c, ETF[c]) for c in ETF] + [(c, IDX[c]) for c in IDX]
    for code, name in todo:
        try:
            df, detail, close = analyze(code, name)
        except Exception as e:  # 数据缺失不中断
            print(f"[skip] {code}: {e!r}")
            continue
        rows.append(df)
        res_full, wt_full = detail["full"]
        eq = res_full["eq"]
        curves[f"{code} {name}"] = pd.Series(eq, index=close.index)
        if code in ("sh510300", "sh000300", "sh510500", "sh000905", "sz159915", "sz399006"):
            yearly_tables[f"{code} {name}"] = {
                m: yearly(detail[m][0]["eq"], close.index) for m in ("full", "gate", "half", "ma60", "hold20", "bh")
            }
        # 主口径交易明细（只在基准标的落盘）
        if code in ("sh510300", "sh000300"):
            w = wt_full.to_numpy()
            chg = np.diff(np.concatenate([[0.0], w]))
            tr = []
            for i in np.where(np.abs(chg) > 1e-9)[0]:
                tr.append({
                    "date": str(close.index[i].date()),
                    "side": "buy" if chg[i] > 0 else "sell",
                    "price": float(close["close"].iloc[i]),
                })
            all_trades.append(pd.DataFrame(tr).assign(code=code))
        print(f"[ok] {code} {name}: full ann={df.set_index('mode').loc['full','ann']:.2%} "
              f"mdd={df.set_index('mode').loc['full','mdd']:.2%}")

    summary = pd.concat(rows, ignore_index=True)
    summary.to_csv(OUT / "summary_all.csv", index=False, encoding="utf-8-sig")

    # 参数扫描：MA 短 / 长，full 口径，看方向是否稳
    sweep = []
    for code, name in todo:
        try:
            close = load_series(code).dropna().iloc[START_LOOKBACK:]
        except Exception:
            continue
        close = close / close.iloc[0]
        for sn in (3, 5, 8, 10):
            for ln in (10, 15, 20, 30, 60):
                if sn >= ln:
                    continue
                res = run(close, target_weight(close, "full", sn, ln), COST)
                st = stats(res, close.pct_change().fillna(0.0).to_numpy())
                sweep.append({"code": code, "name": name, "short": sn, "long": ln,
                              "ann": st["ann"], "mdd": st["mdd"], "calmar": st["calmar"],
                              "trades": st["trades"]})
    sweep = pd.DataFrame(sweep)
    sweep.to_csv(OUT / "sweep.csv", index=False, encoding="utf-8-sig")

    # 汇总：各口径横截面中位数
    piv = summary.pivot_table(index="mode", values=["ann", "mdd", "calmar", "turnover"], aggfunc="median")
    piv = piv.reindex([m for m in summary["mode"].unique()])
    piv.to_csv(OUT / "summary_median.csv", encoding="utf-8-sig")

    # 相对买入持有的超额
    bh = summary[summary["mode"] == "bh"].set_index("code")["ann"]
    beat = {}
    for m in ("full", "gate", "half", "ma60", "cross", "hold20", "hold5"):
        sub = summary[summary["mode"] == m].set_index("code")
        diff = (sub["ann"] - bh.reindex(sub.index)).dropna()
        beat[m] = {"n": int(len(diff)), "beat_ratio": float((diff > 0).mean()),
                   "median_excess": float(diff.median())}
    (OUT / "beat_bh.json").write_text(json.dumps(beat, ensure_ascii=False, indent=2), encoding="utf-8")

    # 净值曲线 CSV（各标的 full 口径 + bh）
    eqs = pd.DataFrame(curves)
    eqs.to_csv(OUT / "equity_full.csv", encoding="utf-8-sig")
    if all_trades:
        pd.concat(all_trades).to_csv(OUT / "trades_main.csv", index=False, encoding="utf-8-sig")
    with open(OUT / "yearly.json", "w", encoding="utf-8") as f:
        json.dump(yearly_tables, f, ensure_ascii=False, indent=2)

    # 交易诊断：MA5 上穿里有几次是在 MA20 下方（下跌中继假信号）
    diag_rows = []
    for code, name in todo:
        try:
            close = load_series(code).dropna().iloc[START_LOOKBACK:]
        except Exception:
            continue
        close = close / close.iloc[0]
        ma5 = close.rolling(5, min_periods=5).mean()
        ma20 = close.rolling(20, min_periods=20).mean()
        up = cross_up(close, ma5)
        below = (close < ma20)
        n_up = int(up.sum())
        n_up_below = int((up & below).sum())
        w = target_weight(close, "full").to_numpy()
        ret = close.pct_change().fillna(0.0).to_numpy()
        # 一轮完整交易：从仓位 0→1 到 1→0
        trips, entry_i = [], None
        for i in range(len(w)):
            if w[i] > 0 and entry_i is None:
                entry_i = i
            elif w[i] == 0 and entry_i is not None:
                trips.append((entry_i, i))
                entry_i = None
        if entry_i is not None:
            trips.append((entry_i, len(w) - 1))
        holds = [(b - a) for a, b in trips]
        wins = 0
        for a, b in trips:
            seg = 1.0
            for i in range(max(a, 1), b + 1):
                seg *= 1.0 + w[i] * ret[i]
            if seg > 1.0:
                wins += 1
        diag_rows.append({
            "code": code, "name": name, "years": round(len(close) / TD, 2),
            "ma5_upcross": n_up, "upcross_per_year": n_up / (len(close) / TD),
            "upcross_below_ma20": n_up_below,
            "below_share": n_up_below / n_up if n_up else np.nan,
            "trips": len(trips), "trips_per_year": len(trips) / (len(close) / TD),
            "avg_hold_days": float(np.mean(holds)) if holds else np.nan,
            "win_rate": wins / len(trips) if trips else np.nan,
        })
    pd.DataFrame(diag_rows).to_csv(OUT / "diag_trades.csv", index=False, encoding="utf-8-sig")

    # ---- 出图（英文标签，避免中文字体缺失）
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(2, 1, figsize=(11, 8), gridspec_kw={"height_ratios": [2, 1]})
        for code, lab in (("sh510300", "HS300 ETF"), ("sh000300", "HS300 idx 2007"),
                          ("sh510500", "CSI500 ETF"), ("sz159915", "ChiNext ETF")):
            key = [k for k in curves if k.startswith(code)]
            if key:
                s = curves[key[0]]
                axes[0].plot(s.index, s.values, lw=1.2, label=f"{lab} MA5/MA20")
                axes[0].plot(s.index, s.cummax().values * 0, alpha=0)
        axes[0].set_yscale("log")
        axes[0].legend(fontsize=8)
        axes[0].set_title("Equity: buy on MA5 up-cross, cut on MA20 down-cross (5bp/side)")
        axes[0].grid(alpha=0.3)

        # 510300 回撤对比
        close300 = load_series("sh510300").dropna().iloc[START_LOOKBACK:]
        close300 = close300 / close300.iloc[0]
        for mode, lab in (("full", "MA5 in / MA20 out"), ("gate", "MA5 in (only above MA20)"),
                          ("hold20", "hold above MA20"), ("bh", "buy&hold")):
            res = run(close300, target_weight(close300, mode), COST)
            eq = res["eq"]
            dd = eq / np.maximum.accumulate(eq) - 1.0
            axes[1].plot(close300.index, dd, lw=1.0, label=lab)
        axes[1].legend(fontsize=8)
        axes[1].set_title("Drawdown - 510300 (2019-04 to 2026-11)")
        axes[1].grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(OUT / "ma5_ma20.png", dpi=130)
    except Exception as e:
        print(f"[plot failed] {e!r}")

    print("\n=== median across assets ===")
    print(piv.to_string(float_format=lambda x: f"{x:.3f}"))
    print("\n=== beat buy&hold ===")
    print(json.dumps(beat, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
