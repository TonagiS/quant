#!/usr/bin/env python3
"""候选规则 v2：BH / A / B / C / D / E / F 七臂一次性比较（预登记冻结，不开网格）。

权威规则：runs/2026-11-14_candidates_v2/PREREG.md
复用 work/layered_v1_backtest.py 的因子、臂 A、臂 B、过滤与统计口径。
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layered_v1_backtest as L  # noqa: E402
from single_asset_harness import run_pos  # noqa: E402

ROOT = L.ROOT
PRIOR = L.PRIOR
LAYERED = ROOT / "runs" / "2026-11-14_layered_v1"
RUN = ROOT / "runs" / "2026-11-14_candidates_v2"
RUN.mkdir(parents=True, exist_ok=True)

# frozen numbers
CONFIRM = 3
SLOPE_TH = -0.02
PCT_WIN, PCT_MIN = 750, 250
PCT_CUTS = (0.70, 0.90)
PCT_COEF = (1.0, 0.75, 0.50)

ARMS = ["A", "B", "C", "D", "E", "F"]
ARM_DESC = {
    "BH": "满仓",
    "A": "旧定稿 G1×volcap(15%,0.4)×taper",
    "B": "layered_v1",
    "C": "慢速组=layered_v1 慢速规则；主动组=A",
    "D": "熔断：3日确认(收盘<MA200且slope60<-2%)→0，3日收盘>MA200→1",
    "E": "自身vol60分位(750日,≥250)系数1/0.75/0.5 × D开关",
    "F": "0/1趋势：3日收盘>MA200→1，3日收盘<MA200→0",
}
FOCUS = ["科创芯片", "半导体设备", "机器人", "银行", "猪肉", "传媒"]
KEY_SECTORS = ["芯片/半导体", "半导体设备", "机器人", "传媒", "银行", "风格-红利/低波",
               "商品-黄金", "宽基", "债券", "养殖/畜牧"]


def log(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def streak(cond: pd.Series) -> np.ndarray:
    """当日为止条件连续成立的天数（NaN 视为不成立）。"""
    c = cond.fillna(False).to_numpy(bool)
    out = np.zeros(len(c), dtype=int)
    run = 0
    for i, v in enumerate(c):
        run = run + 1 if v else 0
        out[i] = run
    return out


def latch(on_cond: pd.Series, off_cond: pd.Series, ma_ok: pd.Series, init=1.0) -> pd.Series:
    """on 连续 CONFIRM 日 → 1；off 连续 CONFIRM 日 → 0；其余保持；MA200 缺失日为 1 且状态记为 1。"""
    son, soff = streak(on_cond), streak(off_cond)
    ok = ma_ok.to_numpy(bool)
    out = np.empty(len(son))
    cur = init
    for i in range(len(son)):
        if not ok[i]:
            cur = 1.0
        elif soff[i] >= CONFIRM:
            cur = 0.0
        elif son[i] >= CONFIRM:
            cur = 1.0
        out[i] = cur
    return pd.Series(out, index=on_cond.index)


def policy_C(F, slow):
    if slow:
        return L.policy_layered(F, slow=True)[0]
    return L.policy_baseline(F)[0]


def switch_D(F):
    c, ma, sl = F["close"], F["ma200"], F["slope60"]
    ma_ok = ma.notna()
    off = ma_ok & sl.notna() & (c < ma) & (sl < SLOPE_TH)
    on = ma_ok & (c > ma)
    return latch(on, off, ma_ok)


def vol_pct(F):
    v = F["vol60"]
    # 窗口含当日：分位 = 窗口内有效值中 <= 当日值的比例
    return v.rolling(PCT_WIN, min_periods=PCT_MIN).rank(method="max", pct=True)


def policy_E(F, sw=None):
    sw = switch_D(F) if sw is None else sw
    p = vol_pct(F)
    coef = np.select([p <= PCT_CUTS[0], p <= PCT_CUTS[1], p > PCT_CUTS[1]],
                     list(PCT_COEF), default=1.0)  # NaN (不足 250 有效) → 1.0
    coef = pd.Series(coef, index=p.index).where(p.notna(), 1.0)
    return (coef * sw).clip(0, 1), p


def policy_F(F):
    c, ma = F["close"], F["ma200"]
    ma_ok = ma.notna()
    return latch(ma_ok & (c > ma), ma_ok & (c < ma), ma_ok)


def self_check():
    idx = pd.date_range("2020-01-01", periods=10, freq="B")
    ok = pd.Series([True] * 10, index=idx)
    on = pd.Series([0, 0, 0, 0, 0, 0, 1, 1, 1, 0], index=idx).astype(bool)
    off = pd.Series([1, 1, 0, 1, 1, 1, 0, 0, 0, 1], index=idx).astype(bool)
    got = list(latch(on, off, ok))
    assert got == [1, 1, 1, 1, 1, 0, 0, 0, 1, 1], got
    ok2 = ok.copy(); ok2.iloc[7] = False
    got2 = list(latch(on, off, ok2))
    assert got2 == [1, 1, 1, 1, 1, 0, 0, 1, 1, 1], got2
    # percentile rank includes current value
    s = pd.Series(np.arange(300, dtype=float))
    r = s.rolling(750, min_periods=250).rank(method="max", pct=True)
    assert np.isnan(r.iloc[248]) and abs(r.iloc[249] - 1.0) < 1e-12
    s2 = pd.Series(list(np.arange(260, dtype=float)) + [0.5])
    r2 = s2.rolling(750, min_periods=250).rank(method="max", pct=True)
    assert abs(r2.iloc[-1] - 2 / 261) < 1e-12
    log("规则自检通过")


def comparison(res: pd.DataFrame) -> pd.DataFrame:
    cs = res[res.in_cross_section == True]  # noqa: E712
    base = cs[cs.rule == "A"].set_index("tx_code")
    rows = []
    for split in ("test", "train", "all"):
        groups = [("ALL", "截面", base.index)]
        for col, kind, pre in (("lane", "快慢组", "分组:"), ("category", "大类", "大类:"), ("sector", "板块", "板块:")):
            for k, g in base.groupby(col):
                groups.append((pre + str(k), kind, g.index))
        for th in FOCUS:
            m = base["focus"].fillna("").map(lambda s, t=th: L.focus_hit(s, t))
            groups.append(("焦点:" + th, "焦点", base.index[m.to_numpy()]))
        for gname, kind, codes in groups:
            a = base.loc[codes]
            a = a[a[f"{split}_n"] >= L.MIN_BARS_STATS]
            if a.empty:
                rows.append(dict(split=split, group=gname, kind=kind, arm="BH", n=0))
                continue
            rows.append(dict(split=split, group=gname, kind=kind, arm="BH", n=len(a),
                             med_cagr=a[f"{split}_bh_cagr"].median(), med_mdd=a[f"{split}_bh_mdd"].median(),
                             med_calmar=a[f"{split}_bh_calmar"].median(), med_avg_pos=1.0, med_turn=0.0))
            for arm in ARMS:
                b = cs[cs.rule == arm].set_index("tx_code").loc[a.index]
                r = dict(split=split, group=gname, kind=kind, arm=arm, n=len(b),
                         med_cagr=b[f"{split}_cagr"].median(), med_mdd=b[f"{split}_mdd"].median(),
                         med_calmar=b[f"{split}_calmar"].median(),
                         med_avg_pos=b[f"{split}_avg_pos"].median(), med_turn=b[f"{split}_turn"].median())
                for k in ("calmar", "cagr", "mdd"):
                    d = b[f"{split}_d_{k}"]
                    r[f"med_d_{k}_vs_bh"] = d.median()
                    r[f"pos_d_{k}_vs_bh"] = float((d > 0).mean())
                    da = b[f"{split}_{k}"] - a[f"{split}_{k}"]
                    r[f"med_d_{k}_vs_a"] = da.median()
                    r[f"pos_d_{k}_vs_a"] = float((da > 0).mean())
                r["med_d_calmar_fixed"] = b[f"{split}_d_calmar_fixed"].median()
                rows.append(r)
    return pd.DataFrame(rows)


def sector_consistency(cmp: pd.DataFrame, split="test", min_n=3) -> pd.DataFrame:
    sub = cmp[(cmp.split == split) & (cmp.kind == "板块") & (cmp.arm.isin(ARMS[1:])) & (cmp.n >= min_n)]
    out = []
    for arm, g in sub.groupby("arm"):
        out.append(dict(arm=arm, n_sectors=len(g),
                        frac_sector_dcalmar_vs_a_pos=float((g.med_d_calmar_vs_a > 0).mean()),
                        frac_sector_dcagr_vs_a_pos=float((g.med_d_cagr_vs_a > 0).mean()),
                        frac_sector_dmdd_vs_a_pos=float((g.med_d_mdd_vs_a > 0).mean())))
    return pd.DataFrame(out)


def P(x, s=False):
    return "NA" if pd.isna(x) else (f"{x*100:+.2f}%" if s else f"{x*100:.2f}%")


def N(x, s=False, nd=3):
    return "NA" if pd.isna(x) else (f"{x:+.{nd}f}" if s else f"{x:.{nd}f}")


def write_report(cmp, cons, snap):
    t = cmp[cmp.split == "test"]
    get = lambda g, a, sp="test": cmp[(cmp.split == sp) & (cmp.group == g) & (cmp.arm == a)].iloc[0]  # noqa: E731
    L_ = []
    L_ += ["# 单支 ETF 候选规则 v2：七臂比较", "",
           f"- 快照日 {snap['snapshot_date']}（Asia/Shanghai 行情末日）；单边 5bp，delay=1；测试段 2023-01-01 至 2026-11-13，训练段至 2022-12-31 只作背景；2025+ 已用过，不是新鲜留出。",
           f"- 截面 n={snap['cross_section_n']}（慢速组 {snap['slow_n']}，主动组 {snap['active_n']}），与 fullmkt / layered_v1 相同。",
           f"- A、B 重算与旧结果核对：{snap['reconcile']}",
           "- 预登记：本目录 `PREREG.md`；不开网格、不按品种选规则。本结果不是投资建议。", ""]
    for arm in ["BH"] + ARMS:
        L_.append(f"- {arm}：{ARM_DESC[arm]}")
    L_ += ["", "## 测试段截面（中位；差值为按只计算后取中位，括号内为为正比例）", "",
           "| 臂 | 年化 | MDD | 卡玛 | 仓位 | 换手 | d_calmar vs BH | d_cagr vs BH | d_mdd vs BH | d_calmar vs A | d_cagr vs A | d_mdd vs A |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for arm in ["BH"] + ARMS:
        r = get("ALL", arm)
        if arm == "BH":
            L_.append(f"| BH | {P(r.med_cagr)} | {P(r.med_mdd)} | {N(r.med_calmar)} | 1.00 | 0.0 | | | | | | |")
            continue
        L_.append(f"| {arm} | {P(r.med_cagr)} | {P(r.med_mdd)} | {N(r.med_calmar)} | {N(r.med_avg_pos,nd=2)} | {N(r.med_turn,nd=1)} | "
                  f"{N(r.med_d_calmar_vs_bh,True)} ({r.pos_d_calmar_vs_bh:.0%}) | {P(r.med_d_cagr_vs_bh,True)} ({r.pos_d_cagr_vs_bh:.0%}) | {N(r.med_d_mdd_vs_bh,True)} ({r.pos_d_mdd_vs_bh:.0%}) | "
                  f"{N(r.med_d_calmar_vs_a,True)} ({r.pos_d_calmar_vs_a:.0%}) | {P(r.med_d_cagr_vs_a,True)} ({r.pos_d_cagr_vs_a:.0%}) | {N(r.med_d_mdd_vs_a,True)} ({r.pos_d_mdd_vs_a:.0%}) |")
    L_ += ["", "## 训练段截面中位 d_calmar vs BH（一致性背景）", ""]
    L_.append("；".join(f"{a} {N(get('ALL', a, 'train').med_d_calmar_vs_bh, True)}（n={int(get('ALL', a, 'train').n)}）" for a in ARMS))
    L_ += ["", "## 测试段板块/焦点（年化 / MDD 中位）", "",
           "| 分组 | n | " + " | ".join(["BH"] + ARMS) + " |", "|---|---:|" + "---|" * 7]
    groups = ["分组:slow", "分组:active"] + [f"焦点:{x}" for x in FOCUS] + [f"板块:{x}" for x in KEY_SECTORS]
    for g in groups:
        rr = [get(g, a) for a in ["BH"] + ARMS]
        if not rr[0].n:
            continue
        L_.append(f"| {g} | {int(rr[0].n)} | " + " | ".join(f"{P(r.med_cagr)} / {P(r.med_mdd)}" for r in rr) + " |")
    L_ += ["", "## 测试段板块方向一致性（板块 n≥3，各板块按只中位相对 A 为正的板块比例）", "",
           "| 臂 | 板块数 | d_calmar vs A>0 | d_cagr vs A>0 | d_mdd vs A>0 |", "|---|---:|---:|---:|---:|"]
    for r in cons.itertuples():
        L_.append(f"| {r.arm} | {r.n_sectors} | {r.frac_sector_dcalmar_vs_a_pos:.0%} | {r.frac_sector_dcagr_vs_a_pos:.0%} | {r.frac_sector_dmdd_vs_a_pos:.0%} |")
    L_ += ["", "## 对照预登记判读", ""]
    L_ += snap["judgments"]
    L_ += ["", "## 文件", "- `work/candidates_v2_backtest.py`（副本在本目录）",
           "- results_long.csv、comparison_test.csv、comparison_all_splits.csv、summary_by_group.csv、sector_consistency.csv、focus_results.csv、live_signal.csv、exclusions.csv、snapshot.json"]
    (RUN / "REPORT.md").write_text("\n".join(L_), encoding="utf-8")


def judgments(cmp, cons):
    g = lambda grp, a, sp="test": cmp[(cmp.split == sp) & (cmp.group == grp) & (cmp.arm == a)].iloc[0]  # noqa: E731
    J = []
    A, C = g("ALL", "A"), g("ALL", "C")
    sA, sC = g("分组:slow", "A"), g("分组:slow", "C")
    aA, aC = g("分组:active", "A"), g("分组:active", "C")
    J.append(f"- C：慢速组中位年化 C {P(sC.med_cagr)} vs BH {P(g('分组:slow','BH').med_cagr)} vs A {P(sA.med_cagr)}，按只 d_cagr vs BH {P(sC.med_d_cagr_vs_bh, True)}；"
             f"主动组 C 与 A 中位年化 {P(aC.med_cagr)} / {P(aA.med_cagr)}（应相同）；全截面中位卡玛 C {N(C.med_calmar)} vs A {N(A.med_calmar)}，按只 d_calmar vs A {N(C.med_d_calmar_vs_a, True)}（为正 {C.pos_d_calmar_vs_a:.0%}，其余为与 A 完全相同的主动组）。"
             f"判定：{'通过' if (C.med_calmar >= A.med_calmar - 0.02 and sC.med_d_cagr_vs_bh > -0.01) else '未通过'}"
             "（慢速组年化拖累消失、主动组与 A 相同；全截面中位卡玛高于 A 主要因慢速组卡玛恢复，不是新的择时能力）。")
    for arm in ["D", "E", "F"]:
        r = g("ALL", arm)
        cal_ok = r.med_calmar >= A.med_calmar
        parts = []
        hv_ok = True
        for grp in ("焦点:科创芯片", "焦点:半导体设备", "板块:芯片/半导体", "板块:半导体设备"):
            x, xa = g(grp, arm), g(grp, "A")
            better = x.med_d_cagr_vs_bh - xa.med_d_cagr_vs_bh
            parts.append(f"{grp.split(':')[1]} 年化少赚 {P(x.med_d_cagr_vs_bh, True)} vs A {P(xa.med_d_cagr_vs_bh, True)}")
            if grp.startswith("焦点") and not (better >= 0.03):
                hv_ok = False
        verdict = "通过（优于旧定稿）" if (cal_ok and hv_ok) else "未通过，只当作「更高仓位、更弱风控」的另一点" if not cal_ok else "未通过（卡玛不低于 A，但高波动主题少赚未明显小于 A）"
        J.append(f"- {arm}：全截面中位卡玛 {N(r.med_calmar)} vs A {N(A.med_calmar)}（{'不低于' if cal_ok else '低于'} A）；" + "；".join(parts) + f"。判定：{verdict}。")
    c = cons.set_index("arm")
    cons_txt = "；".join(f"{a} {c.loc[a,'frac_sector_dcalmar_vs_a_pos']:.0%}（{int(c.loc[a,'n_sectors'])} 板块）" for a in c.index)
    J.append(f"- 多重比较：测试段各板块按只中位 d_calmar vs A 为正的板块比例：{cons_txt}。超过半数才算方向一致。C 的主动组与 A 完全相同（差值为 0，不计为正），因此该比例对 C 不适用，只看慢速组 3 个板块。")
    return J


def main():
    self_check()
    L.self_check_rules()
    uni = pd.read_csv(PRIOR / "universe_all.csv")
    rows, excl, live = [], [], []
    for i, row in uni.iterrows():
        code = row["tx_code"]
        if row["category"] == "货币":
            excl.append({"tx_code": code, "name": row["name"], "exclude": "货币ETF"})
            continue
        df = L.load_bars(code)
        if df is None:
            excl.append({"tx_code": code, "name": row["name"], "exclude": "拉数失败或有效日线不足60",
                         "cache_exists": (L.CACHE / f"{code}.csv").exists()})
            continue
        ev = (df["date"] >= pd.Timestamp(L.EVAL_START)) & (df["date"] <= pd.Timestamp(L.TEST_END))
        ev_n = int(ev.sum())
        tail = (df[ev] if ev.any() else df).tail(60)
        avg_amt = float(tail["amount"].mean()) if "amount" in tail.columns and len(tail) else np.nan
        F = L.slim_factors(df)
        ma200_valid = int(F["ma200"].loc[F["close"].index.isin(set(df.loc[ev, "date"]))].notna().sum())
        reason = L.classify_exclude(row, df, ev_n, ma200_valid, avg_amt)
        lane = "slow" if L.is_slow_group(row["category"], row["sector"]) else "active"
        rec_c = dict(tx_code=code, name=row["name"], index_name=row["index_name"], category=row["category"],
                     sector=row["sector"], focus=row["focus"], first=str(df.date.iloc[0].date()),
                     last=str(df.date.iloc[-1].date()), ev_n=ev_n, ma200_valid=ma200_valid,
                     avg_amt_60=avg_amt, exclude=reason or "", lane=lane)
        if reason:
            do_eval = (bool(row["focus"]) or reason.startswith("短历史") or reason.startswith("流动性")) and ev_n >= L.MIN_BARS_STATS
            excl.append(rec_c)
            if not do_eval:
                continue
            rec_c["in_cross_section"] = False
        else:
            rec_c["in_cross_section"] = True

        slow = lane == "slow"
        pA, T = L.policy_baseline(F)
        pB, info = L.policy_layered(F, slow=slow)
        pC = policy_C(F, slow)
        sw = switch_D(F)
        pD = sw
        pE, pct = policy_E(F, sw)
        pF = policy_F(F)
        pos = dict(A=pA, B=pB, C=pC, D=pD, E=pE, F=pF)
        ret = F["ret"]
        dates = ret.index
        masks = L.split_masks(pd.Series(dates, index=dates))
        li = dates[-1]
        lv = dict(tx_code=code, name=row["name"], category=row["category"], sector=row["sector"],
                  focus=row["focus"], lane=lane, date=str(pd.Timestamp(li).date()),
                  close=float(F["close"].loc[li]), ma200=F["ma200"].loc[li], slope60=F["slope60"].loc[li],
                  mom20=F["mom20"].loc[li], vol60=F["vol60"].loc[li], vol_pct750=pct.loc[li],
                  T_old=int(T.loc[li]), state_B=str(info["state"].loc[li]),
                  in_cross_section=rec_c["in_cross_section"])
        for arm, p in pos.items():
            lv[f"pos_{arm}"] = float(p.loc[li])
            net, ps = run_pos(p, ret, cost=L.COST)
            rec = dict(rec_c, rule=arm)
            for split, m in masks.items():
                for k, v in L.metrics_block(net, ret, ps, m.to_numpy()).items():
                    rec[f"{split}_{k}"] = v
            rows.append(rec)
        live.append(lv)

    res = pd.DataFrame(rows)
    res.to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(excl).to_csv(RUN / "exclusions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(live).to_csv(RUN / "live_signal.csv", index=False, encoding="utf-8-sig")
    res[res.focus.fillna("").astype(str) != ""].to_csv(RUN / "focus_results.csv", index=False, encoding="utf-8-sig")
    cs = res[res.in_cross_section == True]  # noqa: E712

    sr = []
    for arm in ARMS:
        sub = cs[cs.rule == arm]
        for split in ("train", "test", "all"):
            sr.append(dict(rule=arm, group="ALL", split=split, **L._agg(sub, split)))
            for col, pre in (("lane", "分组:"), ("category", "大类:"), ("sector", "板块:")):
                for k, gdf in sub.groupby(col):
                    sr.append(dict(rule=arm, group=pre + str(k), split=split, **L._agg(gdf, split)))
    pd.DataFrame(sr).to_csv(RUN / "summary_by_group.csv", index=False, encoding="utf-8-sig")

    cmp = comparison(res)
    cmp.to_csv(RUN / "comparison_all_splits.csv", index=False, encoding="utf-8-sig")
    cmp[cmp.split == "test"].to_csv(RUN / "comparison_test.csv", index=False, encoding="utf-8-sig")
    cons = sector_consistency(cmp)
    cons.to_csv(RUN / "sector_consistency.csv", index=False, encoding="utf-8-sig")

    # reconcile A vs fullmkt baseline, B vs layered_v1
    rec = {}
    cols = [f"{s}_{k}" for s in ("train", "test", "all") for k in ("cagr", "mdd", "avg_pos", "turn")]
    for arm, path, rule in (("A", PRIOR / "results_long.csv", "baseline"),
                            ("A_vs_layered", LAYERED / "results_long.csv", "baseline"),
                            ("B", LAYERED / "results_long.csv", "layered")):
        old = pd.read_csv(path)
        old = old[(old.rule == rule) & (old.in_cross_section == True)].set_index("tx_code")  # noqa: E712
        new = cs[cs.rule == arm[0]].set_index("tx_code")
        ov = old.index.intersection(new.index)
        diff = max(float((new.loc[ov, c] - old.loc[ov, c]).abs().max()) for c in cols)
        rec[arm] = dict(n_old=len(old), n_new=len(new), n_overlap=len(ov), max_abs_diff=diff,
                        ok=bool(len(old) == len(new) == len(ov) and diff < 1e-9))
    log(f"核对 {rec}")
    # C active == A active
    ca = cs[(cs.rule == "C") & (cs.lane == "active")].set_index("tx_code")
    aa = cs[(cs.rule == "A") & (cs.lane == "active")].set_index("tx_code")
    cb = cs[(cs.rule == "C") & (cs.lane == "slow")].set_index("tx_code")
    bb = cs[(cs.rule == "B") & (cs.lane == "slow")].set_index("tx_code")
    rec["C_active_eq_A"] = float(max((ca[c] - aa.loc[ca.index, c]).abs().max() for c in cols))
    rec["C_slow_eq_B"] = float(max((cb[c] - bb.loc[cb.index, c]).abs().max() for c in cols))

    snap = dict(snapshot_date=res["last"].max(), eval_start=L.EVAL_START, train_end=L.TRAIN_END,
                test_start=L.TEST_START, test_end=L.TEST_END, cost_one_way=L.COST, delay=1,
                cross_section_n=int(cs.tx_code.nunique()),
                slow_n=int((cs[cs.rule == "A"].lane == "slow").sum()),
                active_n=int((cs[cs.rule == "A"].lane == "active").sum()),
                evaluated_n=int(res.tx_code.nunique()),
                missing_cache_n=int(sum(1 for e in excl if e.get("cache_exists") is False)),
                cache_path=str(L.CACHE), script="work/candidates_v2_backtest.py",
                git_commit=L.git_commit(), prereg="runs/2026-11-14_candidates_v2/PREREG.md",
                reconcile=rec,
                frozen=dict(confirm=CONFIRM, slope_th=SLOPE_TH, pct_win=PCT_WIN, pct_min=PCT_MIN,
                            pct_cuts=PCT_CUTS, pct_coef=PCT_COEF))
    snap["judgments"] = judgments(cmp, cons)
    (RUN / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_report(cmp, cons, snap)
    shutil.copy2(Path(__file__), RUN / "candidates_v2_backtest.py")
    log("完成")


if __name__ == "__main__":
    main()
