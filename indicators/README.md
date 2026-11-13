# indicators · 指标参考区（2026-11-03 建）

这里集中放**指标/信号的定义、参数、证据等级和代码索引**，不放回测产物（产物在 `runs/`）。
目的：以后加指标、做仓位管理、写预登记时，先来这里查"这个指标有没有证据、公式是什么、代码在哪"。
为不破坏 shadow 计划任务与脚本 import，`position/live/shadow/RULES_v1.md` 冻结规则和实盘脚本留在 `position/live/`；本目录放指标代码、手册、证据快照（`code/`、`results/`、`frozen/`）。

> 数据快照：除注明外均为 **2026-11-02**（腾讯/新浪日线，ETF 池 2019-2026）。
> 所有"有证据"都是**同一批数据上的参数稳健性，不是样本外**；引用时必须带这句。

## 1. 证据分级

| 级别 | 含义 | 本仓库对应 |
|---|---|---|
| **A** | 通过预登记判据，且跨池/跨段/对照后仍成立 | S2 动量+趋势+逆波动（P1-ROBUST-01）；波动率目标（POS-01） |
| **B** | 方向成立但只在特定池/特定口径 | 60 日线过滤（ETF 正、指数负）；逆波动（仅 ext）；120 日现金闸门（降回撤、不保卡玛） |
| **C** | 证据不足，只能观察 | 底部结构；个券上限；调仓频率 |
| **D** | 无证据或有害 | 量能、拥挤度、RSI6、顶部结构、固定止损、回撤节流、Kelly |

判据门槛：预登记 5 条全过才算"通过"；规则表另用 Bonferroni 约 t≥3（45 个检验）。
**未预登记的规则一律先当 C，不许直接进策略。**

## 2. 核心指标

| 指标 | 公式 / 定义 | 默认参数 | 用法 | 证据 | 代码 |
|---|---|---|---|---|---|
| 动量综合 `rm` | `rank(mom20)+rank(mom60)+rank(mom120)` 的截面 pct rank 均值 | 20/60/120 日 | 候选排序，取前 k=5 | **A** | `code/backtest_composite.py: xs_rank / make_rules`；`../position/live/shadow/engine.py: features` |
| 单期动量 `momN` | `close / close.shift(N) - 1` | N=5/20/60/120 | 打分与诊断 | **A**（20/60/120） | 同上 |
| 趋势过滤 | `close > MA60`（另测 MA120） | 60 日 | 只买站上 60 日线的标的 | **B**（ETF 正、指数负） | 同上 |
| 20 日波动 `vol20` | 日收益 20 日滚动标准差（`min_periods=15`） | 20 日 | 逆波动权重；波动率目标的输入 | **A**（作为权重/风险输入） | 同上 |
| 逆波动权重 | `w_i ∝ 1/vol20_i`，归一 | 20 日 | k=5 内部分配 | **B**（仅 ext 有增益） | `run_portfolio(invvol=True)`；`../position/live/shadow/engine.py: targets` |
| 量能比 `vol_ratio` | `amount / mean(amount, 20).shift(1)` | 20 日 | 放量/缩量规则 | **D** | `code/backtest_composite.py: build_features` |
| 拥挤度 `crowd` | 成交额 60 日历史分位（`(x<=x[-1]).mean()*100`） | 60 日 | >85 回避 | **D** | 同上 |
| RSI6 | Wilder RSI，`alpha=1/6` | 6 日 | <20 买入 | **D** | 同上 |
| MACD 钝化/结构 | 见 `structure_macd.md` | lookback=120, recent=5, trough=5 | 顶部减仓/底部加仓 | **D**（顶部）/ **C**（底部观察） | `code/macd_structure.py` |
| 120 日现金闸门 | 等权指数 < MA120 | 120 日 | 只记录，不影响仓位（v1） | **B**（回撤 4/5 池改善，卡玛 3/5，未过预登记） | `../position/live/shadow/engine.py: cash_gate`；`code/gate_prereg.py` |

## 3. 组合信号 S2 / shadow v1

