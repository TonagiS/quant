#!/usr/bin/env python3
"""slowdecline：阴跌识别 + 防御/指标逐个叠加 + 组合。规则见 runs/2026-11-21_slowdecline/FREEZE.md。样本内。"""
from __future__ import annotations

import itertools
import json
import sys
import time
from dataclasses import replace
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ideas_v1 as I  # noqa: E402
import slowdecline_engine as SE  # noqa: E402

K, RC, E = I.K, I.RC, I.E
ROOT = I.ROOT
RUN = ROOT / "runs" / "2026-11-21_slowdecline"
CI = I.CI
SEGS = ["Y2015", "Y2018", "S2021_24", "S2023_now"]
COSTS = (0.0005, 0.002)
TDY = 252
G = I.G
X = {}   # 本脚本的信号


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------ 规则
class VTX(E.Rule):
    requires_base = True

    def __init__(self, target=0.15, window=60, floor=None, down=False, lock=False, dd=None, vt=True, every=10):
        self.target, self.window, self.floor, self.down, self.lock, self.dd, self.vt = target, window, floor, down, lock, dd, vt
        self.every, self.cap, self.min_periods = every, 1.0, 40
        self.prev = 1.0
        self.name = "VTX"

    def reset(self):
        self.prev = 1.0

    def scale(self, j, ctx):
        src = ctx.get("base_rets")
        st = int(ctx.get("start", 0))
        s = 1.0
        if self.vt:
            lo = max(st, j - self.window + 1)
            r = src[lo: j + 1]
            if len(r) >= max(self.min_periods, 20):
                if self.down:
                    sd = float(np.sqrt(np.nanmean(np.minimum(r, 0.0) ** 2) * 2.0)) * np.sqrt(TDY)
                else:
                    sd = float(np.nanstd(r, ddof=1)) * np.sqrt(TDY)
                if self.floor is not None:
                    sd = max(sd, self.floor)
                if np.isfinite(sd) and sd > 1e-8:
                    s = float(np.clip(self.target / sd, 0.0, 1.0))
        if self.dd is not None:
            lo = max(st, j - 251)
            nav = np.cumprod(1 + np.nan_to_num(src[lo: j + 1]))
            if len(nav):
                d = nav[-1] / max(nav.max(), 1.0) - 1.0
                s = min(s, float(np.clip(1.0 + d / self.dd, 0.0, 1.0)))
        if self.lock and j - 60 >= st:
            nav = np.cumprod(1 + np.nan_to_num(src[j - 60: j + 1]))
            if nav[-1] / nav[0] - 1 < 0:
                s = min(s, self.prev)
        self.prev = s
        return s


# ------------------------------------------------------------------ 信号
def monthly_avail(periods, values, lag_day=16):
    cal = RC.data()["cal"]
    av = [(p + 1).to_timestamp() + pd.Timedelta(days=lag_day - 1) for p in periods]
    pos = cal.searchsorted(pd.DatetimeIndex(av))
    s = pd.Series(np.nan, index=cal)
    for p, v in zip(pos, values):
        if p < len(cal):
            s.iloc[p] = v
    return s.ffill()


def daily_on(ser, cond_fn, shift=1):
    cal = RC.data()["cal"]
    s = ser.sort_index()
    s = s[~s.index.duplicated()]
    s = s.reindex(cal.union(s.index)).ffill().reindex(cal)
    on = cond_fn(s)
    on = on.where(s.notna())
    return on.shift(shift)


def cn_month(col):
    return pd.PeriodIndex([pd.Period(f"{x[:4]}-{x[5:7]}", "M") for x in col], freq="M")


