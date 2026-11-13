"""VAL-02：估值灯改成"双向"——低估加仓（含杠杆）、高估减仓。

VAL-01 里引擎把总敞口 clip 在 [0,1]，只能减不能加；这里用日收益后处理的方式补上"加"这一半：
    r_new = m * r_VT15 - max(m-1, 0) * 融资利率 / 252
m 为灯乘子（可以 >1），融资成本按需扣。灯只用截至前一交易日的分位（shift 1）。

输入：runs/2026-11-13_VAL-01_valuation_light/equity_{ext,long}.csv（VT15 / S2 / L1 的日收益）
产物：runs/2026-11-13_VAL-02_valuation_twosided/
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

from position.engine import monthly, perf, stationary_bootstrap_ci  # noqa: E402
from position.valuation_light import valuation_pct  # noqa: E402

VAL01 = os.path.join(ROOT, "runs", "2026-11-13_VAL-01_valuation_light")
OUT = os.path.join(ROOT, "runs", "2026-11-13_VAL-02_valuation_twosided")
os.makedirs(OUT, exist_ok=True)
BORROW = 0.04          # 融资年化 4%（两融 5~8%，ETF 质押更低，取中偏保守）

# 双向档位：分位上界 -> 乘子（>1 = 加杠杆，<1 = 减仓）
TIERS = {
    "T1双向温和": ((20, 1.20), (50, 1.00), (80, 0.75), (999, 0.40)),
    "T2双向激进": ((10, 1.50), (20, 1.20), (50, 1.00), (80, 0.60), (999, 0.20)),
    "T3极端双向": ((10, 1.25), (90, 1.00), (999, 0.30)),
    "T4只加不减": ((20, 1.25), (999, 1.00)),
    "T5只减不加": ((50, 1.00), (80, 1.00), (999, 0.40)),
}


def multiplier(pct, tiers):
    m = np.ones(len(pct))
    for i, x in enumerate(pct):
        if not np.isfinite(x):
            continue
        for thr, s in tiers:
            if x <= thr:
                m[i] = s
                break
    return m


def apply(r, m, borrow=BORROW):
    lev = np.maximum(m - 1.0, 0.0)
    return m * np.asarray(r, float) - lev * borrow / 252.0, float(np.mean(lev))


def maxdd(r):
    eq = np.cumprod(1 + np.asarray(r, float))
    return float((eq / np.maximum.accumulate(eq) - 1).min())


def row(name, r, extra=None):
    m = perf(r)
    return {"策略": name, "年化%": round(m["cagr"] * 100, 2), "波动%": round(m["vol"] * 100, 2),
            "夏普": round(m["sharpe"], 2), "回撤%": round(m["mdd"] * 100, 1),
            "卡玛": round(m["calmar"], 2), **(extra or {})}


def main():
    log = []

    def p(*a):
        s = " ".join(str(x) for x in a)
        print(s)
        log.append(s)

    p(f"VAL-02 双向估值灯 · 融资 {BORROW:.0%}/年 · {pd.Timestamp.now().isoformat(timespec='seconds')}")
    all_rows, boot = [], []

    for pool in ("ext", "long"):
        df = pd.read_csv(os.path.join(VAL01, f"equity_{pool}.csv"), parse_dates=["date"]).set_index("date")
        idx = df.index
        pct = valuation_pct(idx)
        pct = np.concatenate([[pct[0]], pct[:-1]])          # 用前一日分位（无前视）
        pct = np.where(np.isfinite(pct), pct, np.nan)
        r_vt = df["VT15"].to_numpy(float)
        T = len(df)
        n_live = T
        bounds = [(0, int(n_live * 0.5)), (int(n_live * 0.5), int(n_live * 0.75)), (int(n_live * 0.75), T)]

        p(f"\n===== {pool}（{idx[0].date()} ~ {idx[-1].date()}，{T} 天）=====")
        p("灯档占比: " + "  ".join(
            f"{k} {np.nanmean(v):.0%}" for k, v in
            (("绿<=20", pct <= 20), ("黄20-50", (pct > 20) & (pct <= 50)),
             ("橙50-80", (pct > 50) & (pct <= 80)), ("红>80", pct > 80))))

        base = row("VT15（单向基准，不加不减）", r_vt)
        rows = [base]

        for name, tiers in TIERS.items():
            m = multiplier(pct, tiers)
            r, avg_lev = apply(r_vt, m)
            rows.append(row(name, r, {"平均乘子": round(float(np.mean(m)), 2),
                                      "平均杠杆": round(avg_lev, 2),
                                      "融资拖累pp": round(avg_lev * BORROW * 100, 2)}))
        tbl = pd.DataFrame(rows)
        p(tbl.to_string(index=False))
        tbl.insert(0, "池", pool)
        all_rows.append(tbl)

        # bootstrap vs VT15（月度）
        ma0 = monthly(r_vt, idx, 0)
        for name, tiers in TIERS.items():
            m = multiplier(pct, tiers)
            r, _ = apply(r_vt, m)
            ci = stationary_bootstrap_ci(monthly(r, idx, 0), ma0)
            boot.append({"池": pool, "对比": f"{name} vs VT15", "Sharpe差_5%": round(ci["sharpe_lo"], 3),
                         "Sharpe差_50%": round(ci["sharpe_med"], 3), "Sharpe差_95%": round(ci["sharpe_hi"], 3),
                         "CAGR差_5%": f"{ci['cagr_lo'] * 100:.2f}%", "CAGR差_50%": f"{ci['cagr_med'] * 100:.2f}%",
                         "CAGR差_95%": f"{ci['cagr_hi'] * 100:.2f}%"})

        # 三段
        for name, tiers in TIERS.items():
            m = multiplier(pct, tiers)
            r, _ = apply(r_vt, m)
            segs = []
            for nm, (lo, hi) in zip(("训练", "验证", "测试"), bounds):
                a = perf(r[lo:hi])
                b = perf(r_vt[lo:hi])
                segs.append(f"{nm}:{a['cagr'] * 100:.1f}%/卡玛{a['calmar']:.2f}(基准{b['calmar']:.2f})")
            p(f"  {name:<8} " + "  ".join(segs))

        # 融资利率敏感性
        p(f"\n[融资利率敏感性 {pool}]")
        for rate in (0.0, 0.04, 0.08):
            line = []
            for name in ("T1双向温和", "T2双向激进"):
                m = multiplier(pct, TIERS[name])
                r, _ = apply(r_vt, m, borrow=rate)
                mtr = perf(r)
                line.append(f"{name} 年化{mtr['cagr'] * 100:.2f}% 卡玛{mtr['calmar']:.2f}")
            p(f"  融资{rate:.0%}: " + " | ".join(line))

        # 安慰剂：平移分位
        p(f"[安慰剂 {pool}]")
        for k, tag in ((252, "平移+1年"), (-252, "平移-1年")):
            m = multiplier(np.roll(pct, k), TIERS["T1双向温和"])
            r, _ = apply(r_vt, m)
            mtr = perf(r)
            p(f"  {tag}: 年化{mtr['cagr'] * 100:.2f}% 回撤{mtr['mdd'] * 100:.1f}% 卡玛{mtr['calmar']:.2f}")

    pd.concat(all_rows, ignore_index=True).to_csv(os.path.join(OUT, "summary_all.csv"),
                                                 index=False, encoding="utf-8-sig")
    bdf = pd.DataFrame(boot)
    bdf.to_csv(os.path.join(OUT, "bootstrap.csv"), index=False, encoding="utf-8-sig")
    p("\n[bootstrap 月度 stationary 90% 区间]")
    p(bdf.to_string(index=False))
    open(os.path.join(OUT, "console.log"), "w", encoding="utf-8").write("\n".join(log))
    print(f"\n写入 {OUT}")


if __name__ == "__main__":
    main()
