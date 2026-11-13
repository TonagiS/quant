# quant 工作区（2026-11-03 重整）

给鱼片看的目录地图 + 仓位管理白话说明。旧版本备份：`work/pre_reorg_20261103.zip`。

## 1. 目录

```
quant/
├─ data/            行情数据（研究、影子、模拟盘共用）
│  ├─ cache2019/    2019 起 ETF 日线（base/ext，52 只）
│  ├─ cache2007/    2007 起指数/个股日线（long/stocks）
│  ├─ cache_base/   旧的 25 只 ETF 缓存（历史遗留，一般不用）
│  └─ daily/        每日 14:30/收盘快照 YYYYMMDD.json（原 shadow/raw）
├─ indicators/      指标研究
│  ├─ README.md     指标手册（公式、参数、证据等级、代码索引）
│  ├─ catalog.csv   指标目录（机器可读）
│  ├─ rules_45_ext.csv  45 行规则证据快照
│  ├─ structure_macd.md MACD 钝化/结构定义
│  ├─ code/         回测/预登记代码（backtest_composite、pools、macd_structure、*_prereg）
│  ├─ results/      指标研究的历史输出（composite_*、cost_*、turn_* 等）
│  └─ frozen/       预登记脚本只读副本
├─ position/        仓位管理
│  ├─ README.md     研究结论 + 白话说明
│  ├─ PREREG.md     跑数前的假设与判据
│  ├─ POLICY_v1.md  可执行的仓位提案
│  ├─ ADVANCED.md   网上仓位方法回测（POS-02）
│  ├─ SOURCES.md    方法原文链接
│  ├─ engine.py     仓位规则回测引擎
│  ├─ run_grid.py   24 变体网格
│  ├─ advanced.py   EWMA/CVaR/CPPI/ERC/MDP
│  ├─ policy.py     实盘用纯函数（波动目标敞口 + 单票上限）
│  └─ live/         实盘/模拟盘执行层
│     ├─ decision_1430.py   14:30 决策脚本（人工交互）
│     ├─ portfolio.json     实际持仓
│     ├─ paper_account.py   双账本模拟盘（s2 / vt15，各 10 万）
│     ├─ paper/             模拟盘账户/流水
│     └─ shadow/            影子 v1、V2 观察臂（每日快照写到 data/daily）
├─ runs/            每次回测的产物（按 日期_实验名）
├─ work/            临时脚本/杂项/本次备份 zip
├─ bak/             旧文件备份（部分只读）
├─ venv/            Python 环境
└─ invest-wiki-main/ 外部投资 wiki（另一个项目，未动）
```

命令统一从仓库根目录跑；回测脚本在 `indicators/code/`，实盘脚本在 `position/live/`。

## 2. 仓位管理到底管什么（白话）

**信号层**回答"买什么"：动量排名选出 5 只 ETF，并给出相对权重（比如 28%/20%/20%/19%/12%）。
**仓位层**回答"总共投多少、单只最多多少"：

1. **总敞口**：看策略最近 60 天的波动。波动大就少投，波动小就多投。
   `敞口 = min(1, 15% / 年化波动60)`。
2. **单票上限**：单只不超过股票仓位的 40%，避免候选不足时一把梭。
3. **执行**：按 100 份整数下单，剩下的现金留着。

### 一个 10000 元的例子

| 步骤 | 规则 | 结果 |
|---|---|---|
| 信号 | S2 选出 5 只，相对权重 28.1/20.5/20.0/19.2/12.3% | — |
| 总敞口 | 假设 60 日年化波动 35% → `15%/35%=43%` | 投入约 **4300 元**，现金 5700 |
| 单票上限 | 单只 ≤ 40% × 4300 = 1720 元 | 最大一只 28.1%×4300≈1208，不触发 |
| 下单 | 100 份整数倍 | 5 笔，余零钱进现金 |

所以仓位管理**不是选股、不是止损、不是预测涨跌**，它只决定"这 10000 块拿多少去冒险"。
回测里它把 2026 年那种回撤从 -33.6% 压到 -16.2%，代价是牛市少赚一点（详见 `position/README.md`）。

## 3. 常用命令

```powershell
# 仓位网格（ext/base，约 1 分钟）
.\venv\Scripts\python.exe position\run_grid.py

# 网上仓位方法（EWMA/CVaR/CPPI/ERC/MDP，约 3~5 分钟）
.\venv\Scripts\python.exe position\advanced.py

# 一键重跑两组（无人值守，日志写 work\pos_all_*.log）
powershell -NoProfile -ExecutionPolicy Bypass -File position\run_all.ps1

# 指标回测（在 runs\<日期_实验> 目录里跑，产物落当前目录）
.\venv\Scripts\python.exe ..\..\indicators\code\backtest_composite.py --pool ext --start 20190101

# 影子 14:30 / 收盘
powershell -NoProfile -ExecutionPolicy Bypass -File position\live\shadow\daily.ps1 -phase 1430
powershell -NoProfile -ExecutionPolicy Bypass -File position\live\shadow\daily.ps1 -phase eod
```

## 4. 约定

- 数据只读 `data/`，不要在不同起点之间混用缓存。
- 新指标先写预登记，跑完登记 `registry.csv`，结论写 `results.md` 和 `indicators/README.md`。
- `position/live/shadow/RULES_v2.md` 是当前 v2 规则（v1 已归档到 `indicators/frozen/`，哈希见 `FROZEN_v1.json` / `FROZEN_v2.json`）。
- 产物放 `runs/<日期>_<实验名>/`。