def setup():
    if X:
        return
    I.setup()
    D = RC.data()
    cal, close, M = D["cal"], D["closeT"], D["M"]
    RT = close.pct_change(fill_method=None)
    valid = G["base_f"]["W2013"]["valid"]
    # 池等权指数（PIT 成员）
    ewr = RT.where(M).mean(axis=1).fillna(0.0)
    ew = (1 + ewr).cumprod()
    X["ew"] = ew
    h3 = pd.read_csv(ROOT / "data/cache_index_proxy/H00300.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").close.sort_index()
    h3 = h3.reindex(cal.union(h3.index)).ffill().reindex(cal)

    def sd_mask(lv):
        r = lv.pct_change()
        r60 = lv / lv.shift(60) - 1
        v20 = r.rolling(20).std()
        med = v20.rolling(252, min_periods=200).median()
        return ((r60 < -0.05) & (v20 < med)).fillna(False)
    X["SD300"] = sd_mask(h3).to_numpy()
    X["SDEW"] = sd_mask(ew).to_numpy()
    # 个券特征
    X["mom20"] = close / close.shift(20) - 1
    X["mom60"] = close / close.shift(60) - 1
    X["mom120"] = close / close.shift(120) - 1
    bond = D["Rd"]["sh511010"].reindex(cal).fillna(0.0)
    bn = (1 + bond).cumprod()
    X["bond60"] = bn / bn.shift(60) - 1
    path = close.diff().abs().rolling(60, min_periods=50).sum()
    X["ser60"] = (close - close.shift(60)) / path
    X["dd60"] = close / close.rolling(60, min_periods=40).max() - 1
    # 宽度 / 新低 / 相关
    ma60 = close.rolling(60, min_periods=60).mean()
    v = valid & ma60.notna()
    br = ((close > ma60) & v).sum(axis=1) / v.sum(axis=1).replace(0, np.nan)
    X["breadth"] = br.fillna(0.5).to_numpy()
    low60 = close.rolling(60, min_periods=60).min()
    hi60 = close.rolling(60, min_periods=60).max()
    v2 = valid & low60.notna()
    nl = ((close <= low60) & v2).sum(axis=1) / v2.sum(axis=1).replace(0, np.nan)
    nh = ((close >= hi60) & v2).sum(axis=1) / v2.sum(axis=1).replace(0, np.nan)
    X["newlow"] = nl.rolling(5, min_periods=1).mean().fillna(0).to_numpy()
    X["nhnl"] = (nh - nl).rolling(10, min_periods=1).mean().fillna(0).to_numpy()
    Rm = RT.where(valid)
    sd_i = Rm.rolling(60, min_periods=40).std()
    w_n = Rm.notna().sum(axis=1)
    ewv = Rm.mean(axis=1).rolling(60, min_periods=40).var()
    s1, s2 = sd_i.sum(axis=1), (sd_i ** 2).sum(axis=1)
    N = sd_i.notna().sum(axis=1)
    rho = (N ** 2 * ewv - s2) / (s1 ** 2 - s2)
    X["corr_hi"] = (rho > rho.rolling(252, min_periods=200).quantile(0.8)).fillna(False).to_numpy()
    # 突破推力
    b = pd.Series(X["breadth"], index=cal)
    thrust = (b > 0.6) & (b.rolling(10).min() < 0.3)
    on = np.zeros(len(cal))
    last = -999
    for i, t in enumerate(thrust.to_numpy()):
        if t:
            last = i
        on[i] = 1.0 if i - last <= 60 else 0.0
    X["thrust"] = on
    # 宏观
    mon = pd.read_csv(CI / "ak_money.csv")
    p = cn_month(mon["月份"])
    sc = pd.Series((mon["货币(M1)-同比增长"] - mon["货币和准货币(M2)-同比增长"]).to_numpy(), index=p).sort_index()
    X["M1M2_UP"] = monthly_avail(sc.index, (sc - sc.shift(3) > 0).astype(float).where(sc.shift(3).notna()).to_numpy())
    X["M1M2_POS"] = monthly_avail(sc.index, (sc > 0).astype(float).to_numpy())
    pmi = pd.read_csv(CI / "ak_pmi.csv")
    pp = pd.Series(pmi["制造业-指数"].to_numpy(), index=cn_month(pmi["月份"])).sort_index()
    X["PMI50"] = monthly_avail(pp.index, (pp >= 50).astype(float).to_numpy(), lag_day=2)
    pm3 = pp.rolling(3).mean()
    X["PMIUP"] = monthly_avail(pp.index, (pm3 > pm3.shift(1)).astype(float).where(pm3.shift(1).notna()).to_numpy(), lag_day=2)
    ppi = pd.read_csv(CI / "ak_ppi.csv")
    py = pd.Series(ppi["当月同比增长"].to_numpy(), index=cn_month(ppi["月份"])).sort_index()
    X["PPIUP"] = monthly_avail(py.index, (py - py.shift(3) > 0).astype(float).where(py.shift(3).notna()).to_numpy())
    br_ = pd.read_csv(CI / "ak_bond_rate.csv", parse_dates=["日期"]).set_index("日期")
    ts = (br_["中国国债收益率10年"] - br_["中国国债收益率2年"]).dropna()
    X["TERM_MED"] = daily_on(ts, lambda s: (s > s.rolling(252, min_periods=200).median()).astype(float))
    X["TERM_UP"] = daily_on(ts, lambda s: (s - s.shift(60) > 0).astype(float))
    fx = pd.read_csv(CI / "fred_DEXCHUS.csv", parse_dates=["observation_date"]).set_index("observation_date")["DEXCHUS"]
    fx = pd.to_numeric(fx, errors="coerce").dropna()
    X["CNY_UP"] = daily_on(fx, lambda s: (s / s.shift(60) - 1 < 0).astype(float))
    X["CNY_1P"] = daily_on(fx, lambda s: (s / s.shift(60) - 1 < 0.01).astype(float))
    msh = pd.read_csv(CI / "ak_margin_sh.csv", parse_dates=["日期"]).set_index("日期")["融资融券余额"]
    msz = pd.read_csv(CI / "ak_margin_sz.csv", parse_dates=["日期"]).set_index("日期")["融资融券余额"]
    idx = msh.index.union(msz.index)
    mt = msh.reindex(idx).ffill() + msz.reindex(idx).ffill()
    mt = mt.dropna()
    X["MARG_MA"] = daily_on(mt, lambda s: (s > s.rolling(60, min_periods=60).mean()).astype(float))
    X["MARG_20"] = daily_on(mt, lambda s: (s / s.shift(20) - 1 > 0).astype(float))
    for k in ["M1M2_UP", "M1M2_POS", "PMI50", "PMIUP", "PPIUP", "TERM_MED", "TERM_UP", "CNY_UP", "CNY_1P", "MARG_MA", "MARG_20"]:
        X[k] = pd.Series(X[k]).fillna(1.0).to_numpy()
    months = np.array([d.month for d in cal])
    X["months_ahead"] = np.r_[months[2:], months[-1], months[-1]]   # 乘数 t+2 生效
    X["mask"] = {s: G["masks"][s] for s in SEGS}
    X["cal"] = cal


