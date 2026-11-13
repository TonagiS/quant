"""Shadow 观察臂 V2（缓冲带 k+2）：只记换手、滑点、成交价差，不参与 v1、不比收益。

每日 15:10 由 `position/live/shadow/daily.ps1` 调用；依赖当日 14:30 的 `data/daily/YYYYMMDD.json`（由 run_shadow 写入）。
"""
import json
import os
import sys
import time
from datetime import date

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(LIVE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))
sys.path.insert(0, HERE)
sys.path.insert(0, LIVE)

import engine  # noqa: E402
from run_shadow import hist_close, universe  # noqa: E402
from paper_account import last_price  # noqa: E402

SHADOW = HERE
RAW = os.path.join(ROOT, "data", "daily")
LOG = os.path.join(SHADOW, "v2_observ.csv")
STATE = os.path.join(SHADOW, "v2_state.json")
COLS = ["date", "n_targets", "rebalanced", "turnover_l1", "slippage_mean_bp",
        "slippage_p95_bp", "slippage_std_bp", "failed_n", "notes"]
K, BUF = 5, 7


def load(path, default):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return default


def targets_v2(close, prev):
    f = engine.features(close)
    i = len(close) - 1
    v = f["valid"].iloc[i]
    rm = f["rm"].iloc[i]
    if int(v.sum()) < 8 or not rm.notna().any():
        return {}
    order = [c for c in close.columns
             if bool(v[c]) and pd.notna(rm[c]) and close.iloc[i][c] > f["ma60"].iloc[i][c]]
    order.sort(key=lambda c: -rm[c])
    keep = [c for c in prev if c in set(order[:BUF])][:K]
    for c in order:
        if len(keep) >= K:
            break
        if c not in keep:
            keep.append(c)
    keep = keep[:K]
    if not keep:
        return {}
    vol = f["vol20"].iloc[i]
    iv = np.array([1.0 / max(float(vol[c]), 1e-4) for c in keep])
    return {c: float(x) for c, x in zip(keep, iv / iv.sum())}


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    codes, _ = universe()
    raw = load(os.path.join(RAW, f"{date.today():%Y%m%d}.json"), {"codes": {}, "failed": {}})
    p1430 = raw.get("codes", {})
    if not p1430:
        print("[v2] 当日没有 14:30 快照，跳过")
        return
    hist, failed = {}, dict(raw.get("failed", {}))
    for c in codes:
        try:
            s = hist_close(c)
            s.loc[pd.Timestamp(date.today())] = p1430.get(c, np.nan)
            hist[c] = s
        except Exception as e:
            failed[c] = f"v2hist {type(e).__name__}: {str(e)[:50]}"
        time.sleep(0.1)
    close = pd.DataFrame(hist).sort_index()
    state = load(STATE, {"t0": None, "targets": {}, "last_rebalance": None})
    t0 = state.get("t0") or str(date.today())
    if state.get("t0") is None:
        state["t0"] = t0
    idx = close.index
    n_days = int((idx > pd.Timestamp(t0)).sum()) if idx[-1] >= pd.Timestamp(t0) else 0
    rebal = state.get("last_rebalance") != str(date.today()) and n_days % 5 == 0
    prev = state.get("targets", {})
    tg = targets_v2(close, prev) if rebal else prev
    if rebal and not tg:
        tg = prev
    turn = sum(abs(tg.get(c, 0.0) - prev.get(c, 0.0)) for c in set(tg) | set(prev)) if rebal else 0.0
    slip = []
    for c in tg:
        try:
            pc, _src = last_price(c)
            if c in p1430 and p1430[c] > 0:
                slip.append(pc / p1430[c] - 1.0)
        except Exception:
            pass
    slip = np.array(slip) if slip else np.array([np.nan])
    row = {"date": str(date.today()), "n_targets": len(tg), "rebalanced": rebal,
           "turnover_l1": round(turn, 4),
           "slippage_mean_bp": round(float(np.nanmean(slip)) * 1e4, 2),
           "slippage_p95_bp": round(float(np.nanpercentile(slip, 95)) * 1e4, 2),
           "slippage_std_bp": round(float(np.nanstd(slip)) * 1e4, 2),
           "failed_n": len(failed), "notes": "观察臂：不计收益、不参与 v1"}
    df = pd.DataFrame([row], columns=COLS)
    df.to_csv(LOG, mode="a", header=not os.path.exists(LOG), index=False, encoding="utf-8-sig")
    state["targets"] = tg
    if rebal:
        state["last_rebalance"] = str(date.today())
    json.dump(state, open(STATE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[v2] targets={len(tg)} rebalanced={rebal} turnover={turn:.3f} "
          f"slippage={row['slippage_mean_bp']}bp p95={row['slippage_p95_bp']}bp")


if __name__ == "__main__":
    main()
