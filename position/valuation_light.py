"""VAL-01：估值分位当总敞口"信号灯"，叠在 S2+VT15 上。

规则：敞口 = min(波动目标15%, 估值灯)，灯来自沪深300 PE(TTM) 的 10 年滚动分位。
预登记：runs/20261113_VAL-01_valuation_light/PREREG.md
用法：
  python position/valuation_light.py                 # ext + long
  python position/valuation_light.py --pool ext
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

from position.engine import (  # noqa: E402
    Config, Fixed, MinRule, Rule, VolTarget, features, monthly, perf, simulate,
    slice_metrics, stationary_bootstrap_ci, yearly_table, base_returns,
)

COST = 0.0005
CACHE_2019 = os.path.join(ROOT, "data", "cache2019")
CACHE_2007 = os.path.join(ROOT, "data", "cache2007")
PE_CSV = os.path.join(ROOT, "data", "cache_4pct", "pe_沪深300.csv")
OUT = os.path.join(ROOT, "runs", f"{date.today()}_VAL-01_valuation_light")

TIERS_MILD = ((20, 1.00), (50, 0.85), (80, 0.60), (999, 0.35))
TIERS_HARD = ((20, 1.00), (50, 0.70), (80, 0.40), (999, 0.20))
TIERS_RED = ((50, 1.00), (80, 1.00), (999, 0.50))
TIERS_TGT = ((20, 0.20), (50, 0.15), (80, 0.12), (999, 0.08))   # 灯的另一种用法：直接调波动目标


# ---------------------------------------------------------------- 数据
def load_panel_any(pool: str) -> pd.DataFrame:
    from pools import get_pool
    from macd_structure import ETF_POOL

    codes = list(get_pool(pool, ETF_POOL))
    cache = CACHE_2019 if pool in ("base", "ext") else CACHE_2007
    closes = {}
    for code in codes:
        for name in (f"{code}.csv", f"idx_{code}_20070101.csv"):
            path = os.path.join(cache, name)
            if os.path.exists(path):
                df = pd.read_csv(path, parse_dates=["date"])
                col = "close" if "close" in df.columns else "收盘"
                closes[code] = df.set_index("date")[col].astype(float)
                break
    close = pd.DataFrame(closes).sort_index()
    return close.loc[:, close.notna().sum() >= 300]


def valuation_pct(index: pd.DatetimeIndex, shift_days: int = 0) -> np.ndarray:
    """沪深300 PE(TTM) 10 年滚动分位（0~100），按交易日 ffill；shift_days 用于安慰剂。"""
    pe = pd.read_csv(PE_CSV)
    pe["date"] = pd.to_datetime(pe["日期"])
    pe = pe.dropna(subset=["滚动市盈率"]).sort_values("date").reset_index(drop=True)
    v = pe["滚动市盈率"].to_numpy(float)
    pct = np.full(len(v), np.nan)
    for i in range(len(v)):
        lo = max(0, i - 119)
        h = v[lo : i + 1]
        if len(h) >= 24:
            pct[i] = (h <= v[i]).mean() * 100.0
    s = pd.Series(pct, index=pe["date"])
    out = s.reindex(index, method="ffill").to_numpy(float)
    if shift_days:
        out = np.roll(out, shift_days)
        # 平移后前 shift 根可能越界，用最近的可用值补
        if shift_days > 0:
            out[:shift_days] = out[shift_days]
        else:
            out[shift_days:] = out[shift_days - 1]
    return out


# ---------------------------------------------------------------- 规则
class ValLight(Rule):
    """估值灯：分位越低越放手，越高越收手。tiers 为 (分位上界, 敞口乘子)。"""

    def __init__(self, pct, tiers, name="估值灯"):
        self.pct = np.asarray(pct, float)
        self.tiers = sorted(tiers)
        self.name = name

    def scale(self, j, ctx):
        x = self.pct[j] if j < len(self.pct) else np.nan
        if not np.isfinite(x):
            return 1.0
        for thr, s in self.tiers:
            if x <= thr:
                return float(s)
        return float(self.tiers[-1][1])


class RegimeVT(Rule):
    """灯的另一种用法：不乘在敞口上，而是按档位改波动目标（绿档放大、红档收缩）。"""

    requires_base = True

    def __init__(self, pct, tiers, window=60, min_periods=40, cap=1.0, name="RegimeVT"):
        self.pct = np.asarray(pct, float)
        self.tiers = sorted(tiers)
        self.window, self.min_periods, self.cap, self.name = window, min_periods, cap, name

    def _target(self, j):
        x = self.pct[j] if j < len(self.pct) else np.nan
        if not np.isfinite(x):
            return 0.15
        for thr, t in self.tiers:
            if x <= thr:
                return float(t)
        return float(self.tiers[-1][1])

    def scale(self, j, ctx):
        src = ctx.get("base_rets")
        if src is None:
            raise ValueError("RegimeVT 需要 ctx['base_rets']")
        lo = max(int(ctx.get("start", 0)), j - self.window + 1)
        r = src[lo : j + 1]
        if len(r) < max(self.min_periods, 20):
            return 1.0
        sd = float(np.nanstd(r, ddof=1)) * np.sqrt(252)
        if not np.isfinite(sd) or sd <= 1e-8:
            return 1.0
        return float(np.clip(self._target(j) / sd, 0.0, self.cap))


def variants(pct):
    return [
        ("S2", "S2 满仓基线", lambda: Config(rule=Fixed(1.0))),
        ("VT15", "VT15（在跑的那套）", lambda: Config(rule=VolTarget(0.15, every=1))),
        ("L1", "VT15+灯L1(温和)", lambda: Config(rule=MinRule(
            [VolTarget(0.15, every=1), ValLight(pct, TIERS_MILD)], "VT15+灯L1"))),
        ("L2", "VT15+灯L2(激进)", lambda: Config(rule=MinRule(
            [VolTarget(0.15, every=1), ValLight(pct, TIERS_HARD)], "VT15+灯L2"))),
        ("L3", "VT15+灯L3(只砍红灯)", lambda: Config(rule=MinRule(
            [VolTarget(0.15, every=1), ValLight(pct, TIERS_RED)], "VT15+灯L3"))),
        ("L4", "灯调波动目标(绿20/黄15/橙12/红8)", lambda: Config(
            rule=RegimeVT(pct, TIERS_TGT, name="RegimeVT"))),
        ("LONLY", "只用灯(不加VT)", lambda: Config(rule=ValLight(pct, TIERS_MILD))),
    ]


def pctf(x):
    return "" if x is None or not np.isfinite(x) else f"{x * 100:.2f}%"


def run_pool(pool, log):
    close = load_panel_any(pool)
    f = features(close)
    start, T = f["start"], len(close)
    idx = close.index
    pct = valuation_pct(idx)
    pct_sh = {k: valuation_pct(idx, k) for k in (252, -252, 504, -504)}

    f["base_ret"] = base_returns(close, f, Config(cost=COST))
    log(f"\n{'=' * 78}\n池 {pool}: {close.shape[1]} 只, {idx[0].date()}~{idx[-1].date()}, "
        f"可投起点 {idx[start].date()} ({T - start} 天)")

    base = simulate(close, f, Config(cost=COST, rule=Fixed(1.0)))
    bm = slice_metrics(base, start, T)
    log(f"基线自检: 年化 {bm['cagr']:.2%} 回撤 {bm['mdd']:.2%} 卡玛 {bm['calmar']:.3f} "
        f"换手 {bm['turn_two_way']:.0%}")

    # 灯的分档天数统计（可投区间内）
    p = pct[start:]
    tiers_share = {
        "绿<=20%": float(np.nanmean(p <= 20)),
        "黄20-50%": float(np.nanmean((p > 20) & (p <= 50))),
        "橙50-80%": float(np.nanmean((p > 50) & (p <= 80))),
        "红>80%": float(np.nanmean(p > 80)),
    }
    log("灯档占比: " + "  ".join(f"{k} {v:.0%}" for k, v in tiers_share.items()))

    n_live = T - start
    bounds = [(start, start + int(n_live * 0.5)),
              (start + int(n_live * 0.5), start + int(n_live * 0.75)),
              (start + int(n_live * 0.75), T)]

    rows, subs, eq_data, runs, ctrls = [], [], {"date": idx[start:]}, {}, {}
    ms = {}
    for key, label, fac in variants(pct):
        cfg = fac()
        cfg.cost = COST
        res = simulate(close, f, cfg)
        runs[key] = res
        m = slice_metrics(res, start, T)
        ms[key] = m

        c = float(np.clip(m["avg_expo"], 0.0, 1.0))
        ctl = simulate(close, f, Config(cost=COST, rule=Fixed(c)))
        ctrls[key] = ctl
        cm = slice_metrics(ctl, start, T)

        rows.append({
            "池": pool, "机制": label, "key": key,
            "年化": pctf(m["cagr"]), "波动": pctf(m["vol"]), "夏普": round(m["sharpe"], 2),
            "回撤": pctf(m["mdd"]), "卡玛": round(m["calmar"], 2),
            "平均仓位": f"{m['avg_expo']:.0%}", "双边换手": f"{m['turn_two_way']:.0%}",
            "成本拖累pp": round(m["cost_drag"] * 100, 2),
            "固定同仓位年化": pctf(cm["cagr"]), "固定同仓位卡玛": round(cm["calmar"], 2),
            "卡玛-固定": round(m["calmar"] - cm["calmar"], 2),
            "夏普-固定": round(m["sharpe"] - cm["sharpe"], 2),
        })
        for name, (lo, hi) in zip(("训练", "验证", "测试"), bounds):
            sm = slice_metrics(res, lo, hi)
            scm = slice_metrics(simulate(close, f, Config(cost=COST, rule=Fixed(
                float(np.clip(sm["avg_expo"], 0, 1))))), lo, hi)
            subs.append({"池": pool, "机制": label, "段": name, "年化": pctf(sm["cagr"]),
                         "回撤": pctf(sm["mdd"]), "卡玛": round(sm["calmar"], 2),
                         "平均仓位": f"{sm['avg_expo']:.0%}",
                         "同仓位固定卡玛": round(scm["calmar"], 2),
                         "卡玛-固定": round(sm["calmar"] - scm["calmar"], 2)})
        eq_data[key] = res["ret"][start:]
        log(f"  {label:<22} 年化{rows[-1]['年化']:>8} 夏普{rows[-1]['夏普']:>5} 回撤{rows[-1]['回撤']:>8} "
            f"卡玛{rows[-1]['卡玛']:>5} 仓位{rows[-1]['平均仓位']:>4} 换手{rows[-1]['双边换手']:>5} "
            f"卡玛-固定{rows[-1]['卡玛-固定']:>6}")

    df = pd.DataFrame(rows)
    v15 = ms["VT15"]
    df["回撤改善vsVT15_pp"] = [round((ms[k]["mdd"] - v15["mdd"]) * 100, 1) for k in df["key"]]
    df["年化vsVT15_pp"] = [round((ms[k]["cagr"] - v15["cagr"]) * 100, 2) for k in df["key"]]
    df["卡玛vsVT15"] = [round(ms[k]["calmar"] - v15["calmar"], 2) for k in df["key"]]
    df.to_csv(os.path.join(OUT, f"summary_{pool}.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(subs).to_csv(os.path.join(OUT, f"subperiod_{pool}.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(eq_data).to_csv(os.path.join(OUT, f"equity_{pool}.csv"), index=False, encoding="utf-8-sig")

    yt = []
    for key in ("VT15", "L1", "L3"):
        for _, r in yearly_table(runs[key], idx, start).iterrows():
            yt.append({"池": pool, "机制": dict((k, l) for k, l, _ in variants(pct))[key],
                       **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.to_dict().items()}})
    pd.DataFrame(yt).to_csv(os.path.join(OUT, f"yearly_{pool}.csv"), index=False, encoding="utf-8-sig")

    # bootstrap：vs VT15，vs 同平均仓位固定
    boot = []
    for key in ("L1", "L2", "L3", "L4", "LONLY"):
        ma = monthly(runs[key]["ret"], idx, start)
        for ctl_key, cname, ctl in (("VT15", "VT15 基线", runs["VT15"]),
                                    ("FIXED", "同平均仓位固定", ctrls[key])):
            mb = monthly(ctl["ret"], idx, start)
            ci = stationary_bootstrap_ci(ma, mb)
            boot.append({"池": pool, "对比": f"{key} vs {cname}",
                         "Sharpe差_5%": round(ci["sharpe_lo"], 3),
                         "Sharpe差_50%": round(ci["sharpe_med"], 3),
                         "Sharpe差_95%": round(ci["sharpe_hi"], 3),
                         "CAGR差_5%": pctf(ci["cagr_lo"]), "CAGR差_50%": pctf(ci["cagr_med"]),
                         "CAGR差_95%": pctf(ci["cagr_hi"])})
    bt = pd.DataFrame(boot)
    bt.to_csv(os.path.join(OUT, f"bootstrap_{pool}.csv"), index=False, encoding="utf-8-sig")
    log("\n[bootstrap 月度 stationary, 90% 区间]")
    log(bt.to_string(index=False))

    return {"close": close, "f": f, "start": start, "T": T, "summary": df, "subs": pd.DataFrame(subs),
            "runs": runs, "ctrls": ctrls, "pct": pct, "tiers_share": tiers_share, "bounds": bounds}


def diagnostics(ctx, pool, log):
    """灯到底在干什么：与 VT15 敞口的相关性、触发比例、高波动日里的乘子。"""
    start = ctx["start"]
    pct = ctx["pct"]
    lamp = np.array([ValLight(pct, TIERS_MILD).scale(j, {}) for j in range(start, ctx["T"])])
    expo_vt = np.asarray(ctx["runs"]["VT15"]["expo"][start:], float)
    bind = float(np.mean(lamp < expo_vt - 1e-9))
    corr = float(np.corrcoef(lamp, expo_vt)[0, 1])
    hi = expo_vt > 0.90
    info = {"池": pool, "灯平均乘子": round(float(lamp.mean()), 3),
            "VT15平均敞口": round(float(expo_vt.mean()), 3),
            "灯收紧的日子占比": f"{bind:.0%}",
            "灯乘子与VT15敞口相关": round(corr, 2),
            "VT15满仓日的灯乘子均值": round(float(lamp[hi].mean()) if hi.any() else float("nan"), 3),
            "VT15满仓日占比": f"{hi.mean():.0%}"}
    log(f"\n[诊断 {pool}] " + "  ".join(f"{k}={v}" for k, v in list(info.items())[1:]))
    return info


# ---------------------------------------------------------------- 稳健性
def robustness(ctx, pool, log):
    close, f, start, T = ctx["close"], ctx["f"], ctx["start"], ctx["T"]
    idx = close.index
    out = []

    def one(key, label, cfg):
        cfg.cost = COST
        r = simulate(close, f, cfg)
        m = slice_metrics(r, start, T)
        c = float(np.clip(m["avg_expo"], 0, 1))
        cm = slice_metrics(simulate(close, f, Config(cost=COST, rule=Fixed(c))), start, T)
        return {"池": pool, "变体": key, "说明": label, "年化": pctf(m["cagr"]),
                "回撤": pctf(m["mdd"]), "卡玛": round(m["calmar"], 2),
                "夏普": round(m["sharpe"], 2), "平均仓位": f"{m['avg_expo']:.0%}",
                "卡玛-固定": round(m["calmar"] - cm["calmar"], 2)}

    pct = ctx["pct"]
    for name, tiers in (("扰动阈值15/45/75", ((15, 1.0), (45, 0.85), (75, 0.6), (999, 0.35))),
                        ("扰动阈值25/55/85", ((25, 1.0), (55, 0.85), (85, 0.6), (999, 0.35))),
                        ("扰动乘子-0.1", ((20, 0.9), (50, 0.75), (80, 0.5), (999, 0.25))),
                        ("扰动乘子+0.1", ((20, 1.0), (50, 0.95), (80, 0.7), (999, 0.45)))):
        out.append(one(name, name, Config(rule=MinRule(
            [VolTarget(0.15, every=1), ValLight(pct, tiers)], name))))

    for k, tag in ((252, "平移+1年"), (-252, "平移-1年"), (504, "平移+2年"), (-504, "平移-2年")):
        p = valuation_pct(idx, k)
        out.append(one(f"安慰剂{tag}", f"分位序列{tag}", Config(rule=MinRule(
            [VolTarget(0.15, every=1), ValLight(p, TIERS_MILD)], tag))))

    df = pd.DataFrame(out)
    df.to_csv(os.path.join(OUT, f"robust_{pool}.csv"), index=False, encoding="utf-8-sig")
    log(f"\n[稳健性 {pool}]")
    log(df.to_string(index=False))
    return df


def cost_sens(ctx, pool, log):
    close, f, start, T = ctx["close"], ctx["f"], ctx["start"], ctx["T"]
    rows = []
    for c in (0.0002, 0.0005, 0.0010, 0.0015):
        f2 = dict(f)
        f2["base_ret"] = base_returns(close, f, Config(cost=c))
        for key, fac in (("VT15", lambda: VolTarget(0.15, every=1)),
                         ("L1", lambda: MinRule([VolTarget(0.15, every=1), ValLight(ctx["pct"], TIERS_MILD)], "L1")),
                         ("L3", lambda: MinRule([VolTarget(0.15, every=1), ValLight(ctx["pct"], TIERS_RED)], "L3"))):
            cfg = Config(cost=c, rule=fac())
            m = slice_metrics(simulate(close, f2, cfg), start, T)
            rows.append({"池": pool, "成本bp": c * 1e4, "变体": key, "年化": pctf(m["cagr"]),
                         "卡玛": round(m["calmar"], 2), "回撤": pctf(m["mdd"]),
                         "换手": f"{m['turn_two_way']:.0%}"})
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, f"cost_{pool}.csv"), index=False, encoding="utf-8-sig")
    log(f"\n[成本敏感性 {pool}]")
    log(df.to_string(index=False))
    return df


# ---------------------------------------------------------------- 判据
def criteria(ctx, pool, log):
    df, subs = ctx["summary"], ctx["subs"]
    rows = []
    base = df.set_index("key")
    v15 = base.loc["VT15"]
    for key in ("L1", "L2", "L3", "L4"):
        r, sb = base.loc[key], subs[subs["机制"] == base.loc[key, "机制"]]
        c1 = float(r["卡玛-固定"]) >= 0.05
        c2 = float(r["回撤改善vsVT15_pp"]) >= 3.0
        c3 = float(r["年化vsVT15_pp"]) >= -2.0
        c4 = int((sb["卡玛-固定"].astype(float) > 0).sum()) >= 2
        c6 = float(str(r["双边换手"]).rstrip("%")) <= float(str(v15["双边换手"]).rstrip("%")) * 1.30
        rows.append({"池": pool, "变体": key, "C1卡玛-固定≥0.05": "PASS" if c1 else "FAIL",
                     "C2回撤改善≥3pp": "PASS" if c2 else "FAIL", "C3年化≥VT15-2pp": "PASS" if c3 else "FAIL",
                     "C4三段≥2段": "PASS" if c4 else "FAIL", "C6换手≤+30%": "PASS" if c6 else "FAIL",
                     "卡玛-固定": round(float(r["卡玛-固定"]), 2),
                     "回撤改善pp": float(r["回撤改善vsVT15_pp"]),
                     "年化vsVT15pp": float(r["年化vsVT15_pp"]),
                     "卡玛vsVT15": float(r["卡玛vsVT15"]),
                     "三段胜出": int((sb["卡玛-固定"].astype(float) > 0).sum())})
    out = pd.DataFrame(rows)
    log(f"\n[判据 {pool}]（C5 见 bootstrap 表）")
    log(out.to_string(index=False))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="ext,long")
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    log_lines = []

    def log(*args):
        s = " ".join(str(x) for x in args)
        print(s)
        log_lines.append(s)

    log(f"VAL-01 估值灯 · {pd.Timestamp.now().isoformat(timespec='seconds')} · 成本 {COST * 1e4:.0f}bp")
    crit_all, summaries = [], []
    meta = {"timestamp": str(pd.Timestamp.now()), "cost_bps": COST * 1e4, "pools": {}}
    for pool in [p.strip() for p in a.pool.split(",") if p.strip()]:
        ctx = run_pool(pool, log)
        summaries.append(ctx["summary"])
        robustness(ctx, pool, log)
        cost_sens(ctx, pool, log)
        crit_all.append(criteria(ctx, pool, log))
        diag = diagnostics(ctx, pool, log)
        idx = ctx["close"].index
        meta["pools"][pool] = {
            **diag,
            "n_codes": int(ctx["close"].shape[1]),
            "start": str(idx[ctx["start"]].date()), "end": str(idx[-1].date()),
            "tiers_share": ctx["tiers_share"],
            "avg_expo": {k: round(float(np.mean(ctx["runs"][k]["expo"][ctx["start"]:])), 4)
                         for k in ("VT15", "L1", "L2", "L3", "L4")},
        }
    pd.concat(crit_all, ignore_index=True).to_csv(os.path.join(OUT, "criteria_all.csv"),
                                                  index=False, encoding="utf-8-sig")
    pd.concat(summaries, ignore_index=True).to_csv(os.path.join(OUT, "summary_all.csv"),
                                                   index=False, encoding="utf-8-sig")
    meta["code_sha256_16"] = {p: hashlib.sha256(open(os.path.join(ROOT, "position", p), "rb").read()).hexdigest()[:16]
                              for p in ("engine.py", "valuation_light.py")}
    with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "console.log"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(log_lines))
    print(f"\n结果写入 {OUT}")


if __name__ == "__main__":
    main()