# ------------------------------------------------------------------ 防御 arm 定义
# 每个 arm: dict(rule=VTX kwargs or None, stop=(mode, x), filt=[...], gate=[(kind, param, level)])
def A(**kw):
    return kw


ARMS = {"BASE": A()}
for f_ in (0.18, 0.20, 0.25):
    ARMS[f"VTF{int(f_*100)}"] = A(rule=dict(floor=f_))
ARMS["DVT15"] = A(rule=dict(down=True, target=0.15))
ARMS["DVT12"] = A(rule=dict(down=True, target=0.12))
ARMS["VTLOCK"] = A(rule=dict(lock=True))
for L in (0.15, 0.20, 0.25):
    ARMS[f"DD{int(L*100)}"] = A(rule=dict(vt=False, dd=L))
ARMS["VTDD20"] = A(rule=dict(dd=0.20))
for s in (0.08, 0.10, 0.12, 0.15):
    ARMS[f"TS{int(s*100):02d}"] = A(stop=s)
ARMS.update({"AM60_0": A(filt=["m60pos"]), "AM60_B": A(filt=["m60bond"]), "AM120_0": A(filt=["m120pos"]),
             "M20": A(filt=["m20pos"]), "ER10": A(filt=["er.1"]), "ER20": A(filt=["er.2"]), "ER30": A(filt=["er.3"]),
             "DDN10": A(filt=["dd.10"]), "DDN15": A(filt=["dd.15"])})
for t in (30, 40, 50):
    for lv in (0, 5):
        ARMS[f"BR{t}_{lv}"] = A(gate=[("breadth", t / 100, lv / 10)])
for n in (60, 120):
    for lv in (0, 5):
        ARMS[f"EWT{n}_{lv}"] = A(gate=[("ewma", n, lv / 10)])
ARMS.update({"NL10_5": A(gate=[("newlow", 0.10, 0.5)]), "NL20_5": A(gate=[("newlow", 0.20, 0.5)]),
             "NL20_0": A(gate=[("newlow", 0.20, 0.0)]), "COR80_5": A(gate=[("corr", 0, 0.5)]),
             "SDG_0": A(gate=[("SD300", 0, 0.0)]), "SDG_5": A(gate=[("SD300", 0, 0.5)]), "SDGE_0": A(gate=[("SDEW", 0, 0.0)])})
