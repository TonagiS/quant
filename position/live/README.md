# position/live · 实盘/模拟盘执行层

这里放"每天真正会跑"的东西；研究代码在上一级 `position/`。

| 路径 | 用途 |
|---|---|
| `decision_1430.py` | 14:30 决策脚本（拉实时价、动量排序、DeepSeek 定性评论、手动买卖录入） |
| `portfolio.json` | 实际持仓（`decision_1430.py` 读写） |
| `paper_account.py` | 双账本模拟盘（s2 / vt15，各 10 万，场外分档费） |
| `paper/books/` | `s2`、`vt15` 两本账户、ledger、trades、expo |
| `shadow/` | 影子 v2 每日运行器、V2 观察臂、回放校验；v1 已归档到 `indicators/frozen/` |
| `../../data/daily/` | 每日 14:30/收盘快照（共享数据目录，不在本层） |

## 常用命令

```powershell
cd E:\Code\quant
# 影子 14:30（取价、算目标、写 data/daily/YYYYMMDD.json）
.\venv\Scripts\python.exe position\live\shadow\run_shadow.py --phase 1430

# 影子收盘（补收盘价、算两种成交假设）
.\venv\Scripts\python.exe position\live\shadow\run_shadow.py --phase eod

# 模拟盘：14:30 后成交 / 收盘计净值
.\venv\Scripts\python.exe position\live\paper_account.py --execute
.\venv\Scripts\python.exe position\live\paper_account.py --mark

# 一键跑两轮（含 V2 观察臂、群播报）
powershell -NoProfile -ExecutionPolicy Bypass -File position\live\shadow\daily.ps1 -phase 1430
powershell -NoProfile -ExecutionPolicy Bypass -File position\live\shadow\daily.ps1 -phase eod
```

## 注意

- **v1 已归档**到 `indicators/frozen/run_shadow_v1.py`（哈希见 `FROZEN_v1.json`）；当前 runner 是 v2（`RULES_v2.md` + `FROZEN_v2.json`）。
- 仓位管理 policy（VT15 + 单票 40%）仍是提案，**未接线**；paper 目前并行跑 `s2` 满仓与 `vt15` 两本，见 `../POLICY_v1.md`。
- 计划任务：迁移后查询不到原 `quant_shadow_1430/eod`；需要时按 `shadow/README.md` 文末命令重新注册。
