# -*- coding: utf-8 -*-
"""POS-05 预登记：VT15 + 池等权 MA200 半仓 + 国债固定配置（风险预算）。

判据（跑数前冻结，写入 PREREG.md）：
  ① 主配置卡玛 >= 现用(VT15) 0.58 + 0.05 = 0.63
  ② 主配置最大回撤 <= -22%
  ③ 主配置年化 >= 12.3%（= 现用 14.32% - 2pp）
  ④ 参数扰动（MA150/250、半仓 0.3/0.7、债 0/30%）卡玛都不低于 0.58
  ⑤ 分段 2019-2022 / 2023-2026 卡玛都 >= 现用 VT15 同段
产物：runs/2026-11-04_POS-05_dd/
"""
import itertools
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")
sys.path.insert(0, r"E:\Code\quant\work")

from position.engine import Config, Fixed, VolTarget, features, load_panel, perf, simulate  # noqa: E402
from dd_control import VT_MATrend  # noqa: E402

BUY = 0.0005
OUT = os.path.join(r"E:\Code\quant", "runs", f"{date.today():%Y-%m-%d}_POS-05_dd")
os.makedirs(OUT, exist_ok=True)
BOND = r"E:\Code\quant\data\cache2019_exp\sh511010.csv"
lines = []

MAIN = dict(vt=0.15, ma=200, below=0.5, bond=0.20)


def log(s=""):
    print(s)
    lines.append(str(s))


def bond_series(idx):
    if not os.path.exists(BOND):
        return pd.Series(0.0, index=idx)
    b = pd.read_csv(BOND, parse_dates=["date"]).set_index("date")["close"].astype(float)
    return b.pct_change().reindex(idx).fillna(0.0)


def evaluate(close, f, idx, vt, ma, below, bond_w, bond):
    lo = f["start"]
    rule = VT_MATrend(target=vt, window=60, every=10, below=below, ma=ma)
    res = simulate(close, f, Config(reb=10, k=5, rule=rule, buy_cost=BUY, fee_tier=True))
    strat = pd.Series(res["ret"], index=idx)[lo:]
    blend = (1 - bond_w) * strat + bond_w * bond.reindex(strat.index).fillna(0.0)
    m = perf(blend.to_numpy())
    return m, blend


close = load_panel("ext")
f = features(close)
idx = close.index
bond = bond_series(idx)

# 基准
base = simulate(close, f, Config(reb=10, k=5, rule=VolTarget(target=0.15, window=60, every=10),
                                 buy_cost=BUY, fee_tier=True))
base_r = pd.Series(base["ret"], index=idx)[f["start"]:]
base_m = perf(base_r.to_numpy())

log(f"=== POS-05 预登记结果（52 ETF，场外分档，{idx[f['start']].date()} ~ {idx[-1].date()}）===")
log(f"现用 VT15 基准：年化 {base_m['cagr']:.2%} 回撤 {base_m['mdd']:.2%} 卡玛 {base_m['calmar']:.2f}")

m_main, blend_main = evaluate(close, f, idx, MAIN["vt"], MAIN["ma"], MAIN["below"], MAIN["bond"], bond)
log(f"\n主配置 (VT{MAIN['vt']:.0%} + MA{MAIN['ma']} 半仓 + {MAIN['bond']:.0%}国债)："
    f"年化 {m_main['cagr']:.2%} 回撤 {m_main['mdd']:.2%} 卡玛 {m_main['calmar']:.2f}")

# ④ 参数扰动
log("\n参数扰动（卡玛）：")
rows = []
for vt, ma, below, bw in itertools.product((0.12, 0.15, 0.18), (150, 200, 250), (0.3, 0.5, 0.7), (0.0, 0.2, 0.3)):
    m, _ = evaluate(close, f, idx, vt, ma, below, bw, bond)
    rows.append(dict(vt=vt, ma=ma, below=below, bond=bw, cagr=m["cagr"], mdd=m["mdd"], calmar=m["calmar"]))
df = pd.DataFrame(rows).sort_values("calmar", ascending=False)
df.to_csv(os.path.join(OUT, "grid.csv"), index=False, encoding="utf-8-sig")
worst = df["calmar"].min()
log(f"  共 {len(df)} 组：卡玛最高 {df['calmar'].max():.2f}，最低 {worst:.2f}，"
    f"高于现用基准({base_m['calmar']:.2f})的占 {100*(df['calmar']>=base_m['calmar']).mean():.0f}%")

# ⑤ 分段
log("\n分段卡玛（主配置 vs 现用 VT15）：")
segs = []
for a, b in (("2019-01-01", "2022-12-31"), ("2023-01-01", "2026-12-31")):
    mask = (blend_main.index >= pd.Timestamp(a)) & (blend_main.index <= pd.Timestamp(b))
    mb = perf(blend_main[mask].to_numpy())
    mref = perf(base_r[mask].to_numpy())
    segs.append((a[:4] + "-" + b[:4], mb["calmar"], mref["calmar"]))
    log(f"  {a[:4]}-{b[:4]}：主配置 {mb['cagr']:.2%}/{mb['calmar']:.2f}   现用 {mref['cagr']:.2%}/{mref['calmar']:.2f}")

# 判据
c1 = m_main["calmar"] >= base_m["calmar"] + 0.05
c2 = m_main["mdd"] >= -0.22
c3 = m_main["cagr"] >= base_m["cagr"] - 0.02
c4 = worst >= base_m["calmar"]
c5 = all(mb >= mr for _, mb, mr in segs)
verdict = "通过" if all([c1, c2, c3, c4, c5]) else "未通过"
log("\n判据：")
log(f"  ① 卡玛 ≥ {base_m['calmar']+0.05:.2f}：{m_main['calmar']:.2f} → {'✓' if c1 else '✗'}")
log(f"  ② 回撤 ≥ -22%：{m_main['mdd']:.2%} → {'✓' if c2 else '✗'}")
log(f"  ③ 年化 ≥ {base_m['cagr']-0.02:.2%}：{m_main['cagr']:.2%} → {'✓' if c3 else '✗'}")
log(f"  ④ 扰动最差卡玛 ≥ {base_m['calmar']:.2f}：{worst:.2f} → {'✓' if c4 else '✗'}")
log(f"  ⑤ 两段都 ≥ 现用：{['✓' if mb>=mr else '✗' for _, mb, mr in segs]} → {'✓' if c5 else '✗'}")
log(f"\n判定：**{verdict}**")

json.dump(dict(main=MAIN, base=dict(cagr=base_m["cagr"], mdd=base_m["mdd"], calmar=base_m["calmar"]),
               main_m=dict(cagr=m_main["cagr"], mdd=m_main["mdd"], calmar=m_main["calmar"]),
               verdict=verdict), open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
with open(os.path.join(OUT, "REPORT.md"), "w", encoding="utf-8") as fh:
    fh.write("# POS-05 预登记结果\n\n" + "\n".join(lines))
print("\n产物:", OUT)
