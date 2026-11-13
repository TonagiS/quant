"""事件层补充：去掉 2020-02 后的 5 次；对照无条件 20 日收益；并核查最新 cum14=+42.94%。"""

from __future__ import annotations

import collections
import csv

import numpy as np

ROOT = "E:/Code/quant"

# ---------- 数据 ----------
sh = collections.defaultdict(dict)
for r in csv.DictReader(open(f"{ROOT}/data/national_team/etf_shares_hist.csv", encoding="utf-8-sig")):
    sh[r["date"]][r["code"]] = float(r["shares_wan"])
sdates = sorted(sh)

px_rows = list(csv.DictReader(open(f"{ROOT}/data/cache2019/sh510300.csv", encoding="utf-8-sig")))
pd_dates = [r["date"] for r in px_rows]
px = [float(r["close"]) for r in px_rows]
pix = {d: i for i, d in enumerate(pd_dates)}

EVENTS = ["2020-02-27", "2020-07-27", "2023-04-11", "2026-01-16", "2026-04-27", "2026-08-18"]


def fwd20(d: str):
    i = pix.get(d)
    if i is None or i + 20 >= len(px):
        return None
    return (px[i + 20] / px[i] - 1) * 100


print("=== 1) 事件层：全部 6 次 vs 去掉 2020-02 后的 5 次 ===")
allv, exv = [], []
for e in EVENTS:
    v = fwd20(e)
    print(f"{e}  后20日 510300 {v:+.2f}%" if v is not None else f"{e}  数据不足")
    if v is None:
        continue
    allv.append(v)
    if e != "2020-02-27":
        exv.append(v)
print(f"全部 6 次：均值 {np.mean(allv):+.2f}%  中位 {np.median(allv):+.2f}%  下跌次数 {sum(1 for v in allv if v < 0)}/6")
print(f"去掉 2020-02 的 5 次：均值 {np.mean(exv):+.2f}%  中位 {np.median(exv):+.2f}%  下跌次数 {sum(1 for v in exv if v < 0)}/5")

print()
print("=== 2) 无条件 20 日收益对照（同一价格序列） ===")
base_all = [fwd20(d) for d in pd_dates if fwd20(d) is not None and d >= "2019-08-13"]
base_all = [v for v in base_all if v is not None]
print(f"全样本（{len(base_all)} 个重叠窗口）：均值 {np.mean(base_all):+.2f}%  中位 {np.median(base_all):+.2f}%  下跌占比 {sum(1 for v in base_all if v < 0) / len(base_all) * 100:.0f}%")

# 剔除事件窗口（事件日起 20 个交易日内）后的干净基准
mask_ev = set()
for e in EVENTS:
    i = pix.get(e)
    if i is None:
        continue
    mask_ev.update(pd_dates[i: i + 21])
base_clean = [fwd20(d) for d in pd_dates if d >= "2019-08-13" and d not in mask_ev]
base_clean = [v for v in clean for v in base_clean if False] if False else [v for v in base_clean if v is not None]
print(f"剔除事件窗口后（{len(base_clean)} 个）：均值 {np.mean(base_clean):+.2f}%  中位 {np.median(base_clean):+.2f}%")

# 5 次事件均值的随机对照：随机抽 5 个不重叠窗口
rng = np.random.default_rng(7)
draws = []
for _ in range(5000):
    pick = rng.choice(base_clean, size=5, replace=False)
    draws.append(np.mean(pick))
draws = np.array(draws)
obs = np.mean(exv)
print(f"随机抽 5 个窗口的均值分布：中位 {np.median(draws):+.2f}%，5% 分位 {np.quantile(draws, 0.05):+.2f}%，"
      f"95% 分位 {np.quantile(draws, 0.95):+.2f}%")
print(f"观察到的 5 次均值 {obs:+.2f}% 位于随机分布的 {(draws < obs).mean() * 100:.0f} 分位")

print()
print("=== 3) 核查 2026-11-06 的 cum14 = +42.94% ===")
win = [d for d in sdates if "2026-10-12" <= d <= "2026-11-06"]
codes = sorted(sh[win[-1]])
print("date        " + " ".join(c[:6].rjust(8) for c in codes) + "     合计(亿份)")
for d in win:
    cells = []
    for c in codes:
        v = sh[d].get(c)
        cells.append("%8.0f" % (v / 10000) if v is not None else "%8s" % "-")
    print(d + "  " + " ".join(cells) + "  %12.0f" % (sum(sh[d].values()) / 10000))

print()
print("逐日总份额相对变化（%）以及贡献最大的单只：")
for i in range(1, len(win)):
    a, b = sh[win[i - 1]], sh[win[i]]
    common = [c for c in b if c in a]
    tot = (sum(b[c] for c in common) / sum(a[c] for c in common) - 1) * 100
    contrib = {c: (b[c] / a[c] - 1) * 100 * sum(a[c] for c in common) / sum(a[c] for c in common) for c in common}
    top = max(common, key=lambda c: abs(b[c] / a[c] - 1))
    print(f"{win[i]}  合计 {tot:+6.2f}%  单只最大 {top} {(b[top]/a[top]-1)*100:+.2f}%  "
          f"（占昨日总份额 {a[top]/sum(a.values())*100:.0f}%）")
