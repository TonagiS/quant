# -*- coding: utf-8 -*-
"""POS-08 预登记：ADX 分位阈值（相对阈值）替换固定 20。

假设：ADX 的绝对水平随市场 regime 漂移（描述统计：固定 20 在滚动 2 年窗口里落在 P16~P43），
用「ADX 处于自己近 2 年的后 q% 分位」做减半条件，比固定 20 更稳。
先写 PREREG，再跑判据，最后落 REPORT。
产物：runs/2026-11-04_POS-08_adx_pct/
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
sys.path.insert(0, r"E:\Code\quant\work")

from position.engine import (  # noqa: E402
    Config, Rule, VolTarget, features, load_panel, monthly, perf,
    simulate, stationary_bootstrap_ci,
)
from indicator_sweep import adx  # noqa: E402

BUY = 0.0005
OUT = r"E:\Code\quant\runs\2026-11-04_POS-08_adx_pct"
os.makedirs(OUT, exist_ok=True)
lines = []

MAIN = dict(adx_win=14, q=0.30, lookback=500, minp=250, mult=0.5, reb=5, vt=0.15)
FIXED = dict(adx_win=14, th=20.0, mult=0.5)


def log(s=""):
    print(s)
    lines.append(str(s))


PRE_PREREG = """# POS-08 预登记（跑数前冻结，2026-11-04）

**动机（只用分布，不看收益）**：`work/adx_pct_describe.txt` 显示 ADX(14) 在 ext 池等权指数上全样本 P30≈20.1，
固定阈值 20 恰是"约三成时间触发"的绝对线；但它在滚动 2 年窗口中的分位位置在 **P16~P43** 之间漂移
（2022 年 ADX 中位 33 只剩 16% 时间触发，2021 年中位 19 触发 55%）。绝对阈值在不同 regime 下含义不同。

**假设**：把减半条件从「ADX < 20」换成「ADX 处于过去 500 日（约 2 年）的后 30% 分位」，触发率不变
（全样本 29.5%≈30%），但阈值随 regime 自适应，卡玛与参数稳健性都不劣于固定 20。

**主配置（冻结）**：VT 15% / 波动窗口 60 日 / ADX 窗口 14 / 分位窗口 500 日（min_periods 250）/
分位阈值 q=30% / 减半 0.5 / 调仓 5 交易日 / 场内 5bp。
辅助：固定 20 版（POS-07 主配置）同口径重跑作对照。

**判据（全部满足才算通过）**：
① 卡玛 ≥ 1.00
② 最大回撤 ≥ -20%
③ 年化 ≥ 17.0%
④ 扰动稳健：q∈{20,25,30,35,40}% × 窗口∈{250,500,750,扩展} × 减半∈{0.4,0.5,0.6}（60 组）最差卡玛 ≥ 0.95
⑤ 三段（训练/验证/测试）卡玛都不低于「同平均仓位的固定仓位」对照
⑥ 对同平均仓位对照的月度 bootstrap：Sharpe 差 90% 区间不含 0
⑦ 相对判据：主配置卡玛 ≥ 固定 20 主配置卡玛 − 0.02（分位口径不比绝对阈值差）