DEFENSE_ARMS = list(ARMS)
for k in ["M1M2_UP", "M1M2_POS", "PMI50", "PMIUP", "PPIUP", "TERM_MED", "TERM_UP", "CNY_UP", "CNY_1P", "MARG_MA", "MARG_20"]:
    ARMS[k] = A(gate=[("onoff", k, 0.0)])
ARMS["NHNL"] = A(gate=[("nhnl", 0, 0.0)])
ARMS["THRUST"] = A(gate=[("thrust", 0, 0.0)])
for m in range(1, 13):
    ARMS[f"OFF{m:02d}"] = A(gate=[("month", m, 0.0)])
ARMS["SIM5"] = A(gate=[("sim", 0, 0.5)])
INDICATOR_ARMS = [a for a in ARMS if a not in DEFENSE_ARMS]

BASES = {"S2+VT15": dict(sector=False, credit=False), "S2+CREDIT": dict(sector=False, credit=True),
         "S4": dict(sector=True, credit=False), "S4+CREDIT": dict(sector=True, credit=True)}


def family(a):
    for p, f in (("VTF", "VT"), ("DVT", "VT"), ("VTLOCK", "VT"), ("VTDD", "VT"), ("DD", "VT"), ("TS", "STOP"),
                 ("AM", "FILT"), ("M20", "FILT"), ("ER", "FILT"), ("DDN", "FILT"), ("BR", "BR"), ("EWT", "EWT"),
                 ("NL", "NL"), ("COR", "COR"), ("SDG", "SDG")):
        if a.startswith(p):
            return f
    return a.split("_")[0] if not a.startswith("OFF") else "SEAS"


def merge_arms(arms):
    out = dict(rule=None, stop=None, filt=[], gate=[])
    for a in arms:
        d = ARMS[a]
        if d.get("rule") is not None:
            out["rule"] = {**(out["rule"] or {}), **d["rule"]}
        if d.get("stop") is not None:
            out["stop"] = d["stop"]
        out["filt"] += d.get("filt", [])
        out["gate"] += d.get("gate", [])
    return out


def filt_mask(names, valid):
    m = pd.DataFrame(True, index=valid.index, columns=valid.columns)
    for nm in names:
        if nm == "m60pos":
            m &= ~(X["mom60"] < 0)
        elif nm == "m60bond":
            m &= ~(X["mom60"].lt(X["bond60"], axis=0))
        elif nm == "m120pos":
            m &= ~(X["mom120"] < 0)
        elif nm == "m20pos":
            m &= ~(X["mom20"] < 0)
        elif nm.startswith("er."):
            m &= ~(X["ser60"] < float("0" + nm[2:]))
        elif nm.startswith("dd."):
            m &= ~(X["dd60"] < -int(nm[3:]) / 100)
    return m


def gate_mult(gates, T):
    m = np.ones(T)
    for kind, p, lv in gates:
        if kind == "breadth":
            off = X["breadth"] < p
        elif kind == "ewma":
            ew = X["ew"]
            off = (ew < ew.rolling(p, min_periods=p).mean()).to_numpy()
        elif kind == "newlow":
            off = X["newlow"] > p
        elif kind == "corr":
            off = X["corr_hi"]
        elif kind in ("SD300", "SDEW"):
            off = X[kind]
        elif kind == "onoff":
            off = X[p] < 0.5
        elif kind == "nhnl":
            off = X["nhnl"] < 0
        elif kind == "thrust":
            off = X["thrust"] < 0.5   # 仅推力后 60 日开（极端，信息用）
        elif kind == "month":
            off = X["months_ahead"] == p
        elif kind == "sim":
            off = (X["months_ahead"] >= 5) & (X["months_ahead"] <= 10)
        m = m * np.where(off, lv, 1.0)
    return m