- 有效标的：累计 bar ≥150、收盘非空、`mom120` 非空、`vol20` 非空；当日有效数 <8 不产生目标。
- 候选：`valid & rm 非空 & close > MA60`。
- 目标：候选按 `rm` 降序取前 **k=5**；权重按 `1/vol20` 归一。
- 调仓：每 **5 个交易日**；收盘信号，次日收盘成交（回测 delay=1）。
- 成本：单边 5bp 基准；S2 年化双边换手约 **39~40 倍**（单边 18~20 倍），5bp 成本约 2.0pp/年。
- 冻结版规则见 `../position/live/shadow/RULES_v1.md`（运行期不得改）；实现见 `../position/live/shadow/engine.py`。

## 4. 规则证据表（15 规则 × 3 持有期 = 45 行）

快照：`rules_45_ext.csv`（来自 `runs/2026-11-02_ext/composite_rules.csv`，ext 池、2019-08-13~2026-11-02）。

- ext 池"稳健"仅 2 行：`R1_no_vol 强动量+站上20日线(无量)` 的 5/10 日（NW_t 2.03/2.17）；属于探索组，量能版本 R1 反而不稳。
- base 0 行、stocks 0 行、long+stocks 0 行；long 2 行（R10 的 10/20 日），但 R10 在个股池 NW −1.40，预登记判"未通过"。
- 结论：**量能、拥挤度、顶部结构、RSI6 全部无证据**；不要因为某一池的 2 行"稳健"就上线。

## 5. 指标使用的硬约束（写结论/上线前必看）

1. **成本**：S2 盈亏平衡 25bp（base）/29bp（ext）单边，低于常见实盘成本；任何提高换手的指标必须先过成本关。
2. **样本**：2019-2026 是动量大年；滚动窗口高度重叠；不是样本外。跨池不一致的指标按 C 处理。
3. **标的**：动量只在 ETF 主题池有证据；个股池 5.3% < 等权 8.1%；指数 2007-2018 段趋势过滤反而伤收益。
4. **执行**：回测"收盘信号 + delay=1"与产品"14:30 决策 + 尾盘成交"差一天，未用分钟数据校验。
5. **新增指标**：先写预登记（判据、样本、成本、对照），跑完登记到 `registry.csv`，再把结论写回 `results.md` 和本目录。禁止直接改 `../position/live/shadow/RULES_v1.md`。

## 6. 代码索引

| 功能 | 位置 |
|---|---|
| 特征（动量/均线/波动/量能/拥挤度/RSI/结构） | `code/backtest_composite.py: build_features` |
| 截面排名 | `code/backtest_composite.py: xs_rank` |
| 45 规则定义 | `code/backtest_composite.py: make_rules` |
| 组合回测/成本/仓位叠加 | `code/backtest_composite.py: run_portfolio` |
| 波动率管理（旧版） | `code/backtest_composite.py: volmanage` |
| MACD 钝化/结构 | `code/macd_structure.py` |
| shadow v1 纯函数 | `../position/live/shadow/engine.py` |
| 仓位规则与回测 | `../position/engine.py`、`../position/run_grid.py` |
| 仓位研究结论 | `../position/README.md`、`../runs/2026-11-03_POS-01/` |

## 7. 外部参考（invest-wiki 研究，非本仓库产物）

- `../invest-wiki-main/skills/etf-momentum/research/findings_champion.md`：17 指数 2009-2026，vol scaling 把回撤 -65.6%→-38.5%、Calmar 0.11→0.16；固定止损有害；低相关 top3 是免费午餐。
- `../invest-wiki-main/skills/etf-momentum/research/findings_bt2.md`：轮动/分散/止损的对照。
- `../invest-wiki-main/wiki/strategies/行业ETF动量轮动.md`：策略框架与指标解释。
- `../invest-wiki-main/wiki/strategies/多因子模型与因子投资.md`：因子口径。
> 这些是外部资料，引用时注明来源；不要把 wiki 数字和本仓库 run 数字混在一张表里。

## 8. 维护

- 本目录只增不改历史结论；改结论要带日期和 run 编号。
- `rules_45_ext.csv` 是快照，重跑后如要更新，另存 `rules_45_ext_YYYYMMDD.csv`，不要覆盖旧快照。
- 指标公式若与代码不一致，以代码 + 预登记文档为准，并在本页标注差异。
