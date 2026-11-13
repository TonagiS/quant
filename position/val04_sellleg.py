"""VAL-04：拆开用——买入腿换成 VT15，卖出腿保留"分位>80 且持续 3 个月才分批减仓"。

与 VAL-01/03 的区别：
  * 买入腿不再看估值（VT15 决定敞口），只有卖出腿用估值；
  * 卖出腿加"持续性"过滤（分位 >=80 连续 60 个交易日才启动），并带滞回（<70 才慢慢加回），
    目的就是躲开"偏高区间来回打折"的抖动。
池：long+stocks（37 只，2007 起，最长样本）/ long（15 只指数）/ ext（52 只 ETF，实盘口径）。

产物：runs/2026-11-13_VAL-04_sellleg/
"""
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

from position.engine import (  # noqa: E402
    Config, Fixed, MinRule, Rule, VolTarget, base_returns, features, monthly,
    simulate, slice_metrics, stationary_bootstrap_ci,
)
from position.valuation_light import pctf, valuation_pct  # noqa: E402

OUT = os.path.join(ROOT, "runs", "2026-11-13_VAL-04_sellleg")
os.makedirs(OUT, exist_ok=True)
COST = 0.0005
CACHE_2019 = os.path.join(ROOT, "data", "cache2019")
CACHE_2007 = os.path.join(ROOT, "data", "cache2007")


# ---------------------------------------------------------------- 数据
def load_panel(pool: str) -> pd.DataFrame:
    """支持 long+stocks 这样的复合池：指数走 idx_*，个股走 stk_*_hfq_*。"""
    from pools import get_pool
    from macd_structure import ETF_POOL

    codes = list(get_pool(pool, ETF_POOL))
    closes = {}
    for code in codes:
        pats = [("cache2019", f"{code}.csv"), ("cache2007", f"idx_{code}_20070101.csv"),
                ("cache2007", f"stk_{code}_hfq_20070101.csv")]
        for d, name in pats:
            path = os.path.join(CACHE_2019 if d == "cache2019" else CACHE_2007, name)
            if os.path.exists(path):
                df = pd.read_csv(path, parse_dates=["date"])
                col = "close" if "close" in df.columns else "收盘"
                closes[code] = df.set_index("date")[col].astype(float)
                break
    close = pd.DataFrame(closes).sort_index()
    return close.loc[:, close.notna().sum() >= 300]


# ---------------------------------------------------------------- 卖出腿
def sell_leg(pct, trigger=80.0, sustain=60, step=0.25, every=20, restore=70.0, restore_step=0.25):
    """返回逐日乘子（0~1）：高估持续够久 -> 分批减；分位回到 restore 以下 -> 分批加回。"""
    m, run_len, last_act = 1.0, 0, -10**9
    out = []
    for i, q in enumerate(pct):
        if np.isfinite(q):
            run_len = run_len + 1 if q >= trigger else 0
        if np.isfinite(q) and run_len >= sustain and (i - last_act) >= every and m > 0:
            m = max(0.0, m - step)
            last_act = i
        elif np.isfinite(q) and q < restore and (i - last_act) >= every and m < 1.0:
            m = min(1.0, m + restore_step)
            last_act = i
        out.append(m)
    return np.asarray(out)


class Series(Rule):
    def __init__(self, s, name="卖出腿"):
        self.s = np.asarray(s, float)
        self.name = name

    def scale(self, j, ctx):
        return float(self.s[j]) if 0 <= j < len(self.s) else 1.0


