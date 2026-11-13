"""记录影子首日与模拟盘结果。"""
import os

R = r"E:\Code\quant"


def edit(path, old, new):
    s = open(path, encoding="utf-8").read()
    assert old in s, (path, old[:40])
    open(path, "w", encoding="utf-8").write(s.replace(old, new, 1))
    print("ok", path)


edit(os.path.join(R, "results.md"), "## 8. 运行环境",
     """## 7.1 影子首日（2026-11-03）

- 14:30 机器未开机，计划任务未触发；14:48 **手动补跑** v1 的 14:30 一轮（52 只全部取到价，失败 0）。
- 目标 5 只（权重）：中证180金融 28.1%、银行 20.5%、红利 20.0%、消费 19.2%、酒 12.3%；候选 6 只。
- 120 日闸门状态：等权指数**在 120 日线下方**（仅记录，不影响仓位）。
- **模拟盘 10,000 元**：14:51 按 14:30 价成交 5 笔，成本 5bp 共 4.79 元；收盘净值 **10,025.91**（自 14:30 +0.31%，自建仓 +0.26%），现金 424.21 元。
- v1 fill-to-close 加权 **+0.32%**（收盘价高于 14:30 价，今日早买占优）；V2 观察臂滑点均值 **+31.5bp**、p95 +40.9bp。
- 15:10 计划任务**正常触发**（15:12:31 完成），随后手动用新浪 15:00 bar 兜底补齐收盘价并计净值。
- 数据问题：腾讯日线当日 bar 在 15:12 尚未更新，已给 `run_shadow.px_close` 加“新浪 15:00”兜底；`paper_account` 用同一兜底链。

## 8. 运行环境""")

edit(os.path.join(R, "INDEX.md"), "| `runs/2026-11-02_T4-SHARE-COVER` | 数据可行性 |",
     "| `paper/` | 模拟盘 | `python paper_account.py --execute\\|--mark` | 每交易日 | 10,000 元，按影子 v1 权重成交；首日 10,025.91（+0.26%） | `account.json`, `ledger.csv`, `trades.csv` |\n"
     "| `shadow/v2_observ.csv` | 观察臂 | 每日 15:10 由 daily.ps1 调用 | 逐日 | 只记换手/滑点；首日换手 1.0、滑点 +31.5bp | `v2_observ.csv` |\n"
     "| `runs/2026-11-02_T4-SHARE-COVER` | 数据可行性 |")
