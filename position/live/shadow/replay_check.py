"""Shadow 回放一致性测试：最近 60 个交易日，shadow engine 与三研究的选股/权重逐日对比。

研究侧：直接复用 backtest_composite 的 build_panel / build_features / xs_rank，
按与 robust/gate 相同口径构造 rm、候选、前 k、逆波动权重。
要求：目标集合完全一致，权重最大绝对差 < 1e-6。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))
sys.path.insert(0, HERE)

import backtest_composite as bc  # noqa: E402
import engine  # noqa: E402
from macd_structure import ETF_POOL  # noqa: E402
from pools import get_pool, load_one  # noqa: E402

CACHE = os.path.join(ROOT, "data", "cache2019")
START = "20190101"
N_DAYS = 60


def load_panel():
    data = {}
    for code, (name, kind) in get_pool("ext", ETF_POOL).items():
        df, _ = load_one(code, kind, START, CACHE)
        data[code] = df
    close, amt = bc.build_panel(data)
    return close, amt


def reference(close, amt):
    f = bc.build_features(close, amt, 120)
    rk = {k: bc.xs_rank(f[k], f["valid"], 8) for k in ("mom20", "mom60", "mom120")}
    rm = (rk["mom20"] + rk["mom60"] + rk["mom120"]) / 3
    cand = f["valid"] & rm.notna() & (close > f["ma60"])
    return f, rm, cand


def ref_targets(f, rm, cand, i, k=5):
    ok = cand.iloc[i].astype(bool) & rm.iloc[i].notna()
    idx = [c for c in cand.columns if ok[c]]
    if not idx or int(f["valid"].iloc[i].sum()) < 8:
        return {}
    top = sorted(idx, key=lambda c: -rm.iloc[i][c])[:k]
    vol = f["vol20"].iloc[i]
    iv = np.array([1.0 / max(float(vol[c]) if np.isfinite(vol[c]) else 1e-4, 1e-4) for c in top])
    w = iv / iv.sum()
    return {c: float(x) for c, x in zip(top, w)}


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    close, amt = load_panel()
    f, rm, cand = reference(close, amt)
    dates = close.index
    rows = []
    for i in range(len(dates) - N_DAYS, len(dates)):
        ref = ref_targets(f, rm, cand, i)
        sh = engine.targets(close.iloc[: i + 1])["targets"]
        keys = set(ref) | set(sh)
        diff = max([abs(ref.get(c, 0.0) - sh.get(c, 0.0)) for c in keys], default=0.0)
        rows.append({"date": str(dates[i].date()), "n_ref": len(ref), "n_shadow": len(sh),
                     "same_set": set(ref) == set(sh), "max_w_diff": diff})
    df = pd.DataFrame(rows)
    out = {"days": len(df), "max_abs_diff": float(df["max_w_diff"].max()),
           "set_mismatch_days": int((~df["same_set"]).sum()),
           "weight_mismatch_days": int((df["max_w_diff"] >= 1e-6).sum()),
           "start": df["date"].iloc[0], "end": df["date"].iloc[-1],
           "passed": bool(df["same_set"].all() and (df["max_w_diff"] < 1e-6).all())}
    print(f"回放 {out['days']} 天（{out['start']} ~ {out['end']}）：目标集合不一致 {out['set_mismatch_days']} 天，"
          f"权重差≥1e-6 的天数 {out['weight_mismatch_days']}，最大权重差 {out['max_abs_diff']:.2e}")
    print("结论：" + ("一致（通过）" if out["passed"] else "不一致，必须先修"))
    df.to_csv(os.path.join(HERE, "replay_diff.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(HERE, "replay_report.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
