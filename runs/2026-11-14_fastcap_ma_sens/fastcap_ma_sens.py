#!/usr/bin/env python3
"""A-FC 均线敏感性：M 信号用 MA20 / MA30 替换 MA50。见 runs/2026-11-14_fastcap_ma_sens/PREREG.md
复用 fastcap_v5_backtest（快信号、帽状态、崩盘窗口）与 ablation_v3 面板/统计。"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402
import layered_v1_backtest as L  # noqa: E402
import long_history_backtest as LH  # noqa: E402
import statevol_v4_backtest as S  # noqa: E402
import statevol_v4_episodes as EP  # noqa: E402
import fastcap_v5_backtest as V  # noqa: E402
from single_asset_harness import run_pos  # noqa: E402

RUN = AB.ROOT / "runs" / "2026-11-14_fastcap_ma_sens"
FC = {"A-FC": 50, "A-FC30": 30, "A-FC20": 20}  # A-FC = v5 MA50
ARMS = ["A", "A-vol", "A-SV"] + list(FC)
NEW_ARMS = list(FC)
SIGS = V.SIGS


def fc_signals(F, T, ma):
    old = V.MA_FAST
    V.MA_FAST = ma
    try:
        return V.fast_signals(F, T)
    finally:
        V.MA_FAST = old


def arms_positions(F):
    g1, volcap, T = L.g1_volcap_T(F)
    taper = T.map(L.TAPER_BASE).astype(float)
    pos, comps = S.arms_positions(F)
    for arm, ma in FC.items():
        sig = fc_signals(F, T, ma)
        off = pd.Series(V.cap_state_clear((~sig.any(axis=1)).to_numpy()), index=T.index)
        cap = pd.Series(np.where(off.to_numpy(), 1.0, volcap.to_numpy()), index=T.index)
        pos[arm] = (g1 * cap * taper).clip(0, 1)
        comps[f"off_{arm}"] = off.astype(float)
        comps[f"sig_{arm}"] = sig
    return pos, comps


def eval_one(nm, panel, windows, ev, rows, diag):
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
        yrs = m.sum() / 252
        rec = dict(base, window=w, n=int(m.sum()))
        for arm in FC:
            o = comps[f"off_{arm}"]
            sig = comps[f"sig_{arm}"]
            rec[f"capoff_share_{arm}"] = float(o[mm].mean())
            rec[f"toggles_per_year_{arm}"] = float((o.diff().abs() > 0)[mm].sum() / yrs)
            ev_on = (o.shift(1) == 1) & (o == 0) & mm
            rec[f"capon_events_{arm}"] = int(ev_on.sum())
            for sname in SIGS:
                rec[f"on_by_{sname}_{arm}"] = int((sig[sname] & ev_on).sum())
            rec[f"sig_share_M_{arm}"] = float(sig["M"][mm].mean())
        for arm in ARMS:
            rec[f"turn_{arm}"] = float(nets[arm][1][m].diff().abs().sum() / yrs)
        diag.append(rec)


def episodes():
    out = []
    arms_eq = ["BH"] + ARMS
    for panel, code, name, df in EP.series():
        F = L.slim_factors(df)
        pos, comps = arms_positions(F)
        ret = F["ret"].fillna(0.0)
        eq = {"BH": (1 + ret).cumprod()}
        for a, p in pos.items():
            net, _ = run_pos(p, F["ret"], cost=AB.COST)
            eq[a] = (1 + net.fillna(0.0)).cumprod()
        idx = ret.index
        ev_start = idx[LH.WARMUP] if len(idx) > LH.WARMUP else idx[-1]
        for ep, (p0, p1, t1) in EP.EPIS.items():
            if ep.startswith("A") == panel.startswith("xmkt"):
                continue
            pw = eq["BH"][(idx >= pd.Timestamp(p0)) & (idx <= pd.Timestamp(p1))]
            if len(pw) < 20 or pw.index[0] < ev_start:
                continue
            P = pw.idxmax()
            tw = eq["BH"][(idx > P) & (idx <= pd.Timestamp(t1))]
            if len(tw) < 5:
                continue
            Tr = tw.idxmin()
            iP, iT = idx.get_loc(P), idx.get_loc(Tr)
            rec = dict(panel=panel, code=code, name=name, episode=ep, peak=str(P.date()), trough=str(Tr.date()))
            for arm, key in [("A-SV", "capoff")] + [(a, f"off_{a}") for a in FC]:
                seg = comps[key].iloc[iP:iT + 1]
                on = np.flatnonzero(seg.to_numpy() == 0)
                iC = iP + (on[0] if len(on) else iT - iP)
                C1 = idx[min(iC + 1, iT)]
                rec[f"cap_on_{arm}"] = str(idx[iC].date())
                rec[f"days_to_capon_{arm}"] = int(iC - iP)
                rec[f"leg1_{arm}"] = float(eq[arm][C1] / eq[arm][P] - 1)
                rec[f"leg1_BH_{arm}"] = float(eq["BH"][C1] / eq["BH"][P] - 1)
                rec[f"leg1_A_{arm}"] = float(eq["A"][C1] / eq["A"][P] - 1)
            for a in arms_eq:
                sg = eq[a].iloc[iP:iT + 1] / eq[a].iloc[iP]
                rec[f"mdd_{a}"] = float((sg / sg.cummax() - 1).min())
            out.append(rec)
    pd.DataFrame(out).to_csv(RUN / "crash_first_leg.csv", index=False, encoding="utf-8-sig")


def main():
    AB.RUN = RUN
    AB.ARMS = ARMS
    AB.NEW_ARMS = NEW_ARMS
    AB.arms_positions = arms_positions
    AB.eval_one = eval_one
    AB.main()
    (RUN / "ablation_v3_backtest.py").unlink(missing_ok=True)
    episodes()
    sp = RUN / "snapshot.json"
    snap = json.loads(sp.read_text(encoding="utf-8"))
    snap.pop("lowvol", None)
    snap.update(arms=ARMS, fc_ma=FC, frozen=dict(R=V.R_TH, V=V.V_TH, off_confirm=V.OFF_CONFIRM, vol20_min_periods=20),
                prereg="runs/2026-11-14_fastcap_ma_sens/PREREG.md", script="work/fastcap_ma_sens.py",
                infra=["work/ablation_v3_backtest.py", "work/statevol_v4_backtest.py", "work/fastcap_v5_backtest.py"],
                note="敏感性检查（3 档），非采用检验；adoption.csv 仅按 v5 标准机械判定")
    sp.write_text(json.dumps(snap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    shutil.copy2(Path(__file__), RUN / "fastcap_ma_sens.py")
    AB.log("ma_sens 完成")


if __name__ == "__main__":
    main()
