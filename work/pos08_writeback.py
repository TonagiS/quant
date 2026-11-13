# -*- coding: utf-8 -*-
"""把 POS-08 结论写回 results.md / INDEX.md / indicators/catalog.csv。"""
import io

ROOT = r"E:\Code\quant"

SECTION = """
## 10. 2026-11-04 ADX 分位（描述 + POS-08）

**一句话**：固定 `ADX(14) < 20` 减半（POS-07）已经是 ADX 族里最好的口径；换成**自适应分位阈值**没有改进，判定未通过，不改 shadow v2。

**分布诊断**（`work/adx_pct_describe.txt`，ext 52 ETF 等权指数，2019-01~2026-11，1896 日）：
- ADX 全样本 P30 ≈ 20.1，`<20` 占 29.5% —— 即固定 20 就是"约三成时间触发"的绝对线。
- 但它在**滚动 2 年窗口**里的分位位置在 **P16~P43** 之间漂移（标准差 8pp）：2021 年 ADX 中位 18.8、`<20` 触发 55%；2022 年中位 33.2、只触发 16%。
- 结论：绝对阈值在不同 regime 下含义确实不同，这个前提成立。

**POS-08 预登记与结果**（`runs/2026-11-04_POS-08_adx_pct/`，主配置：ADX 处于过去 500 日后 30% 分位 → 敞口减半 0.5）：
- 分位 30%：年化 19.26% / 回撤 -17.18% / 卡玛 **1.12**，触发率 28.2%
- 固定 20（POS-07 同口径重跑）：19.65% / -16.17% / **1.21**，触发率 30.5%
- 同平均仓位（68%）对照：13.43% / -23.74% / 0.57
- 扰动 60 组（q 20~40% × 窗口 250/500/750/扩展 × 减半 0.4/0.5/0.6）：最高 1.22、中位 1.12、**最低 0.86**（q=40%/250 日，触发过频）
- 逐年看（`diag.txt`）：分位口径在震荡年更好（2021 卡玛 0.63 vs 固定 0.34），在单边年更差（2026 1.77 vs 2.77、2025 3.95 vs 4.28）
- 判据：①②③⑤⑥ 过，**④ 扰动最差 0.86 < 0.95**、**⑦ 卡玛 1.12 < 固定 20 的 1.21 − 0.02** → **未通过**
- 探索项：分位线性斜坡（不缺省硬阈值）1.09，也不如固定 20

**处理**：ADX 减半仍按 POS-07 的固定 20；分位版本登记为 D（无证据），不进 `RULES_v2`。
**局限**：ADX 族已连续三轮（POS-06/07/08）探索，多重检验风险显著；分位想法来自 POS-07 之后的分布诊断，含事后观察成分；仍是单样本单市场，2019 后样本。
"""

rows = [
    "| `runs/2026-11-04_POS-06_adx` | 预登记 | `python work/pos06_prereg.py` | 2026-11-04 | VT15+ADX<20 减半：窗口 20 一组塌到 0.86，④ 未过 | `PREREG.md`, `REPORT.md` |",
    "| `runs/2026-11-04_POS-07_adx` | 预登记 | `python work/pos07_prereg.py` | 2026-11-04 | 主配置 19.65%/-16.17%/卡玛 1.21，扰动 36 组最低 0.96，六条全过 → **通过** | `PREREG.md`, `REPORT.md`, `grid.csv` |",
    "| `runs/2026-11-04_POS-08_adx_pct` | 预登记 | `python work/pos08_adx_pct.py` | 2026-11-04 | ADX 分位阈值（q30/500d）卡玛 1.12 < 固定 20 的 1.21，扰动最差 0.86 → **未通过** | `PREREG.md`, `REPORT.md`, `diag.txt` |",
]

# results.md
p = ROOT + r"\results.md"
t = io.open(p, encoding="utf-8").read()
if "POS-08" not in t:
    if not t.endswith("\n"):
        t += "\n"
    t += SECTION
    io.open(p, "w", encoding="utf-8").write(t)
    print("results.md 追加完成")
else:
    print("results.md 已有 POS-08，跳过")

# INDEX.md：插到 **运行环境** 前
p = ROOT + r"\INDEX.md"
t = io.open(p, encoding="utf-8").read()
if "POS-08_adx_pct" not in t:
    i = t.find("**运行环境**")
    assert i > 0
    add = "\n".join(rows) + "\n"
    t = t[:i] + add + t[i:]
    io.open(p, "w", encoding="utf-8").write(t)
    print("INDEX.md 插入 3 行")
else:
    print("INDEX.md 已有，跳过")

# catalog.csv 追加两行
p = ROOT + r"\indicators\catalog.csv"
t = io.open(p, encoding="utf-8").read()
if "adx14" not in t:
    if not t.endswith("\n"):
        t += "\n"
    t += ('adx14,ADX趋势强度,择时,"Wilder ADX(14)：+DM/-DM/TR 用 alpha=1/14 平滑，DX 再平滑；作用于池等权指数",14日,'
          '"ADX<20 时敞口减半",B,POS-07 通过（卡玛 1.21，扰动最差 0.96；POS-06 未过）,work/indicator_sweep.py: adx\n')
    t += ('adx_pct,ADX分位阈值,择时,"ADX 处于过去 500 日（含当日）的后 q% 分位时敞口减半",ADX14/q30%/500日/'
          'minp250,"低于 q 分位减半",D,POS-08 未通过（卡玛 1.12<1.21，扰动最差 0.86）,work/pos08_adx_pct.py: pct_series\n')
    io.open(p, "w", encoding="utf-8").write(t)
    print("catalog.csv 追加 2 行")
else:
    print("catalog.csv 已有，跳过")
