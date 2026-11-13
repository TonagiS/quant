"""单支 harness：90 标的 × N 规则，一次跑完，输出长表。

用途：以后加一个单支假设 = 往 RULES 加一行 + 重跑 20 秒。

口径（与 single_asset_rules.py / layered_pipeline.py 一致）
- 信号 t 日收盘算、t+1 生效（shift 1），单边 5bp，敞口 ∈ [0,1]，不加杠杆
- 对照 ①满仓 ②同平均仓位的固定仓位（无成本，剔除"降仓=降波动"的机械效应）
- 分两段看稳定：前半 / 后半

产出 runs/2026-11-15_single_asset_grid/grid.csv
列：group,name,rule,n,cagr,mdd,calmar,sharpe,avg_pos,turnover,
    d_calmar,d_cagr,dc1,dc2,d_calmar_fixed,cagr_fixed,calmar_fixed
"""
from __future__ import annotations

import glob
import sys
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OHLC = DATA / "cache_ohlc"
OUT = Path(os.environ.get("SA_OUT", str(ROOT / "runs" / "2026-11-15_single_asset_grid")))
OUT.mkdir(parents=True, exist_ok=True)
TD = 252
COST = 0.0005
import os  # noqa: E402  （放在后面无所谓，模块导入期就执行）


# ---------------- 数据 ----------------
# 可用环境变量切换数据集/切分（长样本模式）：
#   SA_OHLC_DIR      数据目录（默认 cache_ohlc；长池用 cache_ohlc_long）
#   SA_START_ETF     ETF 起点（默认 2019-01-01；长池 2014-01-01）
#   SA_START_LC      长样本起点（默认 2007-01-01）
#   SA_TRAIN_END_ETF ETF 训练截止（默认 2022-12-31；长池 2019-12-31）
#   SA_TRAIN_END_LC  长样本训练截止（默认 2015-12-31）
OHLC = Path(os.environ.get("SA_OHLC_DIR", str(ROOT / "data" / "cache_ohlc")))
START_MAP = {"ETF": os.environ.get("SA_START_ETF", "2019-01-01"),
             "指数": os.environ.get("SA_START_LC", "2007-01-01"),
             "蓝筹": os.environ.get("SA_START_LC", "2007-01-01")}
SPLIT = {"ETF": START_MAP["ETF"], "长样本": START_MAP["指数"]}
TRAIN_END = {"ETF": os.environ.get("SA_TRAIN_END_ETF", "2022-12-31"),
             "长样本": os.environ.get("SA_TRAIN_END_LC", "2015-12-31")}


def load_universe(ohlc_dir=None):
    """返回 {code: (group, df[date,open,high,low,close,volume,amount])}"""
    root = Path(ohlc_dir) if ohlc_dir else OHLC
    uni = {}
    meta = root / "_meta.csv"
    kinds = {}
    if meta.exists():
        m = pd.read_csv(meta)
        kinds = dict(zip(m["code"], m["kind"]))
    for p in sorted(root.glob("*.csv")):
        if p.stem.startswith("_"):
            continue
        code = p.stem
        df = pd.read_csv(p, parse_dates=["date"])
        kind = kinds.get(code, "etf")
        grp = {"etf": "ETF", "index": "指数", "stock": "蓝筹"}.get(kind, "ETF")
        df = df[df["date"] >= START_MAP[grp]]
        if len(df) < 250:
            continue
        uni[code] = (grp, df.sort_values("date").reset_index(drop=True))
    return uni