def sat(win, sector, credit, spec, cost, keep_ret=False):
    D = RC.data()
    f = I.feats(win, sector, "S2", ())
    if spec["filt"]:
        b = G["base_f"][win]
        valid = b["valid"]
        cand = valid & b["rm"].notna() & G["ma_ok"] & filt_mask(spec["filt"], valid)
        cv = cand.to_numpy()
        if sector:
            cv = I.cap2(b["rm"].where(valid).to_numpy(), cv)
        f["cand"] = pd.DataFrame(cv, index=valid.index, columns=valid.columns)
    cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=cost)
    if spec["stop"] is not None:
        cfg = replace(cfg, stop=spec["stop"], stop_mode="trail")
    sim = SE.simulate if spec["stop"] is not None else E.simulate
    r0 = sim(D["closeT"], f, cfg)
    f2 = dict(f); f2["base_ret"] = r0["ret"]
    rule = VTX(**spec["rule"]) if spec["rule"] is not None else E.VolTarget(0.15, 60, 40, every=10)
    rr = sim(D["closeT"], f2, replace(cfg, rule=rule))
    d = dict(ret=np.asarray(rr["ret"]), turn=np.asarray(rr["turn"]), expo=np.asarray(rr["expo"]), lo=f["start"])
    m = np.ones(len(d["ret"]))
    if credit:
        m = m * G["credit"]
    if spec["gate"]:
        m = m * gate_mult(spec["gate"], len(m))
    if credit or spec["gate"]:
        d = K.apply_mult(d, m, cost)
    return d


def sd_stats(ret, lo):
    out = {}
    r = np.asarray(ret)
    for k in ("SD300", "SDEW"):
        msk = X[k].copy()
        msk[:lo] = False
        rr = r[msk]
        out[f"{k}_cum"] = float(np.prod(1 + rr) - 1) if len(rr) else np.nan
        out[f"{k}_ann"] = float(np.prod(1 + rr) ** (TDY / max(len(rr), 1)) - 1) if len(rr) else np.nan
        out[f"{k}_days"] = int(len(rr))
    return out


def perf_rows(d, win, extra):
    rows = []
    m = RC.metr(d["ret"], d["lo"], None, d["turn"], d["expo"])
    row = {**extra, "window": win, **{k: m[k] for k in ("cagr", "mdd", "calmar", "sharpe", "vol", "turn_two_way", "avg_expo")}}
    if win == "W2013":
        row.update(sd_stats(d["ret"], d["lo"]))
    rows.append(row)
    if win == "W2013":
        for s in SEGS:
            m = RC.metr(d["ret"], d["lo"], X["mask"][s])
            rows.append({**extra, "window": s, **{k: m[k] for k in ("cagr", "mdd", "calmar", "sharpe", "vol")}})
    return rows


def task_single(args):
    setup()
    base, arm, cost, win = args
    b = BASES[base]
    d = sat(win, b["sector"], b["credit"], merge_arms([arm] if arm != "BASE" else []), cost)
    return perf_rows(d, win, {"base": base, "arm": arm, "cost_bp": int(round(cost * 1e4))})


def selfcheck():
    setup()
    for w in RC.START_W:
        a = sat(w, True, False, merge_arms([]), 0.0005)
        D = RC.data()
        f = I.feats(w, True, "S2", ())
        cfg = E.Config(k=5, reb=10, reb_mode="rolling", cost=0.0005)
        r1 = SE.simulate(D["closeT"], f, cfg)
        r2 = E.simulate(D["closeT"], f, cfg)
        ref = K.sat_run(RC.START_W[w], 0.0005, sector=True)
        log("selfcheck", w, float(np.abs(r1["ret"] - r2["ret"]).max()), float(np.abs(a["ret"] - ref["ret"]).max()))
        # VTX 默认 == VolTarget
        f2 = dict(f); f2["base_ret"] = r2["ret"]
        x1 = E.simulate(D["closeT"], f2, replace(cfg, rule=VTX()))["ret"]
        log("selfcheck VTX", w, float(np.abs(np.asarray(x1) - a["ret"]).max()))


def regime_report():
    setup()
    cal = X["cal"]
    rows = []
    for k in ("SD300", "SDEW"):
        s = pd.Series(X[k], index=cal)
        # 段：间隔 ≤10 日合并
        idx = np.flatnonzero(s.to_numpy())
        segs = []
        for i in idx:
            if segs and i - segs[-1][1] <= 10:
                segs[-1][1] = i
            else:
                segs.append([i, i])
        for a, b in segs:
            if cal[a] < pd.Timestamp("2013-01-01"):
                continue
            rows.append({"regime": k, "start": str(cal[a].date()), "end": str(cal[b].date()),
                         "days_flag": int(s.iloc[a:b + 1].sum())})
    seg = pd.DataFrame(rows)
    seg.to_csv(RUN / "sd_episodes.csv", index=False)
    yr = pd.DataFrame({k: pd.Series(X[k], index=cal).groupby(cal.year).mean() for k in ("SD300", "SDEW")})
    yr.to_csv(RUN / "sd_share_by_year.csv")
    return seg, yr


