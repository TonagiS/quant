#!/usr/bin/env python3
"""快信号触发波动帽 A-FC（v5）。见 runs/2026-11-14_fastcap_v5/PREREG.md
复用 statevol_v4（进而 ablation_v3）的面板/口径/统计；新增 A-FC 臂、诊断与崩盘第一段。"""
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
from single_asset_harness import run_pos  # noqa: E402

RUN = AB.ROOT / "runs" / "2026-11-14_fastcap_v5"
ARMS = ["A", "A-vol", "A-SV", "A-FC"]
NEW_ARMS = ["A-FC"]
R_TH, V_TH, MA_FAST, OFF_CONFIRM = -0.08, 1.3, 50, 3  # 冻结
SIGS = ["nonU", "R", "V", "M"]


def fast_signals(F, T):
    c = F["close"]
    U = (T.isin([0, 1]) & F["ma200"].notna())
    R = (c / c.rolling(60, min_periods=1).max() - 1) <= R_TH
    vol20 = F["ret"].rolling(20, min_periods=20).std() * np.sqrt(L.TD)
    V = ((vol20 / F["vol60"]) > V_TH).fillna(False)
    ma50 = c.rolling(MA_FAST, min_periods=MA_FAST).mean()
    M = (c < ma50).fillna(False) & ma50.notna()
    return pd.DataFrame({"nonU": ~U, "R": R, "V": V, "M": M})


def cap_state_clear(clear):
    """clear: bool array（满足关帽条件）。关帽时不满足即开帽；开帽时连续 3 日满足才关帽。True=关帽。"""
    off = np.zeros(len(clear), dtype=bool)
    st, run = None, 0
    for i, u in enumerate(clear):
        run = run + 1 if u else 0
        if st is None:
            st = bool(u)
        elif st:
            if not u:
                st = False
        elif run >= OFF_CONFIRM:
            st = True
        off[i] = st
    return off


def arms_positions(F):
    g1, volcap, T = L.g1_volcap_T(F)
    taper = T.map(L.TAPER_BASE).astype(float)
    pos, comps = S.arms_positions(F)
    sig = fast_signals(F, T)
    off = pd.Series(cap_state_clear((~sig.any(axis=1)).to_numpy()), index=T.index)
    capF = pd.Series(np.where(off.to_numpy(), 1.0, volcap.to_numpy()), index=T.index)
    pos["A-FC"] = (g1 * capF * taper).clip(0, 1)
    comps.update(capF=capF, capoff_fc=off.astype(float), sig=sig)
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
    sig = comps["sig"]
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
        for tag, key in (("sv", "capoff"), ("fc", "capoff_fc")):
            o = comps[key]
            rec[f"capoff_share_{tag}"] = float(o[mm].mean())
            rec[f"toggles_per_year_{tag}"] = float((o.diff().abs() > 0)[mm].sum() / yrs)
        # 开帽事件（关→开）当日哪些信号触发
        o = comps["capoff_fc"]
        ev_on = (o.shift(1) == 1) & (o == 0) & mm
        rec["capon_events_fc"] = int(ev_on.sum())
        for sname in SIGS:
            rec[f"on_by_{sname}"] = int((sig[sname] & ev_on).sum())
            only = sig[sname] & ev_on & (sig[SIGS].sum(axis=1) == 1)
            rec[f"on_only_{sname}"] = int(only.sum())
            rec[f"sig_share_{sname}"] = float(sig[sname][mm].mean())
        for arm in ARMS:
            rec[f"turn_{arm}"] = float(nets[arm][1][m].diff().abs().sum() / yrs)
        diag.append(rec)


def episodes():
    out = []
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
        sig = comps["sig"]
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
            rec = dict(panel=panel, code=code, name=name, episode=ep, peak=str(P.date()), trough=str(Tr.date()),
                       bh_peak_to_trough=float(eq["BH"][Tr] / eq["BH"][P] - 1))
            for tag, key in (("SV", "capoff"), ("FC", "capoff_fc")):
                seg = comps[key].iloc[iP:iT + 1]
                on = np.flatnonzero(seg.to_numpy() == 0)
                iC = iP + (on[0] if len(on) else iT - iP)
                C1 = idx[min(iC + 1, iT)]
                rec[f"capoff_at_peak_{tag}"] = float(comps[key].iloc[iP])
                rec[f"cap_on_{tag}"] = str(idx[iC].date())
                rec[f"days_to_capon_{tag}"] = int(iC - iP)
                for a in ["BH", "A", "A-vol", "A-SV", "A-FC"]:
                    rec[f"leg1{tag}_{a}"] = float(eq[a][C1] / eq[a][P] - 1)
                if tag == "FC":
                    # 峰值后首个触发的快信号（按日；同日多个并列）
                    first = {}
                    for s_ in SIGS:
                        hit = np.flatnonzero(sig[s_].iloc[iP:iT + 1].to_numpy())
                        first[s_] = int(hit[0]) if len(hit) else 10**6
                    mn = min(first.values())
                    rec["first_signal"] = "+".join(s_ for s_ in SIGS if first[s_] == mn) if mn < 10**6 else ""
                    for s_ in SIGS:
                        rec[f"days_to_{s_}"] = first[s_] if first[s_] < 10**6 else np.nan
            for a in ["BH", "A", "A-vol", "A-SV", "A-FC"]:
                seg = eq[a].iloc[iP:iT + 1] / eq[a].iloc[iP]
                rec[f"mdd_{a}"] = float((seg / seg.cummax() - 1).min())
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
    snap.update(arms=ARMS, frozen=dict(R=R_TH, V=V_TH, MA=MA_FAST, off_confirm=OFF_CONFIRM, vol20_min_periods=20),
                prereg="runs/2026-11-14_fastcap_v5/PREREG.md", script="work/fastcap_v5_backtest.py",
                infra=["work/ablation_v3_backtest.py", "work/statevol_v4_backtest.py", "work/statevol_v4_episodes.py"])
    sp.write_text(json.dumps(snap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    shutil.copy2(Path(__file__), RUN / "fastcap_v5_backtest.py")
    AB.log("v5 完成")


if __name__ == "__main__":
    main()