def factors(df: pd.DataFrame) -> dict:
    if "date" in df.columns:
        df = df.set_index("date")
    c, o, h, l = df["close"], df["open"], df["high"], df["low"]
    v, a = df["volume"], df["amount"]
    F = {"close": c, "ret": c.pct_change(), "amount": a}
    for n in (5, 10, 20, 30, 60, 120, 200):
        F[f"ma{n}"] = c.rolling(n, min_periods=n).mean()
    for n in (20, 60, 120):
        F[f"mom{n}"] = c / c.shift(n) - 1
    for n in (20, 60):
        F[f"vol{n}"] = F["ret"].rolling(n, min_periods=int(n * 0.7)).std() * np.sqrt(TD)
    # 波动分位（252 日窗口内，不含当日）
    v60 = F["vol60"]
    F["volpct"] = v60.rolling(252, min_periods=120).apply(lambda x: (x[:-1] < x[-1]).mean(), raw=True)
    # 回撤（相对历史最高收盘）
    F["dd"] = c / c.cummax() - 1
    # 成交额比 / 量新高
    F["amtratio"] = a.rolling(5).mean() / a.rolling(60).mean()
    F["hh20"] = c.rolling(20).max().shift(1)
    F["amt_hh20"] = a.rolling(20).max().shift(1)
    # 唐奇安
    F["dn20"] = l.rolling(20).min().shift(1)
    F["dn55"] = l.rolling(55).min().shift(1)
    F["hi20"] = h.rolling(20).max().shift(1)
    F["hi55"] = h.rolling(55).max().shift(1)
    # 布林
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    F["bb_mid"], F["bb_up"], F["bb_dn"] = mid, mid + 2 * sd, mid - 2 * sd
    # ATR
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    F["atr14"] = tr.rolling(14).mean()
    F["atr_pct"] = F["atr14"] / c
    F["hi20c"] = c.rolling(20).max().shift(1)
    # 周线 / 月线
    w = c.resample("W-FRI").last().dropna()
    F["w_close"], F["w_ma20"] = w, w.rolling(20).mean()
    F["w_slope"] = F["w_ma20"].pct_change(4)
    m = c.resample("ME").last().dropna()
    F["m_close"], F["m_ma6"] = m, m.rolling(6).mean()
    F["m_slope"] = F["m_ma6"].pct_change(2)
    return F


def to_daily(s: pd.Series, idx) -> pd.Series:
    return s.reindex(idx, method="ffill")


# ---------------- 规则工具 ----------------
def hyst(reduce: pd.Series, recover: pd.Series, level=0.0, init=1.0) -> pd.Series:
    """reduce 触发降到 level，recover 触发回满仓，其余保持。"""
    s = pd.Series(np.nan, index=reduce.index)
    s[reduce.fillna(False)] = level
    s[recover.fillna(False)] = 1.0
    s = s.ffill()
    return s.fillna(init)


def below_ma(F, n, level=0.0, buf=0.0) -> pd.Series:
    c, ma = F["close"], F[f"ma{n}"]
    return hyst(c < ma * (1 - buf), c > ma * (1 + buf), level)


def vt(F, target, reb=5) -> pd.Series:
    raw = (target / F["vol60"]).clip(0, 1)
    mask = np.arange(len(raw)) % reb == 0
    return raw.where(mask).ffill()


# ---------------- 规则表 ----------------
def build_rules():
    R = {}
    for n in (20, 30, 60, 120, 200):
        R[f"A_MA{n}半仓"] = lambda F, n=n: below_ma(F, n, 0.5)
        R[f"A_MA{n}清仓"] = lambda F, n=n: below_ma(F, n, 0.0)
    for n in (60, 120, 200):
        R[f"A_MA{n}清仓_b2"] = lambda F, n=n: below_ma(F, n, 0.0, 0.02)
    for n in (20, 60, 120):
        R[f"B_mom{n}半仓"] = lambda F, n=n: hyst(F[f"mom{n}"] < 0, F[f"mom{n}"] > 0.01, 0.5)
        R[f"B_mom{n}清仓"] = lambda F, n=n: hyst(F[f"mom{n}"] < 0, F[f"mom{n}"] > 0.01, 0.0)
    for t in (0.08, 0.10, 0.12, 0.15, 0.20):
        R[f"C_VT{int(t*100)}"] = lambda F, t=t: vt(F, t)
    R["C_volpct80半仓"] = lambda F: hyst(F["volpct"] > 0.80, F["volpct"] < 0.60, 0.5)
    R["C_volpct90清仓"] = lambda F: hyst(F["volpct"] > 0.90, F["volpct"] < 0.70, 0.0)
    for th in (0.10, 0.15, 0.20, 0.25):
        R[f"D_dd{int(th*100)}半仓"] = lambda F, th=th: hyst(F["dd"] < -th, F["dd"] > -(th - 0.05), 0.5)
        R[f"D_dd{int(th*100)}清仓"] = lambda F, th=th: hyst(F["dd"] < -th, F["dd"] > -(th - 0.05), 0.0)
    R["E_额比07半仓"] = lambda F: hyst(F["amtratio"] < 0.70, F["amtratio"] > 0.90, 0.5)
    R["E_额比05清仓"] = lambda F: hyst(F["amtratio"] < 0.50, F["amtratio"] > 0.80, 0.0)
    R["E_价额双新高"] = lambda F: ((F["close"] > F["hi20"]) & (F["amount"] > F["amt_hh20"])).astype(float).ffill()
    R["F_周MA20半仓"] = lambda F: hyst(to_daily(F["w_close"], F["close"].index) <
                                       to_daily(F["w_ma20"], F["close"].index),
                                       to_daily(F["w_close"], F["close"].index) > to_daily(F["w_ma20"], F["close"].index), 0.5)
    R["F_周MA20清仓"] = lambda F: hyst(to_daily(F["w_close"], F["close"].index) <
                                       to_daily(F["w_ma20"], F["close"].index),
                                       to_daily(F["w_close"], F["close"].index) > to_daily(F["w_ma20"], F["close"].index), 0.0)
    R["F_周MA20转下半仓"] = lambda F: hyst(to_daily(F["w_slope"], F["close"].index) < 0,
                                         to_daily(F["w_slope"], F["close"].index) > 0.01, 0.5)
    R["F_月MA6半仓"] = lambda F: hyst(to_daily(F["m_close"], F["close"].index) <
                                     to_daily(F["m_ma6"], F["close"].index),
                                     to_daily(F["m_close"], F["close"].index) > to_daily(F["m_ma6"], F["close"].index), 0.5)
    R["G_布林中轨半仓"] = lambda F: hyst(F["close"] < F["bb_mid"], F["close"] > F["bb_mid"], 0.5)
    R["G_唐奇安20清仓"] = lambda F: hyst(F["close"] < F["dn20"], F["close"] > F["hi20"], 0.0)
    R["G_唐奇安55清仓"] = lambda F: hyst(F["close"] < F["dn55"], F["close"] > F["hi55"], 0.0)
    R["H_ATR吊灯3x清仓"] = lambda F: hyst(F["close"] < F["hi20c"] - 3 * F["atr14"],
                                         F["close"] > F["hi20c"] - 1.5 * F["atr14"], 0.0)
    R["H_ATR吊灯2x半仓"] = lambda F: hyst(F["close"] < F["hi20c"] - 2 * F["atr14"],
                                         F["close"] > F["hi20c"] - 1.0 * F["atr14"], 0.5)
    R["H_高波动日清仓"] = lambda F: hyst((F["ret"] < -2.5 * F["atr_pct"]) & (F["close"] < F["ma20"]),
                                        F["close"] > F["ma20"], 0.0)
    return R


