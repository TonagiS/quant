"""分层流水线 v3 —— 预注册见 runs/2026-11-15_layered_pipeline/PREREG.md

9 个假设：
  层1 方向 D1 MA200 半仓 / D2 PE 分位闸门
  层2 时机 T1 20日新高+跌破MA20离场 / T2 回调至MA20进场
  层3 仓位 S1 VT15(60日,15%,cap1) / S1b VT15+buffer
基准 = 等权 ext 池 + D0 + T0 + S0

口径：收盘出信号、delay=1 次日收盘成交、单边 5bp、L1 换手。
评估：5 折分段稳健（2019-01~2024-12）+ 留出段（2025-01~2026-11）+ block bootstrap。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "indicators" / "code"))
from macd_structure import ETF_POOL  # noqa: E402
from pools import CACHE_2019, get_pool, load_one  # noqa: E402

OUT = ROOT / "runs" / "2026-11-15_layered_pipeline"
OUT.mkdir(parents=True, exist_ok=True)

COST = 0.0005
TD = 252
HOLDOUT_START = "2025-01-01"


# ---------- 数据 ----------
def load_panel(pool_name: str = "ext") -> tuple[pd.DataFrame, pd.DataFrame]:
    pool = get_pool(pool_name, ETF_POOL)
    closes, amounts = {}, {}
    for code, (name, kind) in pool.items():
        if kind != "etf":
            continue
        try:
            df, _ = load_one(code, kind, "20190101", CACHE_2019)
            df = df.dropna(subset=["close"]).set_index("date")
            s = pd.to_numeric(df["close"], errors="coerce")
            s = s[s > 0]
            if len(s) < 200:
                continue
            closes[code] = s
            if "amount" in df.columns:
                amounts[code] = pd.to_numeric(df["amount"], errors="coerce")
        except Exception as e:  # noqa: BLE001
            print(f"  [skip] {code}: {e!r}")
    close = pd.DataFrame(closes).sort_index()
    amt = pd.DataFrame(amounts).reindex(close.index)
    close.index = pd.to_datetime(close.index)
    amt.index = close.index
    return close, amt


def load_hs300_pe() -> pd.Series:
    p = ROOT / "data" / "cache_4pct" / "pe_沪深300.csv"
    df = pd.read_csv(p)
    df["日期"] = pd.to_datetime(df["日期"])
    s = df.set_index("日期")["滚动市盈率"].astype(float)
    return s.sort_index()


# ---------- 各层 ----------
def equal_weight(close: pd.DataFrame, min_n: int = 5) -> pd.DataFrame:
    avail = close.notna()
    n = avail.sum(axis=1)
    ew = avail.div(n.replace(0, np.nan), axis=0)
    ew[n < min_n] = np.nan
    return ew.fillna(0.0)


def layer_direction(close: pd.DataFrame, mode: str, pe: pd.Series | None) -> pd.Series:
    """返回整体敞口乘数 0~1（只在信号层，执行时再 delay）。"""
    if mode == "D0":
        return pd.Series(1.0, index=close.index)
    idx = ROOT / "data" / "cache2007" / "idx_sh000300_20070101.csv"
    hs = pd.read_csv(idx, parse_dates=["date"]).set_index("date")["close"].astype(float)
    if mode == "D1":
        ma200 = hs.rolling(200, min_periods=200).mean()
        sig = (hs > ma200).astype(float)
        sig[ma200.isna()] = np.nan
        return sig.reindex(close.index).ffill().fillna(1.0).map({1.0: 1.0, 0.0: 0.5})
    if mode == "D2":
        # 月度 expanding 分位，只用当月之前的数据
        m = pe.resample("ME").last().dropna()
        pct = m.expanding(min_periods=60).apply(lambda a: (a[:-1] < a[-1]).mean(), raw=True)
        pct.index = pct.index + pd.offsets.MonthEnd(0)
        sig = pd.Series(1.0, index=m.index)
        sig[pct > 0.80] = 0.5
        sig[pct > 0.90] = 0.2
        sig.iloc[:60] = 1.0
        return sig.reindex(close.index, method="ffill").fillna(1.0)
    raise ValueError(mode)


def layer_timing(close: pd.DataFrame, mode: str) -> pd.DataFrame:
    """每只标的的持有门控 0/1（状态机，无未来数据）。"""
    if mode == "T0":
        return pd.DataFrame(1.0, index=close.index, columns=close.columns)
    ma20 = close.rolling(20, min_periods=20).mean()
    gate = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    c = close.to_numpy()
    m = ma20.to_numpy()
    if mode == "T1":
        hh = close.rolling(20, min_periods=20).max().shift(1).to_numpy()
        for j in range(close.shape[1]):
            hold = False
            for i in range(len(c)):
                if not np.isfinite(m[i, j]):
                    gate.iat[i, j] = 0.0
                    continue
                if hold and c[i, j] < m[i, j]:
                    hold = False
                if (not hold) and np.isfinite(hh[i, j]) and c[i, j] > hh[i, j]:
                    hold = True
                gate.iat[i, j] = 1.0 if hold else 0.0
    elif mode == "T2":
        for j in range(close.shape[1]):
            hold = False
            for i in range(len(c)):
                if not np.isfinite(m[i, j]):
                    gate.iat[i, j] = 0.0
                    continue
                if hold and c[i, j] >= m[i, j] * 1.03:
                    hold = False
                if (not hold) and c[i, j] <= m[i, j]:
                    hold = True
                gate.iat[i, j] = 1.0 if hold else 0.0
    else:
        raise ValueError(mode)
    return gate


def layer_sizing(w_raw: pd.DataFrame, ret: pd.DataFrame, mode: str) -> pd.Series:
    if mode == "S0":
        return pd.Series(1.0, index=w_raw.index)
    # VT15：用未加仓的组合收益估波动（只用当日及之前）
    expo = w_raw.sum(axis=1).replace(0, np.nan)
    pr = (w_raw * ret).sum(axis=1) / expo
    vol = pr.rolling(60, min_periods=40).std() * np.sqrt(TD)
    scale = (0.15 / vol).clip(lower=0.0, upper=1.0)
    scale = scale.fillna(1.0)
    if mode == "S1b":  # 缓冲带：进入 0.15，退出阈值放宽，减少抖动
        s = scale.to_numpy()
        out = s.copy()
        cur = s[0]
        for i in range(1, len(s)):
            if s[i] < cur:          # 要降就立即降（风控优先）
                cur = s[i]
            elif s[i] > cur * 1.15:  # 要升得超过 15% 才升
                cur = s[i]
            out[i] = cur
        return pd.Series(out, index=scale.index)
    return scale


# ---------- 回测 ----------
def backtest(w_sig: pd.DataFrame, ret: pd.DataFrame, cost: float = COST) -> pd.Series:
    w = w_sig.shift(1).fillna(0.0)
    s = w.sum(axis=1)
    over = s > 1.0
    if over.any():
        w.loc[over] = w.loc[over].div(s[over], axis=0)
    dw = w.diff().abs()
    dw.iloc[0] = w.iloc[0].abs()
    pnl = (w * ret).sum(axis=1) - cost * dw.sum(axis=1)
    return pnl


def metrics(pnl: pd.Series) -> dict:
    pnl = pnl.dropna()
    if len(pnl) < 60:
        return {"ann": np.nan, "mdd": np.nan, "calmar": np.nan, "sharpe": np.nan,
                "vol": np.nan, "turnover": np.nan}
    eq = (1 + pnl).cumprod()
    years = len(pnl) / TD
    ann = eq.iloc[-1] ** (1 / years) - 1
    dd = eq / eq.cummax() - 1
    mdd = dd.min()
    vol = pnl.std() * np.sqrt(TD)
    return {"ann": ann, "mdd": mdd, "calmar": ann / abs(mdd) if mdd < 0 else np.nan,
            "sharpe": pnl.mean() / pnl.std() * np.sqrt(TD) if pnl.std() > 0 else np.nan,
            "vol": vol, "turnover": pnl.abs().mean() * TD / 1.0}


def turnover_ann(w_sig: pd.DataFrame) -> float:
    w = w_sig.shift(1).fillna(0.0)
    dw = w.diff().abs()
    dw.iloc[0] = w.iloc[0].abs()
    return float(dw.sum(axis=1).mean() * TD)


# ---------- bootstrap ----------
def boot_calmar_diff(a: pd.Series, b: pd.Series, n: int = 2000, block: int = 20, seed: int = 11):
    """对 (候选, 基准) 的日收益做块自助，返回卡玛差的 95% CI。"""
    df = pd.concat([a, b], axis=1).dropna()
    x = df.iloc[:, 0].to_numpy()
    y = df.iloc[:, 1].to_numpy()
    T = len(x)
    rng = np.random.default_rng(seed)
    nblk = int(np.ceil(T / block))
    diffs = np.empty(n)
    for k in range(n):
        starts = rng.integers(0, T - block, nblk)
        idx = np.concatenate([np.arange(s, s + block) for s in starts])[:T]
        xa, ya = x[idx], y[idx]
        diffs[k] = _calmar(xa) - _calmar(ya)
    return float(np.quantile(diffs, 0.025)), float(np.quantile(diffs, 0.975))


def _calmar(p: np.ndarray) -> float:
    eq = np.cumprod(1 + p)
    years = len(p) / TD
    ann = eq[-1] ** (1 / years) - 1
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return ann / abs(dd) if dd < 0 else np.nan


# ---------- 主流程 ----------
def build(close, ret, amt, pe, dir_mode, time_mode, size_mode) -> pd.DataFrame:
    ew = equal_weight(close)
    gate = layer_timing(close, time_mode)
    d = layer_direction(close, dir_mode, pe)
    w_raw = ew * gate.mul(d, axis=0)
    s = layer_sizing(w_raw, ret, size_mode)
    w = w_raw.mul(s, axis=0)
    return w


def evaluate(close, ret, amt, pe, combos: dict) -> dict:
    res = {}
    for key, (dm, tm, sm) in combos.items():
        w = build(close, ret, amt, pe, dm, tm, sm)
        pnl = backtest(w, ret)
        res[key] = {"pnl": pnl, "w": w, "turnover": turnover_ann(w)}
    return res


def folds(idx: pd.DatetimeIndex, k: int = 5) -> list[tuple]:
    dev = idx[idx < HOLDOUT_START]
    n = len(dev)
    edges = [dev[0]] + [dev[int(n * i / k)] for i in range(1, k)] + [dev[-1]]
    return [(edges[i], edges[i + 1]) for i in range(k)]


def main():
    close, amt = load_panel("ext")
    close = close.loc["2019-01-01":]
    ret = close.pct_change()
    pe = load_hs300_pe()
    print(f"面板 {close.shape[0]} 日 × {close.shape[1]} 只  {close.index[0].date()}~{close.index[-1].date()}")

    combos = {
        "BASE":     ("D0", "T0", "S0"),
        "D1":       ("D1", "T0", "S0"),
        "D2":       ("D2", "T0", "S0"),
        "T1":       ("D0", "T1", "S0"),
        "T2":       ("D0", "T2", "S0"),
        "S1":       ("D0", "T0", "S1"),
        "S1b":      ("D0", "T0", "S1b"),
    }
    res = evaluate(close, ret, amt, pe, combos)

    fl = folds(close.index, 5)
    print(f"分段：{fl[0][0].date()}~{fl[-1][1].date()} 共 5 折；留出 {HOLDOUT_START} 之后")

    # 分段稳健：每折对基准的卡玛增量
    rows = []
    base_pnl = res["BASE"]["pnl"]
    hold = close.index >= HOLDOUT_START
    for key, r in res.items():
        if key == "BASE":
            continue
        row = {"candidate": key, "turnover": r["turnover"]}
        pos = 0
        for i, (a, b) in enumerate(fl):
            m = (close.index >= a) & (close.index < b)
            d = metrics(r["pnl"][m])["calmar"] - metrics(base_pnl[m])["calmar"]
            row[f"fold{i+1}"] = d
            pos += 1 if d > 0 else 0
        row["folds_pos"] = pos
        allm = close.index < HOLDOUT_START
        row["dev_calmar_delta"] = metrics(r["pnl"][allm])["calmar"] - metrics(base_pnl[allm])["calmar"]
        row["dev_ann_delta"] = metrics(r["pnl"][allm])["ann"] - metrics(base_pnl[allm])["ann"]
        row["hold_calmar_delta"] = metrics(r["pnl"][hold])["calmar"] - metrics(base_pnl[hold])["calmar"]
        row["hold_ann"] = metrics(r["pnl"][hold])["ann"]
        rows.append(row)
    df = pd.DataFrame(rows)

    # 全样本 bootstrap（只用开发段，避免偷看留出）
    boot = {}
    for key, r in res.items():
        if key == "BASE":
            continue
        lo, hi = boot_calmar_diff(r["pnl"][~hold], base_pnl[~hold])
        boot[key] = {"ci_lo": lo, "ci_hi": hi}

    summary = {}
    for key in ("BASE",):
        summary[key] = metrics(res[key]["pnl"])
    for key in combos:
        if key == "BASE":
            continue
        summary[key] = metrics(res[key]["pnl"])

    (OUT / "folds.csv").write_text(df.to_csv(index=False), encoding="utf-8")
    out = {
        "panel": f"{close.shape[0]}d x {close.shape[1]}",
        "period": f"{close.index[0].date()}~{close.index[-1].date()}",
        "folds": [[str(a.date()), str(b.date())] for a, b in fl],
        "metrics_full": summary,
        "folds": df.to_dict(orient="records"),
        "bootstrap_dev_calmar_ci": boot,
    }
    (OUT / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n== 全样本（含留出，仅参考）==")
    for k, v in summary.items():
        print(f"{k:6s} ann {v['ann']:7.2%} mdd {v['mdd']:7.2%} calmar {v['calmar']:5.2f} "
              f"sharpe {v['sharpe']:5.2f} turn {res[k]['turnover']:5.1f}")
    print("\n== 增量（vs BASE）==")
    print(df.to_string(index=False))
    print("\n== bootstrap（开发段，卡玛差 95% CI）==")
    for k, v in boot.items():
        print(f"{k:6s} [{v['ci_lo']:+.3f}, {v['ci_hi']:+.3f}]")
    print("\nwritten", OUT)


if __name__ == "__main__":
    main()