def main():
    log = []

    def p(*a):
        s = " ".join(str(x) for x in a)
        print(s)
        log.append(s)

    p(f"VAL-04 买卖拆开 · {pd.Timestamp.now().isoformat(timespec='seconds')} · 成本 {COST*1e4:.0f}bp")
    rows_all, boot_all, crit_all, meta = [], [], [], {"pools": {}}

    for pool in ("long+stocks", "long", "ext"):
        close = load_panel(pool)
        f = features(close)
        start, T = f["start"], len(close)
        idx = close.index
        f["base_ret"] = base_returns(close, f, Config(cost=COST))
        pct = valuation_pct(idx)

        legs = {
            "S1(80/60日/25%)": sell_leg(pct, 80.0, 60, 0.25, 20, 70.0),
            "S2(85/60日/33%)": sell_leg(pct, 85.0, 60, 0.33, 20, 75.0),
            "S3(80/40日/20%)": sell_leg(pct, 80.0, 40, 0.20, 10, 75.0),
        }
        p(f"\n===== {pool}：{close.shape[1]} 只，{idx[0].date()}~{idx[-1].date()}，"
          f"可投起点 {idx[start].date()}（{T-start} 天）=====")
        seg = pct[start:]
        p("分位分布: " + "  ".join(f"{k} {np.nanmean(v):.0%}" for k, v in
                                (("<=20", seg <= 20), ("20-50", (seg > 20) & (seg <= 50)),
                                 ("50-80", (seg > 50) & (seg <= 80)), (">80", seg > 80))))
        for nm, lg in legs.items():
            p(f"  卖出腿 {nm}: 平均乘子 {lg[start:].mean():.0%}  触发日占比 {np.mean(lg[start:] < 0.999):.0%}")

        cfgs = [("VT15", "VT15（基线）", lambda: Config(rule=VolTarget(0.15, every=1)))]
        for nm, lg in legs.items():
            cfgs.append((f"SL_{nm[:2]}", f"VT15 × 卖出腿{nm}", lambda lg=lg, nm=nm: Config(
                rule=MinRule([VolTarget(0.15, every=1), Series(lg, nm)], f"VT15×{nm}"))))
        cfgs.append(("RED", "VT15 × 简单红灯(>80 直接半仓)", lambda: Config(rule=MinRule(
            [VolTarget(0.15, every=1), Series(np.where(np.nan_to_num(pct, nan=50) > 80, 0.5, 1.0), "RED")],
            "VT15×红灯"))))
        cfgs.append(("S2", "S2 满仓", lambda: Config(rule=Fixed(1.0))))

        runs, ctrls, rows, ms = {}, {}, [], {}
        for key, label, fac in cfgs:
            cfg = fac()
            cfg.cost = COST
            res = simulate(close, f, cfg)
            runs[key] = res
            m = slice_metrics(res, start, T)
            ms[key] = m
            c = float(np.clip(m["avg_expo"], 0, 1))
            ctl = simulate(close, f, Config(cost=COST, rule=Fixed(c)))
            ctrls[key] = ctl
            cm = slice_metrics(ctl, start, T)
            rows.append({"池": pool, "机制": label, "key": key, "年化": pctf(m["cagr"]),
                         "夏普": round(m["sharpe"], 2), "回撤": pctf(m["mdd"]),
                         "卡玛": round(m["calmar"], 2), "平均仓位": f"{m['avg_expo']:.0%}",
                         "换手": f"{m['turn_two_way']:.0%}", "卡玛-固定": round(m["calmar"] - cm["calmar"], 2),
                         "年化vsVT15pp": round((m["cagr"] - ms["VT15"]["cagr"]) * 100, 2)
                         if "VT15" in ms else 0.0,
                         "回撤改善pp": round((m["mdd"] - ms["VT15"]["mdd"]) * 100, 1)
                         if "VT15" in ms else 0.0})
            p(f"  {label:<26} 年化{rows[-1]['年化']:>8} 夏普{rows[-1]['夏普']:>5} 回撤{rows[-1]['回撤']:>8} "
              f"卡玛{rows[-1]['卡玛']:>5} 仓位{rows[-1]['平均仓位']:>4} 卡玛-固定{rows[-1]['卡玛-固定']:>6}")
        tbl = pd.DataFrame(rows)
        tbl.to_csv(os.path.join(OUT, f"summary_{pool.replace('+', '_')}.csv"), index=False, encoding="utf-8-sig")
        rows_all.append(tbl)

        n_live = T - start
        bounds = [(start, start + int(n_live * .5)), (start + int(n_live * .5), start + int(n_live * .75)),
                  (start + int(n_live * .75), T)]
        for key in ("SL_S1", "SL_S2", "SL_S3"):
            segs = []
            for nm, (lo, hi) in zip(("训练", "验证", "测试"), bounds):
                a, b = slice_metrics(runs[key], lo, hi), slice_metrics(runs["VT15"], lo, hi)
                segs.append(f"{nm} 卡玛{a['calmar']:.2f}(基准{b['calmar']:.2f})")
            p(f"  [{key}] " + "  ".join(segs))
            base = tbl.set_index("key").loc[key]
            crit_all.append({"池": pool, "变体": key,
                             "C1卡玛-固定≥0.05": "PASS" if float(base["卡玛-固定"]) >= 0.05 else "FAIL",
                             "C2回撤≥3pp": "PASS" if float(base["回撤改善pp"]) >= 3 else "FAIL",
                             "C3年化≥-2pp": "PASS" if float(base["年化vsVT15pp"]) >= -2 else "FAIL",
                             "C4三段≥2段": "PASS" if sum(
                                 1 for nm, (lo, hi) in zip("abc", bounds)
                                 if slice_metrics(runs[key], lo, hi)["calmar"] >
                                 slice_metrics(runs["VT15"], lo, hi)["calmar"]) >= 2 else "FAIL",
                             "C6换手≤+30%": "PASS" if float(base["换手"].rstrip("%")) <=
                             float(tbl.set_index("key").loc["VT15", "换手"].rstrip("%")) * 1.3 else "FAIL",
                             "卡玛-固定": base["卡玛-固定"], "回撤改善pp": base["回撤改善pp"],
                             "年化vsVT15pp": base["年化vsVT15pp"]})

        m0 = monthly(runs["VT15"]["ret"], idx, start)
        for key in ("SL_S1", "SL_S2", "SL_S3"):
            ci = stationary_bootstrap_ci(monthly(runs[key]["ret"], idx, start), m0)
            boot_all.append({"池": pool, "对比": f"{key} vs VT15",
                             "Sharpe差_5%": round(ci["sharpe_lo"], 3), "Sharpe差_50%": round(ci["sharpe_med"], 3),
                             "Sharpe差_95%": round(ci["sharpe_hi"], 3),
                             "CAGR差_5%": f"{ci['cagr_lo']*100:.2f}%", "CAGR差_50%": f"{ci['cagr_med']*100:.2f}%",
                             "CAGR差_95%": f"{ci['cagr_hi']*100:.2f}%"})

        meta["pools"][pool] = {"n": int(close.shape[1]), "start": str(idx[start].date()),
                               "end": str(idx[-1].date()),
                               "legs": {k: round(float(v[start:].mean()), 3) for k, v in legs.items()},
                               "avg_expo": {k: round(float(np.mean(runs[k]["expo"][start:])), 3)
                                            for k in runs}}

    pd.concat(rows_all, ignore_index=True).to_csv(os.path.join(OUT, "summary_all.csv"),
                                                 index=False, encoding="utf-8-sig")
    pd.DataFrame(boot_all).to_csv(os.path.join(OUT, "bootstrap.csv"), index=False, encoding="utf-8-sig")
    cd = pd.DataFrame(crit_all)
    cd.to_csv(os.path.join(OUT, "criteria.csv"), index=False, encoding="utf-8-sig")
    p("\n[判据]")
    p(cd.to_string(index=False))
    p("\n[bootstrap]")
    p(pd.DataFrame(boot_all).to_string(index=False))
    json.dump(meta, open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    open(os.path.join(OUT, "console.log"), "w", encoding="utf-8").write("\n".join(log))
    print(f"\n写入 {OUT}")


if __name__ == "__main__":
    main()
