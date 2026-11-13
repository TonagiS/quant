# position/live/shadow 使用说明

**V2 观察臂**：`v2_observ.py`（缓冲带 k+2）只在 15:10 由 `daily.ps1` 调用，记录换手、滑点、价差到 `v2_observ.csv`；不参与 v1、不比收益、不改规则。

**状态**：v1 已归档（`indicators/frozen/run_shadow_v1.py` + `FROZEN_v1.json`）；当前 runner 为 v2（`RULES_v2.md` + `FROZEN_v2.json`），v1 规则文件保留未改。
**目录迁移（2026-11-03）**：从 `quant/shadow/` 移到 `quant/position/live/shadow/`；v1 runner 的路径改动使旧 `FROZEN.json` 失效，已用归档版重建 v1 哈希。
**计划任务**：迁移后查询不到原 `quant_shadow_1430/eod`（可能已被删除）；需要时重新注册，见文末。

## 每日两次
```powershell
$env:PYTHONIOENCODING='utf-8'
.\venv\Scripts\python.exe position\live\shadow\run_shadow.py --phase 1430   # 14:30 取价、算目标、写假设订单
.\venv\Scripts\python.exe position\live\shadow\run_shadow.py --phase eod    # 15:10 后补收盘价与两种成交收益
```
14:30 那一轮抓不到某只 ETF 的 14:30 bar 时，该标的记入 `failed`，不参与当日目标，不补数。

## 文件
| 文件 | 内容 |
|---|---|
| `RULES_v1.md` / `indicators/frozen/FROZEN_v1.json` | v1 冻结规则与归档 runner 的 SHA256 |
| `RULES_v2.md` / `FROZEN_v2.json` | v2 规则与当前 runner 的 SHA256 |
| `universe_v1.csv` | ext 成份快照（含 status / 失败原因） |
| `shadow_log.csv` | 每日一行（1430）一行（eod），字段见下 |
| `data/daily/YYYYMMDD.json` | 当日原始价与失败明细（共享数据目录，便于复盘） |
| `state.json` | 上一交易日目标权重（算订单用） |
| `manual_overrides.csv` | 人工覆盖日志 |
| `replay_diff.csv` / `replay_report.json` | 60 日回放一致性 |
| `comment_YYYYMMDD.md` | DeepSeek 定性评论（人工触发，不进计算） |

## shadow_log.csv 字段
`date, time, phase, universe_n, enough, cash_gate_above_ma, n_cand, targets_json, orders_json, px_1430_json, px_close_json, ret_fill1430, ret_fillclose, failed, notes`

- `ret_fill1430`：按 14:30 成交、持有到收盘的组合收益（fill-to-close）。
- `ret_fillclose`：按收盘成交，当日剩余收益为 0（对照）。
- `cash_gate_above_ma` 只记录，不影响仓位。

## 检查点
第 20/40/60 个交易日各出一份小结：数据质量（失败只数/原因）、滑点分布（均值、95 分位）、与回测口径的一致性；**不用收益改规则**。

## 14:30 群播报（2026-11-03 上线）
`daily.ps1 -phase 1430` 在模拟盘成交后调用 `E:\Code\dsh-agent\group_brief.py`，把当日操作 / 持仓 / 板块 20 日涨跌以 QQ 合并消息发到群 315962846。
- 发送走 dsh_bridge 的 HTTP `/notify`（`forward: true`，每组一行一个 node）；群号在 `E:\Code\dsh-agent\group.json`。
- 非交易日没有 `data/daily/YYYYMMDD.json` 时自动跳过；同一天只发一次（`E:\Code\dsh-agent\state\sent_YYYYMMDD.txt`）。
- 播报只用 14:30 快照价，不代表收盘净值；不改规则、不写 shadow 数值列。
- 原任务 `quant_shadow_1430` 曾设 StartWhenAvailable / WakeToRun（错过可补跑），执行上限 1 小时；迁移后需重新注册。


## 计划任务（待确认）
如需每天自动跑，注册两个任务（不涉及券商、不下单）：
```powershell
$t1 = 'powershell -NoProfile -ExecutionPolicy Bypass -File "E:\Code\quant\position\live\shadow\daily.ps1" -phase 1430'
$t2 = 'powershell -NoProfile -ExecutionPolicy Bypass -File "E:\Code\quant\position\live\shadow\daily.ps1" -phase eod'
schtasks /Create /TN "quant_shadow_1430" /TR $t1 /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 14:30 /F
schtasks /Create /TN "quant_shadow_eod"  /TR $t2 /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:10 /F
```
需要管理员权限或在本会话批准 danger-full-access 才能写入系统任务目录。
