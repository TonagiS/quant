"""§6 加下限注；§13 加影子成本/滑点对比口径。"""

from pathlib import Path

p = Path(r"E:\Code\quant\results.md")
text = p.read_text(encoding="utf-8")

NOTE = (
    "- **下限而非上界**：165 不含 POS-03（探索性网格）、R10（登记簿外）及各类探索性运行；"
    "多重检验按 **≥165** 从严处理，不因漏登而放宽阈值。\n"
)
if "下限而非上界" not in text:
    anchor = "- 后续 Bonferroni / SPA 一律按 **165** 计；新增条目时先改这张表，再动上限。\n"
    text = text.replace(anchor, anchor + NOTE, 1)

SEC = """
### 13.3 影子运行的成本/滑点对比口径（2026-11-05 固定）

- **对比基准只用预登记口径的 VT15**：ext 年化 **16.50%**、回撤 **−16.51%**、卡玛 **1.00**、换手 26.0x；
  base 15.85% / −21.89% / 0.72、换手 24.3x（2019-08-13~2026-11-02、场内 5bp、reb5、5 日重测）。
- **`v2_observ.csv` 只记换手与滑点**（`turnover_l1`、`slippage_mean/p95/std_bp`），不比收益、不进 v1；
  第 20/40/60 个交易日的阶段小结也只拿滑点分布与上表换手对照，不拿 `RULES_v2` 成本表的 15.64%/0.95 那一套。
- **`RULES_v2.md` 的成本表是 2019-01 起快照**，属于费率档选择的历史依据，**不是**影子对比基准；
  两套窗口不同源，引用时必须写明是哪一套。
"""
if "13.3 影子运行的成本/滑点对比口径" not in text:
    text = text.rstrip() + "\n" + SEC

p.write_text(text, encoding="utf-8")
print("results.md updated:", "下限而非上界" in text, "13.3" in text)
