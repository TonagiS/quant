#!/usr/bin/env python3
"""champion_ext（2026-11-21）：冠军两种口径在 ext 事后池上的效果，并与 ext_rerun / 同起点 PIT 对照。
规则见 runs/2026-11-21_champion_ext/FREEZE.md。样本内。ext 有选择偏差，结果偏乐观。
用法：python3 work/champion_ext.py check | run"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext_rerun as ER  # noqa: E402  # 导入时已 S.setup() + TD.build()
import c3x_combo as X  # noqa: E402
import pv_pools as PV  # noqa: E402

RC, I, K, E = ER.RC, ER.I, ER.K, ER.E
cal, P, EXT = ER.cal, ER.P, ER.EXT
RUN = PV.ROOT / "runs" / "2026-11-21_champion_ext"
CACHE_FR = PV.ROOT / "data" / "cache_full_rebuild"
COSTS = (0.0005, 0.0020)
SEG = ER.SEG
G = {}


def feats_donch(close, member, sdate):
    """与 c3x_combo.feats(DONCH, NOCAP) 同公式，起点按 sdate（同 ext_rerun.feats）。"""
    ret = close.pct_change(fill_method=None)
    vol20 = ret.rolling(20, min_periods=15).std()
    mom120 = close / close.shift(120) - 1.0
    ma60 = close.rolling(60, min_periods=60).mean()
    bars = close.notna().cumsum()
    valid = (bars >= 150) & close.notna() & mom120.notna() & vol20.notna() & member
    rk = lambda X: X.where(valid).rank(axis=1, pct=True)
    pos = 0
    for n in (60, 120):
        hi = close.rolling(n, min_periods=n).max()
        lo = close.rolling(n, min_periods=n).min()
        pos = pos + rk((close - lo) / (hi - lo).where(hi > lo))
    rm = pos / 2.0
    hi60 = close.rolling(60, min_periods=60).max()
    lo60 = close.rolling(60, min_periods=60).min()
    cand = valid & rm.notna() & (close > (hi60 + lo60) / 2).fillna(False)
    enough = (valid.sum(axis=1) >= 8) & rm.notna().any(axis=1) & (close.index >= pd.Timestamp(sdate))
    start = int(np.argmax(enough.to_numpy())) if enough.any() else len(close) - 1
    return {"ret": ret, "vol20": vol20, "ma60": ma60, "valid": valid, "rm": rm, "cand": cand,
            "start": start, "bench": (1.0 + ret.mean(axis=1).fillna(0.0)).cumprod()}


def apply_cap2(f, secv):
    f = dict(f)
    cv = ER.cap2(f["rm"].to_numpy(), f["cand"].to_numpy(), secv)
    f["cand"] = pd.DataFrame(cv, index=f["cand"].index, columns=f["cand"].columns)
    return f


def run_vt(close, f, cost, vt):
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    r0 = E.simulate(close, f, cfg)
    if vt is None:
        rr = r0
    else:
        f2 = dict(f)
        f2["base_ret"] = r0["ret"]
        rr = E.simulate(close, f2, replace(cfg, rule=E.VolTarget(vt / 100.0, 60, 40, every=10)))
    return dict(ret=np.asarray(rr["ret"], float), turn=np.asarray(rr["turn"], float),
                expo=np.asarray(rr["expo"], float), lo=f["start"])


def p12_lists_in(C, A, min_bars=150):
    """在给定 close 列（ext 截断后的宇宙）内部做 P12_HYST60。"""
    A = A.reindex(index=C.index, columns=C.columns)
    A = A.where(C.notna())
    bars = C.notna().cumsum()
    a60 = A.rolling(60, min_periods=40).mean()
    me = C.index.to_series().groupby(C.index.to_period("M")).max()
    lists, prev = {}, None
    for d in me:
        if d not in C.index:
            continue
        ok = (bars.loc[d] >= min_bars) & C.loc[d].notna()
        s = a60.loc[d][ok].dropna().sort_values(ascending=False)
        if s.empty:
            lists[d] = list(prev or [])
            continue
        top60 = set(s.index[:60])
        keep = [c for c in (prev or []) if c in top60]
        out = keep + [c for c in s.index if c not in keep]
        prev = out[:40]
        lists[d] = prev
    return lists


def pack_row(kind, window, n_names, arm, cost, d):
    m = RC.metr(d["ret"], d["lo"], None, d["turn"], d["expo"])
    row = {"pool": kind, "window": window, "n_names": n_names, "arm": arm,
           "cost_bp": int(round(cost * 1e4)),
           "start": str(cal[d["lo"]].date()) if 0 <= d["lo"] < len(cal) else "",
           "lo": int(d["lo"]),
           "cagr": m["cagr"], "mdd": m["mdd"], "calmar": m["calmar"],
           "turn_two_way": m.get("turn_two_way", m.get("turn")),
           "avg_expo": m.get("avg_expo", m.get("expo", float(np.mean(d["expo"][d["lo"]:])) if d["lo"] < len(d["expo"]) else np.nan))}
    for s, (a, b) in SEG.items():
        ms = RC.metr(d["ret"], d["lo"], np.asarray(RC.seg_mask(a, b)))
        row[f"{s}_cagr"], row[f"{s}_mdd"], row[f"{s}_calmar"] = ms["cagr"], ms["mdd"], ms.get("calmar", np.nan)
    return row


def setup():
    if G:
        return
    X.setup_parent()
    # ext close/amount for P12-in-ext
    have = [c for c in EXT if c in P["close_tr"].columns]
    G["A_all"] = P["amount"].reindex(index=cal, columns=have)
    G["C_all"] = P["close_tr"].reindex(cal)[have]
    G["ref"] = {}
    for bp in (5, 20):
        z = np.load(PV.ROOT / "data" / "cache_combo3" / f"ref_{bp}.npz")
        G["ref"][bp] = dict(core=z["core"])
        G.setdefault("rS4", {})[bp] = np.load(CACHE_FR / f"ret_{bp}_NONE__BASE__NONE__S2__CAP2__VT15__NONE.npy")
        G.setdefault("rCH", {})[bp] = np.load(CACHE_FR / f"ret_{bp}_NONE__P12_HYST60__NONE__DONCH__CAP2__VT10__NONE.npy")
    # PIT P12 close / member / sector (full history)
    p12 = "P12_HYST60"
    ever = sorted({c for v in X.G["lists"][p12].values() for c in v})
    G["pit_p12_close"] = PV.G["C"][ever]
    G["pit_p12_M"] = X.G["M"][p12]
    G["pit_p12_sec"] = np.array([PV.G["sec"].get(c, "") for c in ever])
    # discover EXT starts (same as ext_rerun)
    _, fe19, _ = ER.universe("EXT19", "2019-01-02")
    _, fe17, _ = ER.universe("EXT17", "2017-01-01")
    G["ext19_start"] = str(cal[fe19["start"]].date())
    G["ext17_start"] = str(cal[fe17["start"]].date())
    G["wins"] = [("EXT19", "2019-01-02"), ("EXT17", "2017-01-01"),
                 ("PIT", "2019-01-02"), ("PIT", G["ext19_start"]),
                 ("PIT", "2017-01-03"), ("PIT", G["ext17_start"])]


def ext_universe(kind):
    """与 ext_rerun.universe 相同的价格截断，另给成交额。"""
    trunc = {"EXT19": "2019-01-02", "EXT17": "2017-01-01"}[kind]
    C, f_s2, secv = ER.universe(kind, trunc)
    A = G["A_all"].reindex(index=C.index, columns=C.columns)
    A = A.where(pd.Series(cal >= pd.Timestamp(trunc), index=cal), axis=0)
    return C, A, secv, trunc


def champ_ext(kind, variant, cost):
    C, A, secv, sdate = ext_universe(kind)
    if variant == "FIXED":
        member = pd.DataFrame(True, index=C.index, columns=C.columns)
        lists = None
    elif variant == "P12":
        lists = p12_lists_in(C, A, min_bars=150)
        ever = [c for c in C.columns]
        member = PV.PB.member_mask(lists, C.index, ever)
    else:
        raise KeyError(variant)
    f = apply_cap2(feats_donch(C, member, sdate), secv)
    d = run_vt(C, f, cost, 10)
    return d, C, lists, member


def champ_pit(sdate, cost):
    """PIT 冠军走 c3x_combo.feats（DONCH+CAP2），起点取 max(标准 start, sdate)。"""
    close, M = G["pit_p12_close"], G["pit_p12_M"]
    f = dict(X.feats(close, M, "DONCH", "CAP2"))
    lo = max(int(f["start"]), int(close.index.searchsorted(pd.Timestamp(sdate))))
    f["start"] = lo
    return run_vt(close, f, cost, 10)


def win_label(kind, f):
    return f"{kind}@{str(cal[f['start']].date())}"


def collect_books():
    """跑齐所有窗口的书。"""
    books = {}  # (kind, sdate, arm, bp) -> d
    rows = []
    for kind, sd in G["wins"]:
        if kind.startswith("EXT"):
            C, f_s2, secv = ER.universe(kind, sd)
        else:
            C, f_s2, secv = ER.universe("PIT", sd)
        lab = win_label(kind, f_s2)
        ncol = int(C.notna().any().sum())
        print(f"window {lab} n={ncol}", flush=True)
        for cost in COSTS:
            bp = int(round(cost * 1e4))
            # 对照：S2 / S2+VT15 / S4 走 ext_rerun
            for nm in ("S2", "S2+VT15", "S4"):
                d = ER.run_sat(C, f_s2, secv, nm, cost)
                books[(kind, sd, nm, bp)] = d
                rows.append(pack_row(kind, lab, ncol, nm, cost, d))
            # 冠军
            if kind.startswith("EXT"):
                for var, arm in (("FIXED", "CHAMP_EXT_FIXED"), ("P12", "CHAMP_EXT_P12")):
                    d, _, _, _ = champ_ext(kind, var, cost)
                    books[(kind, sd, arm, bp)] = d
                    rows.append(pack_row(kind, lab, ncol, arm, cost, d))
            else:
                d = champ_pit(sd, cost)
                books[(kind, sd, "CHAMP_PIT", bp)] = d
                rows.append(pack_row(kind, lab, ncol, "CHAMP_PIT", cost, d))
            # C3 与混合
            lo_win = f_s2["start"]
            core = I.core("C3", lo_win, cost)
            books[(kind, sd, "C3", bp)] = core
            rows.append(pack_row(kind, lab, ncol, "C3", cost, core))
            mix_arms = ["S4"]
            if kind.startswith("EXT"):
                mix_arms += ["CHAMP_EXT_FIXED", "CHAMP_EXT_P12"]
            else:
                mix_arms += ["CHAMP_PIT"]
            for sn in mix_arms:
                sat = books[(kind, sd, sn, bp)]
                # 与 ext_rerun 对照臂从窗口 feats start 混合；冠军若更晚则从其 lo 起，避免零收益垫前
                lo_mix = lo_win if sn in ("S2", "S2+VT15", "S4") else max(lo_win, int(sat["lo"]))
                mix = RC.mix(core, sat, 0.60, lo_mix, cost)
                books[(kind, sd, f"C3 60% + {sn}", bp)] = mix
                rows.append(pack_row(kind, lab, ncol, f"C3 60% + {sn}", cost, mix))
    return pd.DataFrame(rows), books


def cmd_check():
    setup()
    print("EXT19 start", G["ext19_start"], "EXT17 start", G["ext17_start"], flush=True)
    # 1) 全历史 PIT S4 / 冠军 vs 缓存
    # S4：ext_rerun 路径，sdate 取 2013 年足够早，start 由 enough 决定
    s0 = str(pd.Timestamp(PV.SDATE).date())
    Cp, fp, sp = ER.universe("PIT", s0)
    d_s4 = ER.run_sat(Cp, fp, sp, "S4", 0.0005)
    a, b = d_s4["ret"], G["rS4"][5]
    n = min(len(a), len(b))
    d1 = float(np.max(np.abs(a[:n] - b[:n])))
    print(f"PIT S4 vs cache maxabs={d1:.3e} lo={d_s4['lo']} len={len(a)} cache={len(b)}")
    d_ch = champ_pit(s0, 0.0005)
    c, e = d_ch["ret"], G["rCH"][5]
    n2 = min(len(c), len(e))
    d2 = float(np.max(np.abs(c[:n2] - e[:n2])))
    print(f"PIT champ vs cache maxabs={d2:.3e} lo={d_ch['lo']} len={len(c)} cache={len(e)}")
    # 2) ext 对照 vs ext_rerun 表
    ref = pd.read_csv(PV.ROOT / "runs/2026-11-21_ext_rerun/results.csv")
    worst = 0.0
    for kind, sd in (("EXT19", "2019-01-02"), ("EXT17", "2017-01-01")):
        C, f, secv = ER.universe(kind, sd)
        lab = win_label(kind, f)
        for cost in COSTS:
            bp = int(round(cost * 1e4))
            for nm in ("S2", "S2+VT15", "S4", "C3"):
                if nm == "C3":
                    d = I.core("C3", f["start"], cost)
                else:
                    d = ER.run_sat(C, f, secv, nm, cost)
                m = RC.metr(d["ret"], d["lo"], None, d["turn"], d["expo"])
                hit = ref[(ref.window == lab) & (ref.arm == nm) & (ref.cost_bp == bp)]
                if hit.empty:
                    print("MISSING ref", lab, nm, bp)
                    worst = 1.0
                    continue
                r = hit.iloc[0]
                err = max(abs(m["cagr"] - r["cagr"]), abs(m["mdd"] - r["mdd"]), abs(m["calmar"] - r["calmar"]))
                worst = max(worst, err)
                print(f"  align {lab} {nm} {bp}bp err={err:.3e}")
    ok = (d1 < 1e-6 and d2 < 1e-6 and worst < 1e-10)
    print("CHECK", "PASS" if ok else "FAIL", f"cache S4={d1:.3e} champ={d2:.3e} ext_table={worst:.3e}")
    if not ok:
        sys.exit(2)


def pool_stats():
    """P12 在 52 只内部的规模与换手，对照固定 ext 池。汇总从评价起点起算（不含截断后尚无 150 根的月份）。"""
    rows = []
    ev = {"EXT19": G["ext19_start"], "EXT17": G["ext17_start"]}
    for kind in ("EXT19", "EXT17"):
        C, A, secv, sdate = ext_universe(kind)
        lists = p12_lists_in(C, A, min_bars=150)
        ev0 = pd.Timestamp(ev[kind])
        prev = None
        for d, mem in lists.items():
            if pd.Timestamp(d) < ev0:
                prev = mem
                continue
            turned = np.nan
            evictions = additions = np.nan
            if prev is not None:
                a, b = set(mem), set(prev)
                if a or b:
                    turned = 1.0 - len(a & b) / max(len(a | b), 1)
                    evictions = len(b - a)
                    additions = len(a - b)
            rows.append(dict(pool=kind, date=d, n=len(mem), turnover=turned,
                             evictions=evictions, additions=additions,
                             n_fixed=int(C.loc[d].notna().sum()) if d in C.index else np.nan))
            prev = mem
    df = pd.DataFrame(rows)
    df.to_csv(RUN / "pool_p12_in_ext.csv", index=False)
    summ = []
    for kind, g in df.groupby("pool"):
        g2 = g.dropna(subset=["turnover"])
        # 首月若从空名单起步，换手=1 是名单诞生，不算例行换池；另报去掉首月后的换手
        g3 = g2.iloc[1:] if len(g2) else g2
        at40 = g[g.n == 40]
        ev40 = at40.dropna(subset=["evictions"])
        summ.append(dict(pool=kind, n_months=int(len(g)),
                         mean_n=float(g.n.mean()), min_n=int(g.n.min()), max_n=int(g.n.max()),
                         months_at_40=int((g.n == 40).sum()),
                         mean_turn=float(g2.turnover.mean()) if len(g2) else np.nan,
                         mean_turn_ex_first=float(g3.turnover.mean()) if len(g3) else np.nan,
                         share_unchanged_ex_first=float((g3.turnover == 0).mean()) if len(g3) else np.nan,
                         total_evictions=float(g3.evictions.sum()) if len(g3) else np.nan,
                         evictions_after_40=float(ev40.evictions.sum()) if len(ev40) else 0.0,
                         mean_n_fixed=float(g.n_fixed.mean())))
    sdf = pd.DataFrame(summ)
    sdf.to_csv(RUN / "pool_p12_summary.csv", index=False)
    return sdf


def cmd_run():
    setup()
    RUN.mkdir(parents=True, exist_ok=True)
    df, books = collect_books()
    df.to_csv(RUN / "results.csv", index=False)
    # 同起点切片：P12 因月末生效比 FIXED 晚约半个月，把 FIXED 从 P12 的 lo 起重算，避免把这 10 余个交易日算进「P12 有没有作用」
    same = []
    for kind, sd in G["wins"]:
        if not kind.startswith("EXT"):
            continue
        C, f_s2, secv = ER.universe(kind, sd)
        lab = win_label(kind, f_s2)
        ncol = int(C.notna().any().sum())
        for cost in COSTS:
            bp = int(round(cost * 1e4))
            dP = books[(kind, sd, "CHAMP_EXT_P12", bp)]
            dF = books[(kind, sd, "CHAMP_EXT_FIXED", bp)]
            lo = int(dP["lo"])
            for arm, d in (("CHAMP_EXT_FIXED_from_P12start", dF), ("CHAMP_EXT_P12", dP)):
                dd = dict(d, lo=lo)
                same.append(pack_row(kind, lab, ncol, arm, cost, dd))
            # 也切片 S4 / S2+VT15 到同一 lo，便于对照
            for arm in ("S4", "S2+VT15", "S2"):
                dd = dict(books[(kind, sd, arm, bp)], lo=lo)
                same.append(pack_row(kind, lab, ncol, f"{arm}_from_P12start", cost, dd))
    sdf_same = pd.DataFrame(same)
    sdf_same.to_csv(RUN / "results_same_start.csv", index=False)
    ps = pool_stats()
    print(ps.to_string(index=False), flush=True)
    # 校准摘要写入 json
    ref = pd.read_csv(PV.ROOT / "runs/2026-11-21_ext_rerun/results.csv")
    align = []
    for _, r in df.iterrows():
        if r.arm not in ("S2", "S2+VT15", "S4", "C3"):
            continue
        hit = ref[(ref.window == r.window) & (ref.arm == r.arm) & (ref.cost_bp == r.cost_bp)]
        if hit.empty:
            continue
        h = hit.iloc[0]
        align.append(dict(window=r.window, arm=r.arm, cost_bp=int(r.cost_bp),
                          dcagr=float(r.cagr - h.cagr), dmdd=float(r.mdd - h.mdd),
                          dcalmar=float(r.calmar - h.calmar)))
    verdict = dict(ext19_start=G["ext19_start"], ext17_start=G["ext17_start"],
                   n_rows=int(len(df)), align_max_abs=float(max(max(abs(x["dcagr"]), abs(x["dmdd"]), abs(x["dcalmar"])) for x in align) if align else np.nan),
                   pool_p12=ps.to_dict(orient="records"))
    (RUN / "verdict.json").write_text(json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(align).to_csv(RUN / "align_ext_rerun.csv", index=False)
    print("run done rows", len(df), "align_max", verdict["align_max_abs"], flush=True)
    # 打印主表便于写报告
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 200)
    for bp in (5, 20):
        x = df[df.cost_bp == bp]
        print(f"\n===== {bp}bp calmar =====")
        print(x.pivot_table(index="arm", columns="window", values="calmar", sort=False).round(3).to_string())


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "check":
        cmd_check()
    elif cmd == "run":
        cmd_run()
    else:
        raise SystemExit(f"unknown {cmd}")


if __name__ == "__main__":
    main()
