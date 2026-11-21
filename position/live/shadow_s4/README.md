# shadow_s4 —— S4 纸面影子盘（独立于 v2 影子盘）

依据 position/frozen_candidates/PROPOSAL_S4_SHADOW.md（用户 2026-11-21 批准，方案 B：独立目录 + 独立计划任务）。
**只记纸面，不下单**；不读写 portfolio.json、paper/books/s2|vt15、shadow_log.csv、daily.ps1、FROZEN_v2.json。

## 文件
| 文件 | 作用 |
|---|---|
| run_s4_shadow.py | 每日运行器：拉数据 → 延长面板 → 调用冻结研究代码 → 写日志 |
| s4lib.py | 数据拉取（腾讯日线 qfq+不复权、csindex PE、东财 10 年国债、FRED DGS10）、面板延长、模块打补丁、指纹 |
| replay_check_s4.py | 复现检验：research / live / ext 三场景比较近期调仓持仓与收益 |
| FROZEN_SHADOW_S4.json | 代码 sha256 指纹（LF 口径） |
| RULES_S4.md | 规则出处 |
| run_s4_daily.sh | Linux / box 入口 |
| run_s4_daily.ps1, register_task.ps1 | Windows 入口与计划任务注册（任务名 quant_shadow_s4_eod，工作日 15:30） |
| seed_files.txt | 新机器需要从 box 复制的研究期缓存清单 |

## 输出（正式运行写在本目录；`--dry-run` 写 dryrun/）
- `s4_log.csv`：每交易日一行：status(pre-start/official)、池规模、有效数、候选数、是否信号日/执行日、VT scale、当前持仓、次日目标(orders_json)、5bp/20bp 当日收益与净值、14:30 成交差(bp，仅执行日当天运行可得)、当日换手、回撤、K1 标志、拉取失败、指纹是否一致、备注。
- `arm_BROAD_VT15.csv`、`arm_C3.csv`、`arm_C3_60_S4.csv`：各臂持仓/目标/收益/净值。
- `obs01_log.csv`：DGS10、20 日变化(bp)、触发、t+2 生效、假设减半后的 S4 收益。
- `weekly_YYYYWW.md`（周五）、`latest.json`（最近一次摘要）、`data_overlap_check.csv`（实时数据与研究面板重叠期逐日比较）。
- `replay/`：replay_check_s4.py 的结果。
- 数据缓存：`data/cache_live_s4/`（gitignored）。

## 运行
```bash
# box / Linux（仓库根目录）
bash position/live/shadow_s4/run_s4_daily.sh               # 正式
DRY=1 bash position/live/shadow_s4/run_s4_daily.sh         # 演练
PUSH=1 bash position/live/shadow_s4/run_s4_daily.sh        # 正式 + 只提交本目录日志并 fetch+rebase 推送（从不 force）
python3 position/live/shadow_s4/replay_check_s4.py         # 复现检验（第 20/60 个交易日各跑一次）
```
单次约 1–2 分钟（拉 ~200 只 ETF）；每月第一个交易日多一次全市场重拉（~1600 只，约 5–10 分钟）。

## Windows 注册（用户本人执行一次）
1. 确认 `E:\Code\quant\venv` 可用，且 seed_files.txt 里的研究缓存已在 `E:\Code\quant\data\` 下（没有就从 box 复制；pickle 需要与 box 相近的 pandas 版本）。
2. 先手动演练：`powershell -ExecutionPolicy Bypass -File E:\Code\quant\position\live\shadow_s4\run_s4_daily.ps1 -DryRun`，看 `shadow_s4\run.log` 末尾有 JSON 摘要。
3. 注册：`powershell -ExecutionPolicy Bypass -File E:\Code\quant\position\live\shadow_s4\register_task.ps1`
4. 撤销：`schtasks /Delete /TN quant_shadow_s4_eod /F`，再删/归档本目录（v2 影子盘不受影响）。

box 和 Windows **只选一处**跑正式记录，避免两份 s4_log.csv 冲突。

## 规则与评估
见 RULES_S4.md 与 PROPOSAL_S4_SHADOW.md 第 5 节。指纹不一致（fingerprint_ok=0）= 代码被改，按 K3 作废当期记录、修复后重新计时。