**数据**：`data/cache2019` 52 只 ETF，2019-08-13~2026-11-02；场内口径（买 5bp、卖 5bp）。
**事前选择**：q=30% 由"固定 20 的全样本分位≈30%"确定，不是为了刷卡玛；窗口 500 日取"约 2 年"的常见口径。
**局限**：分位阈值的想法来自 POS-07 之后的分布诊断，含事后观察成分；仍是单一样本、单市场；
ADX 族已连续三轮探索（POS-06/07/08），多重检验风险需在结论中折扣。
"""


class Fix(Rule):
    def __init__(self, e, name=""):
        self.e = e.to_numpy()
        self.name = name

    def reset(self):
        pass

    def scale(self, j, ctx):
        return float(self.e[min(j, len(self.e) - 1)])


close = load_panel("ext")
f = features(close)
idx = close.index
lo = f["start"]
n = len(close)
level = close.mean(axis=1)
level = level / level.iloc[0]
A = adx(level, MAIN["adx_win"])


def pct_series(s, lookback, minp=250):
    """当日值在过去 lookback 日（含当日）中的分位；lookback=None 用扩展窗口。"""
    if lookback is None:
        return s.expanding(min_periods=minp).apply(lambda x: float(np.mean(x <= x[-1])), raw=True)
    return s.rolling(lookback, min_periods=minp).apply(lambda x: float(np.mean(x <= x[-1])), raw=True)


def vt_scale(reb, vt):
    r = simulate(close, f, Config(reb=reb, k=5, buy_cost=BUY, sell_cost=BUY,
                                  rule=VolTarget(target=vt, window=60, every=reb)))
    return pd.Series(r["scale"], index=idx)


def expo_from(cond, vt, mult):
    return (vt * pd.Series(np.where(cond, mult, 1.0), index=idx)).clip(0, 1)


def run(e):
    r = simulate(close, f, Config(reb=MAIN["reb"], k=5, buy_cost=BUY, sell_cost=BUY, rule=Fix(e, "x")))
    return r, perf(r["ret"][lo:])


vt = vt_scale(MAIN["reb"], MAIN["vt"])
pct = pct_series(A, MAIN["lookback"], MAIN["minp"])
main_e = expo_from(pct < MAIN["q"], vt, MAIN["mult"])
main_res, main_m = run(main_e)

# 固定 20 对照（POS-07 主配置同口径重跑）
fix_e = expo_from(A < FIXED["th"], vt, FIXED["mult"])
fix_res, fix_m = run(fix_e)

base_res = simulate(close, f, Config(reb=MAIN["reb"], k=5, buy_cost=BUY, sell_cost=BUY,
                                     rule=VolTarget(target=MAIN["vt"], window=60, every=MAIN["reb"])))
base_m = perf(base_res["ret"][lo:])

avg = float(np.mean(main_res["scale"][lo:]))
ctrl = simulate(close, f, Config(reb=MAIN["reb"], k=5, buy_cost=BUY, sell_cost=BUY,
                                 rule=Fix(pd.Series(avg, index=idx), "ctrl")))
ctrl_m = perf(ctrl["ret"][lo:])

log("=== POS-08 结果（52 ETF，场内 5bp，2019-08~2026-11）===")
log(f"VT15 基线：年化 {base_m['cagr']:.2%} 回撤 {base_m['mdd']:.2%} 卡玛 {base_m['calmar']:.2f}")
log(f"固定20（POS-07）：年化 {fix_m['cagr']:.2%} 回撤 {fix_m['mdd']:.2%} 卡玛 {fix_m['calmar']:.2f} "
    f"平均仓位 {np.mean(fix_res['scale'][lo:]):.0%} 触发率 {np.mean((A < 20)[idx[lo:]]):.1%}")
log(f"分位30%（主配置）：年化 {main_m['cagr']:.2%} 回撤 {main_m['mdd']:.2%} 卡玛 {main_m['calmar']:.2f} "
    f"平均仓位 {avg:.0%} 触发率 {np.mean((pct < MAIN['q'])[idx[lo:]]):.1%}")
log(f"同仓位对照（固定 {avg:.0%}）：年化 {ctrl_m['cagr']:.2%} 回撤 {ctrl_m['mdd']:.2%} 卡玛 {ctrl_m['calmar']:.2f}")

# ④ 扰动
grid = []
for q in (0.20, 0.25, 0.30, 0.35, 0.40):
    for lb in (250, 500, 750, None):
        ps = pct_series(A, lb, MAIN["minp"])
        for mult in (0.4, 0.5, 0.6):
            e = expo_from(ps < q, vt, mult)
            _, m = run(e)
            grid.append(dict(q=q, lookback=(lb if lb else "expanding"), mult=mult,
                             cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"]))
df = pd.DataFrame(grid).sort_values("calmar", ascending=False)
df.to_csv(os.path.join(OUT, "grid.csv"), index=False, encoding="utf-8-sig")
log("\n参数扰动 60 组：卡玛最高 {:.2f}、最低 {:.2f}、中位 {:.2f}".format(
    df["calmar"].max(), df["calmar"].min(), df["calmar"].median()))
log("最差 5 组：")
for _, r in df.tail(5).iterrows():
    log(f"  q={r['q']:.0%} 窗口={r['lookback']} 减半={r['mult']}: 年化 {r['cagr']:.2%} 回撤 {r['mdd']:.2%} 卡玛 {r['calmar']:.2f}")

# 探索：分位线性斜坡（不进判据）
ramp_e = (vt * pd.Series(np.clip(0.5 + pct.fillna(0.5) / 0.6, 0.5, 1.0), index=idx)).clip(0, 1)
_, ramp_m = run(ramp_e)
log(f"\n[探索，不进判据] 分位线性斜坡：年化 {ramp_m['cagr']:.2%} 回撤 {ramp_m['mdd']:.2%} 卡玛 {ramp_m['calmar']:.2f}")

# ⑤ 三段
c1 = lo + int((n - lo) * 0.5)
c2 = c1 + int((n - lo) * 0.25)
segs, segs_c = [], []
for a0, b0 in ((lo, c1), (c1, c2), (c2, n)):
    segs.append(perf(main_res["ret"][a0:b0])["calmar"])
    segs_c.append(perf(ctrl["ret"][a0:b0])["calmar"])
log("\n三段卡玛：分位 " + "/".join(f"{x:.2f}" for x in segs) +
    "   同仓位对照 " + "/".join(f"{x:.2f}" for x in segs_c))

# ⑥ bootstrap
ci = stationary_bootstrap_ci(monthly(main_res["ret"], idx, lo), monthly(ctrl["ret"], idx, lo))
log(f"bootstrap(vs 同仓位)：Sharpe 差 {ci['sharpe_med']:+.3f} [{ci['sharpe_lo']:+.3f}, {ci['sharpe_hi']:+.3f}]，"
    f"CAGR 差 {ci['cagr_med']:+.2%} [{ci['cagr_lo']:+.2%}, {ci['cagr_hi']:+.2%}]")

c_ok = [
    ("① 卡玛 ≥ 1.00", main_m["calmar"] >= 1.00, f"{main_m['calmar']:.2f}"),
    ("② 回撤 ≥ -20%", main_m["mdd"] >= -0.20, f"{main_m['mdd']:.2%}"),
    ("③ 年化 ≥ 17.0%", main_m["cagr"] >= 0.17, f"{main_m['cagr']:.2%}"),
    ("④ 扰动最差卡玛 ≥ 0.95", df["calmar"].min() >= 0.95, f"{df['calmar'].min():.2f}"),
    ("⑤ 三段都不低于对照", all(s >= c for s, c in zip(segs, segs_c)),
     " ".join(f"{s:.2f} vs {c:.2f}" for s, c in zip(segs, segs_c))),
    ("⑥ Sharpe 差区间不含 0", ci["sharpe_lo"] > 0, f"[{ci['sharpe_lo']:+.3f}, {ci['sharpe_hi']:+.3f}]"),
    ("⑦ 卡玛 ≥ 固定20 − 0.02", main_m["calmar"] >= fix_m["calmar"] - 0.02,
     f"{main_m['calmar']:.2f} vs {fix_m['calmar']:.2f}"),
]
log("\n判据：")
for name, ok, val in c_ok:
    log(f"  {name}: {val} → {'✓' if ok else '✗'}")
verdict = "通过" if all(ok for _, ok, _ in c_ok) else "未通过"
log(f"\n判定：**{verdict}**")

open(os.path.join(OUT, "PREREG.md"), "w", encoding="utf-8").write(PRE_PREREG)
open(os.path.join(OUT, "REPORT.md"), "w", encoding="utf-8").write(
    "# POS-08 结果（ADX 分位阈值 vs 固定 20）\n\n```\n" + "\n".join(lines) + "\n```\n")
json.dump(dict(main=MAIN, fixed=FIXED,
               base=dict(cagr=base_m["cagr"], mdd=base_m["mdd"], calmar=base_m["calmar"]),
               main_m=dict(cagr=main_m["cagr"], mdd=main_m["mdd"], calmar=main_m["calmar"], avg=avg),
               fixed_m=dict(cagr=fix_m["cagr"], mdd=fix_m["mdd"], calmar=fix_m["calmar"]),
               control=dict(avg=avg, cagr=ctrl_m["cagr"], mdd=ctrl_m["mdd"], calmar=ctrl_m["calmar"]),
               grid=dict(n=len(df), best=float(df["calmar"].max()), worst=float(df["calmar"].min()),
                         median=float(df["calmar"].median())),
               ramp=dict(cagr=ramp_m["cagr"], mdd=ramp_m["mdd"], calmar=ramp_m["calmar"]),
               verdict=verdict), open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("\n产物:", OUT)