def main_single():
    t0 = time.time()
    tasks = [(b, a, c, w) for b in BASES for a in ARMS for c in COSTS for w in RC.START_W]
    rows = []
    with Pool(6, initializer=setup) as p:
        for k, r in enumerate(p.imap_unordered(task_single, tasks, chunksize=2)):
            rows += r
            if k % 200 == 0:
                log(k, len(tasks), round(time.time() - t0))
    df = pd.DataFrame(rows)
    df.to_csv(RUN / "single.csv.gz", index=False)
    return df


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "check":
        selfcheck()
    elif cmd == "single":
        setup()
        seg, yr = regime_report()
        log(len(seg), yr.round(2).to_string())
        main_single()


# ------------------------------------------------------------------ 第二阶段：组合
SAT_BASES = ["S4", "S4+CREDIT", "S2+CREDIT"]


def choose(df, base, topn=8):
    x = df[(df.base == base) & (df.cost_bp == 5) & df.window.isin(list(RC.START_W))]
    cal = x.pivot_table(index="arm", columns="window", values="calmar")[list(RC.START_W)]
    b = cal.loc["BASE"]
    avg = cal.mean(axis=1)
    wins = cal.gt(b, axis=1).sum(axis=1)
    ok = cal[(avg > avg["BASE"]) & (wins >= 2)].index
    best = {}
    for a in ok:
        fam = family(a)
        if fam not in best or avg[a] > avg[best[fam]]:
            best[fam] = a
    picks = sorted(best.values(), key=lambda a: -avg[a])[:topn]
    return picks, {a: dict(avg=float(avg[a]), wins=int(wins[a]), family=family(a)) for a in picks}


def compatible(arms):
    fams = [family(a) for a in arms]
    return len(set(fams)) == len(fams)


def task_combo_sat(args):
    setup()
    base, arms, cost, win = args
    b = BASES[base]
    d = sat(win, b["sector"], b["credit"], merge_arms(arms), cost)
    return base, tuple(arms), cost, win, d


def main_combo():
    setup()
    df = pd.read_csv(RUN / "single.csv.gz")
    sats, chosen = [], {}
    for base in SAT_BASES:
        picks, info = choose(df, base)
        chosen[base] = info
        log("chosen", base, info)
        sats.append((base, ()))
        for r in (1, 2, 3):
            for sub in itertools.combinations(picks, r):
                if compatible(sub):
                    sats.append((base, sub))
    json.dump(chosen, open(RUN / "chosen.json", "w"), ensure_ascii=False, indent=1)
    log("n sats", len(sats))
    tasks = [(b, a, c, w) for b, a in sats for c in COSTS for w in RC.START_W]
    SD = {}
    t0 = time.time()
    with Pool(6, initializer=setup) as p:
        for k, (b, a, c, w, d) in enumerate(p.imap_unordered(task_combo_sat, tasks, chunksize=2)):
            SD[(b, a, c, w)] = d
            if k % 300 == 0:
                log(k, len(tasks), round(time.time() - t0))
    rows, R13 = [], {}
    cores = {}
    for c in COSTS:
        for w in RC.START_W:
            lo0 = SD[(sats[0][0], (), c, w)]["lo"]
            for cn in ("DIV-IV", "C3"):
                cores[(cn, c, w)] = I.core(cn, lo0, c)
                rows += perf_rows(cores[(cn, c, w)], w, {"combo": cn, "sat": "-", "core": cn, "wc": 1.0, "cost_bp": int(round(c * 1e4))})
                if c == 0.0005 and w == "W2013":
                    R13[cn] = cores[(cn, c, w)]["ret"]
    for (b, a, c, w), d in SD.items():
        sk = b + "".join("+" + x for x in a)
        for cn in ("DIV-IV", "C3"):
            for wc in (0.0, 0.5, 0.6, 0.7, 0.8):
                if wc == 0.0 and cn == "C3":
                    continue
                md = RC.mix(cores[(cn, c, w)], d, wc, d["lo"], c) if wc > 0 else d
                nm = sk if wc == 0 else f"{cn}{int(wc*100)}/{sk}"
                rows += perf_rows(md, w, {"combo": nm, "sat": sk, "core": cn if wc else "-", "wc": wc,
                                          "cost_bp": int(round(c * 1e4))})
                if c == 0.0005 and w == "W2013":
                    R13[nm] = md["ret"]
    cdf = pd.DataFrame(rows)
    cdf.to_csv(RUN / "combos.csv.gz", index=False)
    np.savez_compressed(RUN / "_combo_ret_w2013_5bp.npz", names=np.array(list(R13)), R=np.array(list(R13.values()), dtype=np.float32))
    lo = G["base_f"]["W2013"]["start"]
    wf_picks, wf_m, s = walk_forward(R13, lo)
    wf_picks.to_csv(RUN / "walk_forward_picks.csv", index=False)
    fixed = {nm: RC.metr(R13[nm], s) for nm in ("C380/S4", "C360/S4+CREDIT", "S4", "S4+CREDIT") if nm in R13}
    pd.DataFrame({"WF": wf_m, **fixed}).T.to_csv(RUN / "walk_forward_perf.csv")
    log("WF", {k: round(v, 3) for k, v in wf_m.items() if isinstance(v, float)})


