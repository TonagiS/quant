"""VAL-03 稳健性：把"牛牛仓位 ∩ VT15"在 long 池上做阈值/步长/安慰剂/成本扰动。

只在 long 池做（ext 池这套规则平均仓位 9%、空仓 60%，已判死刑）。
"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

from position.engine import (  # noqa: E402
    Config, Fixed, MinRule, VolTarget, base_returns, features, monthly,
    simulate, slice_metrics, stationary_bootstrap_ci,
)
from position.valuation_light import load_panel_any, pctf, valuation_pct  # noqa: E402
from position.val03_niuniu_sizing import ExposureSeries, niuniu_exposure, HS300_CSV  # noqa: E402

OUT = os.path.join(ROOT, "runs", "2026-11-13_VAL-03_niuniu_sizing")
COST = 0.0005


def main():
    log = []

    def p(*a):
        s = " ".join(str(x) for x in a)
        print(s)
        log.append(s)

    pool = "long"
    close = load_panel_any(pool)
    f = features(close)
    start, T = f["start"], len(close)
    idx = close.index
    f["base_ret"] = base_returns(close, f, Config(cost=COST))
    hs = pd.read_csv(HS300_CSV)
    hs["date"] = pd.to_datetime(hs["日期"])
    hs = hs[["date", "收盘"]].rename(columns={"收盘": "px"}).sort_values("date")
    hsr = pd.merge_asof(pd.DataFrame({"date": idx}), hs, on="date", direction="backward")
    px = hsr["px"].to_numpy(float)
    pct = valuation_pct(idx)

    def run(expo, cost=COST, f_=None):
        cfg = Config(cost=cost, rule=MinRule([ExposureSeries(expo, "NN"), VolTarget(0.15, every=1)], "NN∩VT15"))
        res = simulate(close, f_ if f_ is not None else f, cfg)
        m = slice_metrics(res, start, T)
        c = float(np.clip(m["avg_expo"], 0, 1))
        cm = slice_metrics(simulate(close, f_ if f_ is not None else f, Config(cost=cost, rule=Fixed(c))), start, T)
        return m, cm, res

    rows = []

    def rec(tag, expo, **kw):
        m, cm, res = run(expo, **kw)
        rows.append({"变体": tag, "年化": pctf(m["cagr"]), "夏普": round(m["sharpe"], 2),
                     "回撤": pctf(m["mdd"]), "卡玛": round(m["calmar"], 2),
                     "平均仓位": f"{m['avg_expo']:.0%}", "卡玛-固定": round(m["calmar"] - cm["calmar"], 2),
                     "换手": f"{m['turn_two_way']:.0%}"})
        p(f"  {tag:<18} 年化{rows[-1]['年化']:>8} 夏普{rows[-1]['夏普']:>5} 回撤{rows[-1]['回撤']:>8} "
          f"卡玛{rows[-1]['卡玛']:>5} 仓位{rows[-1]['平均仓位']:>4} 卡玛-固定{rows[-1]['卡玛-固定']:>6}")
        return res

    p(f"VAL-03 稳健性（{pool} 池）· 基准 NN20∩VT15：4.95% / 卡玛 0.14 / 回撤 -35.1%")

    p("\n[阈值扰动]")
    rec("基准 NN20", niuniu_exposure(idx, px, pct, gate_low=20.0))
    rec("门15/卖75", niuniu_exposure(idx, px, pct, gate_low=15.0, sell_pct=75.0))
    rec("门25/卖85", niuniu_exposure(idx, px, pct, gate_low=25.0, sell_pct=85.0))
    rec("门30/卖80", niuniu_exposure(idx, px, pct, gate_low=30.0))

    p("\n[步长/卖出节奏]")
    rec("步长3%", niuniu_exposure(idx, px, pct, gate_low=20.0, step=0.03))
    rec("步长6%", niuniu_exposure(idx, px, pct, gate_low=20.0, step=0.06))
    rec("卖1份/20日", niuniu_exposure(idx, px, pct, gate_low=20.0, sell_tranches=1))
    rec("卖4份/20日", niuniu_exposure(idx, px, pct, gate_low=20.0, sell_tranches=4))

    p("\n[安慰剂：分位平移]")
    for k, tag in ((252, "+1年"), (-252, "-1年"), (504, "+2年"), (-504, "-2年")):
        rec(f"平移{tag}", niuniu_exposure(idx, px, np.roll(pct, k), gate_low=20.0))

    p("\n[成本敏感性]")
    for c in (0.0002, 0.0010):
        f2 = dict(f)
        f2["base_ret"] = base_returns(close, f, Config(cost=c))
        rec(f"成本{c*1e4:.0f}bp", niuniu_exposure(idx, px, pct, gate_low=20.0), cost=c, f_=f2)

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "robust_long.csv"), index=False, encoding="utf-8-sig")

    # bootstrap：基准 vs VT15
    m, _, res = run(niuniu_exposure(idx, px, pct, gate_low=20.0))
    vt = simulate(close, f, Config(cost=COST, rule=VolTarget(0.15, every=1)))
    ma = monthly(res["ret"], idx, start)
    mb = monthly(vt["ret"], idx, start)
    ci = stationary_bootstrap_ci(ma, mb)
    p(f"\n[bootstrap NN20∩VT15 vs VT15] Sharpe差 90% 区间 {ci['sharpe_lo']:.3f} ~ {ci['sharpe_hi']:.3f}"
      f"（中位 {ci['sharpe_med']:.3f}）；CAGR 差 {ci['cagr_lo']*100:.2f}% ~ {ci['cagr_hi']*100:.2f}%"
      f"（中位 {ci['cagr_med']*100:.2f}%）")
    # 单侧 p 值（bootstrap 中"差<=0"的比例）
    pval = float(np.mean(np.array([ci["sharpe_lo"]]) < 0))  # 占位
    open(os.path.join(OUT, "robust_console.log"), "w", encoding="utf-8").write("\n".join(log))


if __name__ == "__main__":
    main()
