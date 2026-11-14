#!/usr/bin/env python3
"""状态切换波动帽 A-SV（v4）。见 runs/2026-11-14_statevol_v4/PREREG.md
复用 ablation_v3_backtest 的面板/口径/统计；只替换臂集合与诊断。"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402
import layered_v1_backtest as L  # noqa: E402
from single_asset_harness import run_pos  # noqa: E402

RUN = AB.ROOT / "runs" / "2026-11-14_statevol_v4"
ARMS = ["A", "A-vol", "A-SV"]
NEW_ARMS = ["A-SV"]
OFF_CONFIRM = 3  # 冻结：连续 3 个 T0/T1 日才关帽


def cap_state(T, ma200):
    """返回 bool Series：True=关帽。非上行当日即开帽；开帽后需连续 3 日上行才关帽。"""
    up = (T.isin([0, 1]) & ma200.notna()).to_numpy()
    off = np.zeros(len(up), dtype=bool)
    state_off = None
    run = 0
    for i, u in enumerate(up):
        run = run + 1 if u else 0
        if state_off is None:          # 首个可算日
            state_off = bool(u)
        elif state_off:
            if not u:
                state_off = False      # 立即开帽
        else:
            if run >= OFF_CONFIRM:
                state_off = True
        off[i] = state_off
    return pd.Series(off, index=T.index)


def arms_positions(F):
    g1, volcap, T = L.g1_volcap_T(F)
    taper = T.map(L.TAPER_BASE).astype(float)
    A = (g1 * volcap * taper).clip(0, 1)
    off = cap_state(T, F["ma200"])
    capS = pd.Series(np.where(off.to_numpy(), 1.0, volcap.to_numpy()), index=A.index)
    pos = {"A": A, "A-vol": (g1 * taper).clip(0, 1), "A-SV": (g1 * capS * taper).clip(0, 1)}
    comps = dict(g1=g1, volcap=volcap, capS=capS, taper=taper, capoff=off.astype(float))
    return pos, comps


def eval_one(nm, panel, windows, ev, rows, diag):
    """同 v3 的 eval_one，诊断对所有标的记录（关帽占比等）。"""
    df = nm["df"]
    F = L.slim_factors(df)
    pos, comps = arms_positions(F)
    ret = F["ret"]
    dates = pd.Series(ret.index)
    nets = {a: run_pos(p, ret, cost=AB.COST) for a, p in pos.items()}
    base = {k: nm.get(k, "") for k in ("code", "name", "sector", "category", "focus", "lane", "ret_type")}
    base.update(panel=panel, first=str(dates.iloc[0].date()), last=str(dates.iloc[-1].date()))
    for w, a, b in windows:
        m = ev.copy()
        if a:
            m &= ((dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b))).to_numpy()
        bh = None
        for arm, (net, ps) in nets.items():
            s = AB.wstats(net, ret, ps, m)
            if s is None:
                break
            rows.append(dict(base, window=w, arm=arm, **s))
            bh = s
        if bh is None:
            continue
        rows.append(dict(base, window=w, arm="BH", n=bh["n"], cagr=bh["bh_cagr"], mdd=bh["bh_mdd"],
                         calmar=bh["bh_calmar"], avg_pos=1.0, turn=0.0, static_cagr=bh["bh_cagr"],
                         static_mdd=bh["bh_mdd"], static_calmar=bh["bh_calmar"], d_calmar_static=0.0,
                         bh_cagr=bh["bh_cagr"], bh_mdd=bh["bh_mdd"], bh_calmar=bh["bh_calmar"]))
        mm = pd.Series(m, index=ret.index)
        diag.append(dict(base, window=w, n=int(m.sum()),
                         capoff_share=float(comps["capoff"][mm].mean()),
                         volcap_at_floor=float((comps["volcap"][mm] <= 0.4 + 1e-9).mean()),
                         capS_at_floor=float((comps["capS"][mm] <= 0.4 + 1e-9).mean()),
                         volcap_mean=float(comps["volcap"][mm].mean()),
                         capS_mean=float(comps["capS"][mm].mean()),
                         g1_mean=float(comps["g1"][mm].mean()),
                         taper_mean=float(comps["taper"][mm].mean()),
                         vol60_median=float(F["vol60"][mm].median())))


def main():
    AB.RUN = RUN
    AB.ARMS = ARMS
    AB.NEW_ARMS = NEW_ARMS
    AB.arms_positions = arms_positions
    AB.eval_one = eval_one
    AB.main()  # 写 results_long/by_window/vs_static/cross_market/adoption/snapshot 等
    (RUN / "ablation_v3_backtest.py").unlink(missing_ok=True)  # AB.main 复制的是 v3 脚本，删去
    # 改写 snapshot 字段
    import json
    sp = RUN / "snapshot.json"
    snap = json.loads(sp.read_text(encoding="utf-8"))
    snap.pop("lowvol", None)
    snap.update(arms=ARMS, cap_off_confirm=OFF_CONFIRM, prereg="runs/2026-11-14_statevol_v4/PREREG.md",
                script="work/statevol_v4_backtest.py", infra="work/ablation_v3_backtest.py")
    sp.write_text(json.dumps(snap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    shutil.copy2(Path(__file__), RUN / "statevol_v4_backtest.py")
    AB.log("v4 完成")


if __name__ == "__main__":
    main()