def walk_forward(R13, lo):
    cal = RC.data()["cal"]
    names = list(R13)
    R = np.array([R13[n] for n in names], dtype=float)
    out = np.zeros(R.shape[1])
    picks = []
    for y in range(2016, 2027):
        a = cal.searchsorted(pd.Timestamp(f"{y}-01-01"))
        b = cal.searchsorted(pd.Timestamp(f"{y+1}-01-01"))
        nav = np.cumprod(1 + R[:, lo:a], axis=1)
        yrs = (a - lo) / 244
        cagr = nav[:, -1] ** (1 / yrs) - 1
        mdd = (nav / np.maximum.accumulate(nav, axis=1) - 1).min(axis=1)
        c = np.where(mdd < 0, cagr / np.abs(mdd), 0)
        i = int(np.argmax(c))
        out[a:b] = R[i, a:b]
        picks.append({"year": y, "pick": names[i], "insample_calmar": round(float(c[i]), 3)})
    s = cal.searchsorted(pd.Timestamp("2016-01-01"))
    return pd.DataFrame(picks), RC.metr(out, s), s


def bench_sd():
    """基准在阴跌日的表现：池等权、沪深300。"""
    setup()
    cal = X["cal"]
    lo = G["base_f"]["W2013"]["start"]
    h3 = pd.read_csv(ROOT / "data/cache_index_proxy/H00300.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").close.sort_index()
    h3 = h3.reindex(cal.union(h3.index)).ffill().reindex(cal)
    out = {}
    for nm, r in (("EW池", X["ew"].pct_change().fillna(0).to_numpy()), ("沪深300TR", h3.pct_change().fillna(0).to_numpy())):
        out[nm] = sd_stats(r, lo)
    return pd.DataFrame(out).T


if __name__ == "__main__" and sys.argv[1] == "combo":
    main_combo()
    print(bench_sd().round(3).to_string())
    bench_sd().to_csv(RUN / "bench_sd.csv")


# ------------------------------------------------------------------ 第三阶段：贪心前向选择（全部 arm）
def _eval(base, arms, cost=0.0005, wc=None):
    setup()
    b = BASES[base]
    out = {}
    for w, sd in RC.START_W.items():
        d = sat(w, b["sector"], b["credit"], merge_arms(list(arms)), cost)
        if wc:
            d = RC.mix(I.core("C3", d["lo"], cost), d, wc, d["lo"], cost)
        out[w] = RC.metr(d["ret"], d["lo"])
    return out


def _score(res, obj):
    cal = np.mean([res[w]["calmar"] for w in RC.START_W])
    if obj == "calmar":
        return cal
    mdd = min(res[w]["mdd"] for w in RC.START_W)
    return res["W2013"]["cagr"] if mdd >= -0.15 else res["W2013"]["cagr"] - 1.0 + mdd   # 惩罚超线


def task_greedy(args):
    base, arms, wc = args
    return args, _eval(base, arms, wc=wc)