COMBOS = {
    # 两两串联（取 min）：名字 -> (mode, 基础规则名列表)
    "X_MA60零×VT12": ("min", ["A_MA60清仓", "C_VT12"]),
    "X_MA60零×dd15": ("min", ["A_MA60清仓", "D_dd15半仓"]),
    "X_MA30零×dd20": ("min", ["A_MA30清仓", "D_dd20半仓"]),
    "X_mom60零×dd15": ("min", ["B_mom60清仓", "D_dd15半仓"]),
    "X_volpct80×dd15": ("min", ["C_volpct80半仓", "D_dd15半仓"]),
    "X_周MA20×MA30零": ("min", ["F_周MA20半仓", "A_MA30清仓"]),
    "X_周MA20×VT12": ("min", ["F_周MA20半仓", "C_VT12"]),
    "X_唐奇安55×VT15": ("min", ["G_唐奇安55清仓", "C_VT15"]),
    "X_ATR吊灯×MA60零": ("min", ["H_ATR吊灯3x清仓", "A_MA60清仓"]),
    "X_月MA6×MA120零": ("min", ["F_月MA6半仓", "A_MA120清仓"]),
    # 投票（取均值）：趋势/动量/波动/回撤四类各一票，0/0.25/0.5/0.75/1 平滑
    "X_投票三件套": ("vote", ["A_MA60清仓", "B_mom60清仓", "F_周MA20清仓"]),
    "X_投票四件套": ("vote", ["A_MA60清仓", "B_mom60清仓", "F_周MA20清仓", "D_dd15半仓"]),
    "X_投票四件套B": ("vote", ["A_MA120清仓", "B_mom60清仓", "F_周MA20半仓", "C_VT12"]),
}


# ---------------- 回测 ----------------
def stats(net: pd.Series) -> dict:
    net = net.dropna()
    n = len(net)
    if n < 60:
        return dict(cagr=np.nan, mdd=np.nan, calmar=np.nan, sharpe=np.nan)
    nav = (1 + net).cumprod()
    cagr = nav.iloc[-1] ** (TD / n) - 1
    mdd = (nav / nav.cummax() - 1).min()
    sd = net.std()
    return dict(cagr=cagr, mdd=mdd, calmar=cagr / abs(mdd) if mdd < 0 else np.nan,
                sharpe=net.mean() / sd * np.sqrt(TD) if sd > 0 else np.nan)


def run_pos(pos: pd.Series, ret: pd.Series, cost: float = None):
    c = COST if cost is None else cost
    p = pos.shift(1).fillna(0.0)
    gross = p * ret.fillna(0.0)
    turn = p.diff().abs().fillna(p.abs())
    return gross - turn * c, p


