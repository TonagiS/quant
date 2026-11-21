#!/usr/bin/env python3
"""S4 影子盘（纸面，不下单）每日收盘后运行器。
规则：position/frozen_candidates/FROZEN_S4.md（S4）、runs/2026-11-21_sat_vt15_control/PREREG_ADDENDUM.md（宽基+MA60+VT15）、
components_core.core_run(mult=ERP)（C3）、OBSERVATION_LIST.md OBS-01。提案与评估标准：PROPOSAL_S4_SHADOW.md。

用法（仓库根目录）：
  python position/live/shadow_s4/run_s4_shadow.py                # 正式：拉数据 → 算 → 写 position/live/shadow_s4/ 下日志
  python position/live/shadow_s4/run_s4_shadow.py --dry-run      # 演练：输出写到 shadow_s4/dryrun/，不碰正式日志
  可选：--no-pull（只用已拉数据）、--asof YYYY-MM-DD（截到某日）、--full-refresh（强制全市场 ETF 重拉）"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import s4lib as L  # noqa: E402

APPROVED = pd.Timestamp("2026-11-21")       # 用户批准日；正式记录从此后的首个 S4 信号日开始
COSTS = {"5bp": 0.0005, "20bp": 0.0020}
W_CORE = 0.60
EXTRA_CODES = ["sh511380", "sh511360", "sh513520", "sh513030"]


def daily_codes(P):
    import pit_pool_backtest as PB
    import recombine_core as RC
    ever = set(RC.data()["ever"])        # 研究期 PIT 曾入池名单（调用时尚未打补丁=研究缓存）
    pools = L.LIVE / "pools.json"
    if pools.exists():
        for v in json.loads(pools.read_text()).values():
            ever |= set(v)
    return sorted(ever | set(L.BROAD) | {c for c, *_ in PB.DIV} | set(EXTRA_CODES))


def eligible_codes(P):
    cat = P["cat"]
    C = P["close_tr"]
    return [c for c in C.columns if cat.get(c) not in ("货币", "债券") and C[c].loc[:L.RESEARCH_END].notna().iloc[-1]]


def month_ends_needing_refresh(cal, asof):
    me = cal.to_series().groupby(cal.to_period("M")).max()
    me = me[(me > L.RESEARCH_END) & (me.index < asof.to_period("M"))]
    return [d for d in me if not (L.LIVE / f"pool_refreshed_{d:%Y%m%d}").exists()]


def compute(cost):
    """返回该成本下各臂的日收益与持仓/待执行目标。全部调用冻结研究代码。"""
    import components_core as K
    import recombine_core as RC
    import sat_vt15_control as SV
    import prereg_satellite as PS
    E = K.E
    D = RC.data()
    cal, close = D["cal"], D["closeT"]
    cols = list(close.columns)
    T = len(cal)
    i = T - 1
    sd = RC.START_W["W2013"]
    # ---- S4（与 components_core.sat_run(sector=True) 逐行相同，另取 base_ret 以复算待执行目标）
    f = K.sat_feats(sd, sector=True)
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    r0 = E.simulate(close, f, cfg)
    f2 = dict(f); f2["base_ret"] = r0["ret"]
    rule = E.VolTarget(0.15, 60, 40, every=10)
    rr = E.simulate(close, f2, replace(cfg, rule=rule), keep_weights=True)
    chk = K.sat_run(sd, cost, sector=True)
    assert np.allclose(chk["ret"], rr["ret"]), "S4 与 components_core.sat_run 不一致"
    lo = int(f["start"])
    hold = {cols[c]: float(w) for c, w in rr["w_hist"][-1].items() if w > 1e-9}
    sig_s4 = i > lo and (i - lo) % 10 == 0          # rolling：首个信号在 lo+10，之后每 10 日
    cand = f["cand"].to_numpy()[i] & ~np.isnan(f["rm"].to_numpy()[i])
    ok = np.flatnonzero(cand)
    order = [int(c) for c in ok[np.argsort(-f["rm"].to_numpy()[i, ok])]][:5]
    scale_now = float(np.clip(rule.scale(i, {"base_rets": r0["ret"], "start": lo}), 0, 1))
    base_w = E._weights(order, f["vol20"].to_numpy()[i], 1.0)
    s4_next = {cols[c]: float(w * scale_now) for c, w in base_w.items()}
    s4 = dict(ret=np.asarray(rr["ret"]), turn=np.asarray(rr["turn"]), expo=np.asarray(rr["expo"]), lo=lo)
    info = {"pool_n": int(D["M"].iloc[i].sum()), "n_valid": int(f["valid"].iloc[i].sum()), "n_cand": int(cand.sum()),
            "vt_scale_now": scale_now, "signal_day": bool(sig_s4), "start_idx": lo,
            "next_signal_in_days": int((10 - (i - lo) % 10) % 10)}
    # ---- 宽基 5 只 + MA60 + VT15（调用冻结的 sat_vt15_control.broad_vt_run；目标按同一公式复算）
    if not PS.G:
        PS.setup()
    assert PS.G["lo0"] == lo
    broad = SV.broad_vt_run(cost)
    bret = pd.Series(PS.broad_run(cost)["ret"])
    P = K.panel()
    c5 = P["close_tr"].reindex(cal)[L.BROAD]
    ma = c5.rolling(60, min_periods=60).mean()
    bars = c5.notna().cumsum()

    def broad_target(s):
        el = [k for k in L.BROAD if bars[k].iat[s] >= 150 and np.isfinite(c5[k].iat[s])]
        r = bret.to_numpy()[max(lo, s - 59): s + 1]
        sc = 1.0
        if len(r) >= 40:
            sdv = float(np.std(r, ddof=1)) * np.sqrt(252)
            if np.isfinite(sdv) and sdv > 1e-8:
                sc = float(np.clip(0.15 / sdv, 0.0, 1.0))
        on = [k for k in el if np.isfinite(ma[k].iat[s]) and c5[k].iat[s] > ma[k].iat[s]]
        return {k: sc / len(el) for k in on} if el else {}, sc
    last_sig = i - (i - lo) % 10
    exec_sig = last_sig if last_sig < i else last_sig - 10       # 已执行的最近一次信号（信号日次日收盘成交）
    broad_hold, _ = broad_target(exec_sig)
    broad_prev, broad_sc = broad_target(i)            # 用今天收盘算出的目标（只有信号日才会在次日执行）
    broad_next = broad_prev if sig_s4 else {}
    # ---- C3 底仓 与 60/40 组合
    m = K.erp_mult()[0]
    core = K.core_run(lo, cost, mult=m)
    tg, mused = K.core_targets(K.core_returns(), lo, mult=m)
    me_exec = max(s for s in tg if s < i) if any(s < i for s in tg) else None
    c3_hold = {k: float(v) for k, v in tg[me_exec].items()} if me_exec is not None else {}
    c3_if_me = {k: float(v) for k, v in tg[i].items()} if i in tg else {}
    combo = RC.mix(core, s4, W_CORE, lo, cost)
    combo_hold = {}
    for k, v in c3_hold.items():
        combo_hold[k] = combo_hold.get(k, 0) + W_CORE * v
    for k, v in hold.items():
        combo_hold[k] = combo_hold.get(k, 0) + (1 - W_CORE) * v
    rets = {"S4": s4["ret"], "BROAD_VT15": np.asarray(broad["ret"]), "C3": np.asarray(core["ret"]),
            "C3_60_S4": np.asarray(combo["ret"])}
    return dict(cal=cal, i=i, info=info, rets=rets, hold={"S4": hold, "BROAD_VT15": broad_hold, "C3": c3_hold,
                "C3_60_S4(近似)": combo_hold},
                pending={"S4": s4_next if sig_s4 else {}, "BROAD_VT15": broad_next,
                         "C3(若今天是月末)": c3_if_me, "C3_mult_now": float(m.iloc[i])},
                preview={"S4(若今天是信号日)": s4_next, "BROAD_VT15(若今天是信号日)": broad_prev, "BROAD_vt_scale": broad_sc},
                s4_ret=s4["ret"], s4_turn=s4["turn"], lo=lo, w_hist=rr["w_hist"], cols=cols)


def obs01(cal):
    s = L.fred_dgs10()
    s = s.reindex(cal.union(s.index)).ffill().reindex(cal)
    d20 = s - s.shift(20)
    on = (d20 > 0.30).astype(float).where(s.notna()).shift(1)       # 同 slowdecline.daily_on（对齐交易日后滞后 1 日）
    return s, d20.shift(1), on


def fill1430_adj(prev_hold, new_hold, run_today):
    """执行日（信号日次日）：记录 14:30 成交与收盘成交的差（bp，正=14:30 更优）。只在当天运行时可取到分时。"""
    if not run_today:
        return np.nan
    import requests
    tot = 0.0
    for k in set(prev_hold) | set(new_hold):
        dw = new_hold.get(k, 0) - prev_hold.get(k, 0)
        if abs(dw) < 1e-9:
            continue
        try:
            j = requests.get(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={k}", timeout=10).json()
            arr = j["data"][k]["data"]["data"]
            px = {x.split()[0]: float(x.split()[1]) for x in arr}
            p1430, pc = px.get("1430"), px.get("1500") or float(arr[-1].split()[1])
            tot += dw * (pc / p1430 - 1.0)
        except Exception:  # noqa: BLE001
            return np.nan
    return tot * 1e4


def upsert(path, row):
    df = pd.read_csv(path) if path.exists() else pd.DataFrame()
    if len(df) and "date" in df:
        df = df[df.date != row["date"]]
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True).sort_values("date")
    df.to_csv(path, index=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-pull", action="store_true")
    ap.add_argument("--full-refresh", action="store_true")
    ap.add_argument("--asof", default=None)
    a = ap.parse_args()
    out = L.HERE / ("dryrun" if a.dry_run else "")
    out.mkdir(exist_ok=True)
    fp_ok, fp_bad = L.check_fingerprints()
    notes = [] if fp_ok else [f"FINGERPRINT_MISMATCH:{','.join(fp_bad)}"]
    P = L.research_panel()
    failed = {}
    if not a.no_pull:
        _, failed = L.pull(daily_codes(P))
    P2, chk = L.extend_panel(P, a.asof)
    asof = P2["close_tr"].index[-1]
    need = month_ends_needing_refresh(P2["close_tr"].index, asof)
    if (need or a.full_refresh) and not a.no_pull:
        L.log(f"全市场 ETF 重拉（月末池）：{[str(d.date()) for d in need]}")
        _, f2 = L.pull(eligible_codes(P))
        failed.update(f2)
        P2, chk = L.extend_panel(P, a.asof)
        for d in need:
            (L.LIVE / f"pool_refreshed_{d:%Y%m%d}").write_text("ok")
    notes += L.update_cc() if not a.no_pull else ["cc_not_updated(--no-pull)"]
    end = L.patch_modules(P2)
    import recombine_core as RC
    D = RC.data()
    # 最后一天若在月中会被 pit_members 当成"月末"生成伪池（次日生效、不影响任何已算日），不记录
    pools = {str(k.date()): v for k, v in D["lists"].items() if L.RESEARCH_END < k < end}
    (L.LIVE / "pools.json").write_text(json.dumps(pools, ensure_ascii=False))
    res = {k: compute(c) for k, c in COSTS.items()}
    r5 = res["5bp"]
    cal, i = r5["cal"], r5["i"]
    lo = r5["lo"]
    # 正式起点：批准日之后的首个 S4 信号日；净值从信号日次日（执行日）起算
    start_sig = next(j for j in range(lo + 10, len(cal) + 40, 10) if j >= len(cal) or cal[j] > APPROVED)
    started = start_sig < len(cal) and i >= start_sig
    y, d20, on = obs01(cal)
    run_today = (end.date() == datetime.now(ZoneInfo("Asia/Shanghai")).date())   # 14:30 分时只在当天可取
    prev_hold = {r5["cols"][c]: w for c, w in r5["w_hist"][-2].items()} if len(r5["w_hist"]) > 1 else {}
    exec_day = (i - lo) % 10 == 1
    row = {"date": str(end.date()), "phase": "eod", "status": "official" if started else "pre-start",
           "pool_n": r5["info"]["pool_n"], "n_valid": r5["info"]["n_valid"], "n_cand": r5["info"]["n_cand"],
           "signal_day": int(r5["info"]["signal_day"]), "exec_day": int(exec_day), "vt_scale": round(r5["info"]["vt_scale_now"], 4),
           "next_signal_in_days": r5["info"]["next_signal_in_days"],
           "holdings_json": json.dumps({k: round(v, 4) for k, v in r5["hold"]["S4"].items()}, ensure_ascii=False),
           "orders_json": json.dumps({k: round(v, 4) for k, v in r5["pending"]["S4"].items()}, ensure_ascii=False),
           "ret_close_fill_5bp": float(res["5bp"]["rets"]["S4"][i]), "ret_close_fill_20bp": float(res["20bp"]["rets"]["S4"][i]),
           "fill1430_adj_bp": fill1430_adj(prev_hold, r5["hold"]["S4"], run_today) if exec_day else np.nan,
           "failed": json.dumps(failed, ensure_ascii=False), "fingerprint_ok": int(fp_ok),
           "data_last": str(end.date()), "notes": ";".join(notes)}
    for k in COSTS:
        rr = res[k]["rets"]["S4"]
        row[f"nav_{k}"] = float(np.prod(1 + rr[start_sig + 1: i + 1])) if started else 1.0
    row["turn_today_5bp"] = float(r5["s4_turn"][i])
    if started:
        nav = np.cumprod(1 + res["5bp"]["rets"]["S4"][start_sig + 1: i + 1])
        dd = nav / np.maximum.accumulate(nav) - 1
        row["dd_5bp"], row["mdd_5bp"] = float(dd[-1]), float(dd.min())
    else:
        row["dd_5bp"] = row["mdd_5bp"] = 0.0
    row["K1_flag(mdd<=-25%)"] = int(row["mdd_5bp"] <= -0.25)
    upsert(out / "s4_log.csv", row)
    for arm in ("BROAD_VT15", "C3", "C3_60_S4"):
        ar = {"date": str(end.date()), "arm": arm, "status": row["status"],
              "holdings_json": json.dumps({k: round(v, 4) for k, v in r5["hold"][arm if arm != "C3_60_S4" else "C3_60_S4(近似)"].items()}, ensure_ascii=False),
              "ret_5bp": float(res["5bp"]["rets"][arm][i]), "ret_20bp": float(res["20bp"]["rets"][arm][i])}
        if arm == "BROAD_VT15":
            ar["orders_json"] = json.dumps({k: round(v, 4) for k, v in r5["pending"]["BROAD_VT15"].items()})
        if arm == "C3":
            ar["orders_json"] = json.dumps({k: round(v, 4) for k, v in r5["pending"]["C3(若今天是月末)"].items()})
            ar["erp_mult"] = round(r5["pending"]["C3_mult_now"], 4)
        for k in COSTS:
            x = res[k]["rets"][arm]
            ar[f"nav_{k}"] = float(np.prod(1 + x[start_sig + 1: i + 1])) if started else 1.0
        p = out / f"arm_{arm}.csv"
        upsert(p, ar)
    s4r = res["5bp"]["rets"]["S4"][i]
    on_t2 = float(on.iloc[i - 2]) if i >= 2 and np.isfinite(on.iloc[i - 2]) else 0.0
    upsert(out / "obs01_log.csv", {"date": str(end.date()), "dgs10_lag1": float(y.shift(1).iloc[i]), "d20_bp": float(d20.iloc[i] * 100),
                                   "trigger": int(on.iloc[i] == 1.0), "effective_today(t-2)": int(on_t2 == 1.0),
                                   "s4_ret_5bp": float(s4r), "hypo_s4_ret_if_half": float(s4r * (0.5 if on_t2 == 1.0 else 1.0))})
    if not chk.empty:
        chk.to_csv(out / "data_overlap_check.csv", index=False)
    if end.weekday() == 4:
        lg = pd.read_csv(out / "s4_log.csv").tail(5)
        (out / f"weekly_{end:%G%V}.md").write_text(
            f"# S4 影子周报 {end:%G-W%V}\n\n只记录，不用收益改规则。最近 5 个交易日（CSV）：\n\n" + lg[["date", "status", "signal_day", "vt_scale", "holdings_json", "orders_json",
                                                                   "ret_close_fill_5bp", "nav_5bp", "failed", "fingerprint_ok"]].to_csv(index=False) + "\n",
            encoding="utf-8")
    summary = {"asof": str(end.date()), "status": row["status"],
               "official_start_signal_day": str(cal[start_sig].date()) if start_sig < len(cal) else f"{start_sig - i} 个交易日后",
               "fingerprint_ok": fp_ok, "failed": failed, "notes": notes, "info": r5["info"],
               "holdings": {k: {c: round(v, 4) for c, v in d.items()} for k, d in r5["hold"].items()},
               "pending": {k: ({c: round(v, 4) for c, v in d.items()} if isinstance(d, dict) else d) for k, d in r5["pending"].items()},
               "preview_not_executed": {k: ({c: round(v, 4) for c, v in d.items()} if isinstance(d, dict) else d) for k, d in r5["preview"].items()},
               "obs01": {"d20_bp": round(float(d20.iloc[i] * 100), 1), "trigger": int(on.iloc[i] == 1.0)}}
    (out / "latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
