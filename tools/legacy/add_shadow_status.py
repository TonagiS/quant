"""补 POS-03 配置核验、影子运行状态、研究冻结窗口。"""

from pathlib import Path

ROOT = Path(r"E:\Code\quant")
RES = ROOT / "results.md"
POS03 = ROOT / "runs" / "2026-11-04_POS-03_offex" / "REPORT.md"

POS03_BLOCK = """
## 配置与 base_ret 核验（2026-11-05）

- **最优配置**：池 `ext`；`reb=35` 交易日（≈49 自然日 → 卖出档 0.15% + 5bp 佣金）；买入 5bp；
  `cap=100%`（不限单票）；规则 `VT20`（目标波动 20%、60 日窗口、每 reb 重算）；年化换手 7.4x。
- **base_ret 口径**：`simulate()` 在 `rule.requires_base` 为真时，会用**同一 cfg**（同 reb、同费率的 `sell_cost(reb)`）
  重建未加仓控的 `base_ret`；本次用 `work/verify_pos03_best.py` 把两种路径各跑一遍对照：
  - 自动重建：ext 21.38% / −22.89% / 0.93，base 15.21% / −24.07% / 0.63
  - 手工重建（同参数显式喂 `base_ret`）：数值完全相同
- **与 REPORT 原表的差异**：21.45% / 0.94 → 21.38% / 0.93，差 0.07pp / 0.01，属行情快照漂移
  （原表快照 2026-11-02，核验时已含 11-03/11-04 收盘），**不是口径错误，无需重跑**。
"""

SHADOW = """
## 14. 影子运行状态与研究冻结（2026-11-05）

**计划任务**（`schtasks` 实测）
- `\\quant_shadow_1430`：每天 14:30，下次 2026-11-06 14:30，状态 Ready
- `\\quant_shadow_eod`：每天 15:10，下次 2026-11-06 15:10，状态 Ready
- 两者 `Logon Mode = Interactive only`：**需要已登录会话**；机器关机或锁屏未登录时不会自动补跑
  （11-03 首日就是 14:30 未开机、14:48 手动补跑，之后正常）。

**首次运行**：2026-11-03（手动补跑首日 14:48 那一轮）。
**日志**：`position\\live\\shadow\\run.log`（影子）+ `v2_observ.csv`（观察臂）+ `paper/books/*/ledger.csv`（账本）。

**v2_observ.csv 是否在记录**：在记录，已有 3 个交易日
| 日期 | 换手 | 滑点均值 | p95 | 备注 |
|---|---|---|---|---|
| 2026-11-03 | 1.00 | 31.5bp | 40.9bp | 首日建仓 |
| 2026-11-04 | 0.00 | 2.6bp | 10.6bp | 非调仓日 |
| 2026-11-05 | 0.00 | 25.1bp | 45.8bp | 非调仓日，价差偏大 |

**研究冻结（本日起生效）**
- 2026-11-05 起**暂停新研究**：不新增预登记、不改判据、不动 shadow v1/v2 规则。
- 第 20 个交易日（自 11-03 起算 = **2026-11-30**，按无节假日估算）只汇报三件事：
  **数据质量（失败只数/原因）、滑点（均值/95 分位/分布）、耗时**；不比收益、不做新假设。
"""

if "## 配置与 base_ret 核验（2026-11-05）" not in POS03.read_text(encoding="utf-8"):
    POS03.write_text(POS03.read_text(encoding="utf-8").rstrip() + "\n" + POS03_BLOCK, encoding="utf-8")
    print("POS-03 REPORT updated")

text = RES.read_text(encoding="utf-8")
if "## 14. 影子运行状态与研究冻结" not in text:
    text = text.rstrip() + "\n" + SHADOW
    RES.write_text(text, encoding="utf-8")
    print("results.md §14 added")