def _row(code, grp, name, n, pos, ret):
    net, p = run_pos(pos, ret)
    st = stats(net)
    avg = float(p.mean())
    fixed = stats(avg * ret.fillna(0.0))
    h1, h2 = slice(0, n // 2), slice(n // 2, n)
    s1, b1 = stats(net.iloc[h1]), stats(ret.iloc[h1])
    s2, b2 = stats(net.iloc[h2]), stats(ret.iloc[h2])
    base = stats(ret)
    return dict(group=grp, name=code, rule=name, n=n, **st, avg_pos=avg,
                turnover=float(p.diff().abs().sum() / (n / TD)),
                d_calmar=st["calmar"] - base["calmar"],
                d_cagr=st["cagr"] - base["cagr"],
                dc1=s1["calmar"] - b1["calmar"], dc2=s2["calmar"] - b2["calmar"],
                d_calmar_fixed=st["calmar"] - fixed["calmar"],
                cagr_fixed=fixed["cagr"], calmar_fixed=fixed["calmar"])


def eval_asset(code, grp, df, R, combos):
    F = factors(df)
    ret = F["ret"]
    n = len(df)
    rows, cache = [], {}
    for name, fn in R.items():
        pos = fn(F).clip(0, 1)
        cache[name] = pos
        rows.append(_row(code, grp, name, n, pos, ret))
    for name, (mode, parts) in combos.items():
        if any(p not in cache for p in parts):
            continue
        mat = pd.concat({p: cache[p] for p in parts}, axis=1)
        pos = mat.min(axis=1) if mode == "min" else mat.mean(axis=1)
        rows.append(_row(code, grp, name, n, pos.clip(0, 1), ret))
    base = stats(ret)
    rows.append(dict(group=grp, name=code, rule="满仓", n=n, **base, avg_pos=1.0, turnover=0.0,
                     d_calmar=0.0, d_cagr=0.0, dc1=0.0, dc2=0.0, d_calmar_fixed=0.0,
                     cagr_fixed=base["cagr"], calmar_fixed=base["calmar"]))
    return rows


def main():
    uni = load_universe()
    R = build_rules()
    print(f"标的 {len(uni)}，基础规则 {len(R)}，组合 {len(COMBOS)}")
    all_rows = []
    for code, (grp, df) in uni.items():
        all_rows += eval_asset(code, grp, df, R, COMBOS)
    g = pd.DataFrame(all_rows)
    g.to_csv(OUT / "grid.csv", index=False, encoding="utf-8-sig")

    print(f"\n行数 {len(g)}，标的 {g['name'].nunique()}，规则 {g['rule'].nunique()}")
    print(f"样本：ETF {g[g.group=='ETF']['name'].nunique()} 只 "
          f"{g[g.group=='ETF']['n'].max()} 日；指数 {g[g.group=='指数']['name'].nunique()}、"
          f"蓝筹 {g[g.group=='蓝筹']['name'].nunique()}（长样本 {g[g.group!='ETF']['n'].max()} 日）")

    # 规则汇总（相对满仓 / 相对同仓位）
    piv = g.pivot_table(index="rule", values="d_calmar", aggfunc="median")
    agg = g.groupby("rule").agg(
        med_d=("d_calmar", "median"), pos_rate=("d_calmar", lambda s: (s > 0).mean()),
        med_d_fixed=("d_calmar_fixed", "median"), pos_fixed=("d_calmar_fixed", lambda s: (s > 0).mean()),
        med_avg_pos=("avg_pos", "median"), med_turn=("turnover", "median"))
    for grp in ("ETF", "指数", "蓝筹"):
        sub = g[g.group == grp].groupby("rule")["d_calmar"].median()
        agg[f"med_{grp}"] = sub
    agg = agg.sort_values("med_d", ascending=False)
    agg.round(3).to_csv(OUT / "rule_summary.csv", encoding="utf-8-sig")

    show = ["med_d", "pos_rate", "med_d_fixed", "pos_fixed", "med_ETF", "med_指数", "med_蓝筹", "med_avg_pos"]
    print("\n== 规则中位卡玛增量（d_calmar=vs满仓，d_fixed=vs同均仓）前 20 ==")
    print(agg[show].head(20).round(3).to_string())
    print("\n== 后 5（明显有害的）==")
    print(agg[show].tail(5).round(3).to_string())

    print("\n== 按 d_fixed（真时机能力）排前 12 ==")
    print(agg.sort_values("med_d_fixed", ascending=False)[show].head(12).round(3).to_string())


if __name__ == "__main__":
    main()