def main_greedy():
    setup()
    cands = [a for a in ARMS if a != "BASE"]
    rows = []
    with Pool(6, initializer=setup) as p:
        for base in SAT_BASES:
            for obj, wc in (("calmar", None), ("cagr_mdd15", None), ("calmar", 0.6), ("cagr_mdd15", 0.6)):
                cur, cur_s = (), None
                r0 = _eval(base, (), wc=wc)
                cur_s = _score(r0, obj)
                rows.append({"base": base, "obj": obj, "wc": wc or 0, "step": 0, "arms": "", "score": cur_s,
                             **{f"cal_{w}": r0[w]["calmar"] for w in RC.START_W}, "cagr13": r0["W2013"]["cagr"], "mdd13": r0["W2013"]["mdd"],
                             "mdd_min": min(r0[w]["mdd"] for w in RC.START_W)})
                for step in range(1, 5):
                    tasks = [(base, cur + (a,), wc) for a in cands if a not in cur and compatible(cur + (a,))]
                    res = p.map(task_greedy, tasks)
                    best = max(res, key=lambda t: _score(t[1], obj))
                    s = _score(best[1], obj)
                    if s <= cur_s + 1e-4:
                        break
                    cur, cur_s = best[0][1], s
                    r = best[1]
                    rows.append({"base": base, "obj": obj, "wc": wc or 0, "step": step, "arms": "+".join(cur), "score": s,
                                 **{f"cal_{w}": r[w]["calmar"] for w in RC.START_W}, "cagr13": r["W2013"]["cagr"], "mdd13": r["W2013"]["mdd"],
                                 "mdd_min": min(r[w]["mdd"] for w in RC.START_W)})
                    log("greedy", base, obj, wc, step, cur, round(s, 4))
    g = pd.DataFrame(rows)
    g.to_csv(RUN / "greedy.csv", index=False)
    return g


PERIODS = {"2015H2": ("2015-06-15", "2015-12-31"), "2016": ("2016-01-01", "2016-12-31"), "2017": ("2017-01-01", "2017-12-31"),
           "2018": ("2018-01-01", "2018-12-31"), "2021-02→2024-02": ("2021-02-18", "2024-02-02"), "2023": ("2023-01-01", "2023-12-31")}
KEY = [("S2+VT15", ()), ("S2+CREDIT", ()), ("S4", ()), ("S4+CREDIT", ()), ("S4", ("COR80_5",)), ("S4", ("VTLOCK",)),
       ("S4", ("EWT60_5",)), ("S4", ("TS10",)), ("S4", ("CNY_1P",)), ("S4+CREDIT", ("TS12",)), ("S4+CREDIT", ("CNY_1P",)),
       ("S4+CREDIT", ("COR80_5",)), ("S4+CREDIT", ("VTF20",)), ("S4+CREDIT", ("DVT12",)), ("S4+CREDIT", ("BR40_5",)),
       ("S4+CREDIT", ("SDG_5",)), ("S2+CREDIT", ("TS10",))]


def period_table():
    setup()
    cal = X["cal"]
    rows = []
    lo = G["base_f"]["W2013"]["start"]
    h3 = pd.read_csv(ROOT / "data/cache_index_proxy/H00300.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").close.sort_index()
    h3 = h3.reindex(cal.union(h3.index)).ffill().reindex(cal)
    series = {"EW池": X["ew"].pct_change().fillna(0).to_numpy(), "沪深300TR": h3.pct_change().fillna(0).to_numpy()}
    for base, arms in KEY:
        b = BASES[base]
        d = sat("W2013", b["sector"], b["credit"], merge_arms(list(arms)), 0.0005)
        series[base + "".join("+" + a for a in arms)] = d["ret"]
    for nm, r in series.items():
        row = {"策略": nm}
        for p, (a, bb) in PERIODS.items():
            m = np.asarray((cal >= pd.Timestamp(a)) & (cal <= pd.Timestamp(bb)))
            m[:lo] = False
            row[p] = float(np.prod(1 + r[m]) - 1)
            nav = np.cumprod(1 + r[m])
            row[p + "_mdd"] = float((nav / np.maximum.accumulate(nav) - 1).min())
        row.update(sd_stats(r, lo))
        rows.append(row)
    t = pd.DataFrame(rows)
    t.to_csv(RUN / "period_table.csv", index=False)
    return t


if __name__ == "__main__" and sys.argv[1] == "greedy":
    print(period_table().round(3).to_string())
    main_greedy()
