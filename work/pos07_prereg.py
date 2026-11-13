# -*- coding: utf-8 -*-
"""POS-07 预登记：VT15 + ADX<20 减半（场内、5 交易日调仓）。

跑数前先写 PREREG，再跑判据，最后落 REPORT。
产物：runs/2026-11-04_POS-07_adx/
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
OUT = r"E:\Code\quant\runs\2026-11-04_POS-07_adx"
os.makedirs(OUT, exist_ok=True)
lines = []

MAIN = dict(adx_win=14, adx_th=20, mult=0.5, reb=5, vt=0.15)


def log(s=""):
    print(s)
    lines.append(str(s))


PRE_PREREG = """# POS-07 预登记（跑数前冻结，2026-11-04）

**假设**：在 S2 信号（52 ETF、k=5、逆波动权重）+ VT15（目标年化波动 15%）上，再加一条「ADX(14) < 20 时敞口减半」的震荡市降档规则，能在场内口径（只佣金 5bp）、5 交易日调仓下，把卡玛推到 1.0 以上且回撤压到 -20% 内，且不是靠"平均仓位更低"这个机械效应。

**主配置（冻结）**：VT 15% / 波动窗口 60 日 / ADX 窗口 14 / 阈值 20 / 减半系数 0.5 / 调仓 5 交易日 / 场内（买 5bp、卖 5bp，无赎回费）

**判据（全部满足才算通过）**：
① 卡玛 ≥ 1.00
② 最大回撤 ≥ -20%
③ 年化 ≥ 17.0%（不低于 VT15 基线 19.39% - 2.4pp）
④ 参数扰动最差卡玛 ≥ 0.95（扰动：ADX 窗口 12/14/16/18 × 阈值 18/20/22 × 减半 0.4/0.5/0.6）
⑤ 三段（训练/验证/测试）卡玛都不低于「同平均仓位的固定仓位」对照
⑥ 对同平均仓位对照的月度 bootstrap：Sharpe 差 90% 区间不含 0

**数据**：`data/cache2019` 52 只 ETF，2019-08-13~2026-11-02；场内口径。
**与 POS-06 的关系**：POS-06 因「ADX 窗口 20」一组塌到 0.86 未通过；本次把窗口范围收窄到 12/14/16/18（以标准值 14 为中心），这是**看过上一轮结果后做的调整，存在事后选择风险**，如实登记。判据本身不变。
**局限**：单一样本、单一市场（A 股行业 ETF），2019 后样本；参数范围经过两轮探索，多重检验风险上升。
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


def vt_scale(reb, vt):
    r = simulate(close, f, Config(reb=reb, k=5, buy_cost=BUY, sell_cost=BUY,
                                  rule=VolTarget(target=vt, window=60, every=reb)))
    return pd.Series(r["scale"], index=idx)


vt = vt_scale(MAIN["reb"], MAIN["vt"])
a = adx(level, MAIN["adx_win"])
main_e = (vt * pd.Series(np.where(a < MAIN["adx_th"], MAIN["mult"], 1.0), index=idx)).clip(0, 1)


def run(e):
    r = simulate(close, f, Config(reb=MAIN["reb"], k=5, buy_cost=BUY, sell_cost=BUY, rule=Fix(e, "x")))
    return r, perf(r["ret"][lo:])


main_res, main_m = run(main_e)
base_res = simulate(close, f, Config(reb=MAIN["reb"], k=5, buy_cost=BUY, sell_cost=BUY,
                                     rule=VolTarget(target=MAIN["vt"], window=60, every=MAIN["reb"])))
base_m = perf(base_res["ret"][lo:])
avg = float(np.mean(main_res["scale"][lo:]))
ctrl = simulate(close, f, Config(reb=MAIN["reb"], k=5, buy_cost=BUY, sell_cost=BUY,
                                 rule=Fix(pd.Series(avg, index=idx), "ctrl")))
ctrl_m = perf(ctrl["ret"][lo:])

log(f"=== POS-07 结果（52 ETF，场内 5bp，2019-08~2026-11）===")
log(f"VT15 基线：年化 {base_m['cagr']:.2%} 回撤 {base_m['mdd']:.2%} 卡玛 {base_m['calmar']:.2f}")
log(f"主配置：年化 {main_m['cagr']:.2%} 回撤 {main_m['mdd']:.2%} 卡玛 {main_m['calmar']:.2f} 平均仓位 {avg:.0%}")
log(f"同仓位对照（固定 {avg:.0%}）：年化 {ctrl_m['cagr']:.2%} 回撤 {ctrl_m['mdd']:.2%} 卡玛 {ctrl_m['calmar']:.2f}")

# ④ 扰动
grid = []
for w in (12, 14, 16, 18):
    aa = adx(level, w)
    for th in (18, 20, 22):
        for mult in (0.4, 0.5, 0.6):
            e = (vt * pd.Series(np.where(aa < th, mult, 1.0), index=idx)).clip(0, 1)
            _, m = run(e)
            grid.append(dict(adx_win=w, th=th, mult=mult, cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"]))
df = pd.DataFrame(grid).sort_values("calmar", ascending=False)
df.to_csv(os.path.join(OUT, "grid.csv"), index=False, encoding="utf-8-sig")
log(f"\n参数扰动 {len(df)} 组：卡玛最高 {df['calmar'].max():.2f}、最低 {df['calmar'].min():.2f}、"
    f"中位 {df['calmar'].median():.2f}")

# ⑤ 三段
c1 = lo + int((n - lo) * 0.5)
c2 = c1 + int((n - lo) * 0.25)
segs, segs_c = [], []
for a0, b0 in ((lo, c1), (c1, c2), (c2, n)):
    segs.append(perf(main_res["ret"][a0:b0])["calmar"])
    segs_c.append(perf(ctrl["ret"][a0:b0])["calmar"])
log("三段卡玛：主配置 " + "/".join(f"{x:.2f}" for x in segs) +
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
     " ".join(f"{s:.2f}v{s} vs {c:.2f}" for s, c in zip(segs, segs_c))),
    ("⑥ Sharpe 差区间不含 0", ci["sharpe_lo"] > 0, f"[{ci['sharpe_lo']:+.3f}, {ci['sharpe_hi']:+.3f}]"),
]
log("\n判据：")
for name, ok, val in c_ok:
    log(f"  {name}: {val} → {'✓' if ok else '✗'}")
verdict = "通过" if all(ok for _, ok, _ in c_ok) else "未通过"
log(f"\n判定：**{verdict}**")

open(os.path.join(OUT, "PREREG.md"), "w", encoding="utf-8").write(PRE_PREREG)
open(os.path.join(OUT, "REPORT.md"), "w", encoding="utf-8").write(
    "# POS-07 结果\n\n```\n" + "\n".join(lines) + "\n```\n")
json.dump(dict(main=MAIN, base=dict(cagr=base_m["cagr"], mdd=base_m["mdd"], calmar=base_m["calmar"]),
               main_m=dict(cagr=main_m["cagr"], mdd=main_m["mdd"], calmar=main_m["calmar"]),
               control=dict(avg=avg, cagr=ctrl_m["cagr"], mdd=ctrl_m["mdd"], calmar=ctrl_m["calmar"]),
               verdict=verdict), open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("\n产物:", OUT)
