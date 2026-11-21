# 提案：把冻结的 S4 接入影子盘（只做纸面记录）

> **状态：提案，等用户拍板。没有实施**，`position/live/` 下没有任何文件被改动（HANDOFF 第 9 条）。
> 日期：2026-11-21（Asia/Shanghai）。对象：`FROZEN_S4.md` 冻结的 S4（S2 + 同板块 ≤2 + VT15）；可选再加 C3 60% + S4 40% 组合和 OBS-01（美债冲击）日志。
> 性质：纸面影子，**不下单、不碰券商、不替换在位策略**。

## 0. 为什么、以及先说清楚的证据边界
- **支持观察**：
  - S4 对同约束随机持仓的 p 值为 0.004 / 0.025 / 0.012（s4_random）；
  - 60/40 组合里，S4 卫星胜过随机卫星，114 个起点 100% 为正（prereg_satellite、sat_vt15_control）；
  - 对 S2+VT15 在 C3 底仓下也通过（sat_vt15_control）。
- **不支持替换的证据**：
  - S4 卫星**输给"宽基 5 只等权 + MA60 + VT15"**（sat_vt15_control：只有 37.7% / 26.3% 的起点为正）；
  - 加 S4 卫星不如纯底仓（0/114）；
  - 20bp 下 S4 相对宽基的优势消失；
  - 全部是样本内结果，DSR 0.09。
- 所以影子盘要回答的是：**实盘数据管线能不能复现回测？S4 相对 S2+VT15 和宽基+VT15 在样本外的表现怎样？**而不是"该不该上 S4"。

## 1. 要记录的臂
| 臂 | 规则 | 来源 |
|---|---|---|
| **S4**（必选） | FROZEN_S4，一字不改：PIT 前 40 池；20/60/120 动量百分位均值；close > MA60；同板块 ≤2；前 5；1/vol20；每 10 个交易日调仓；VT15；信号滞后 1 日 | `position/frozen_candidates/FROZEN_S4.md` |
| BROAD_VT15（对照，建议加） | 510300/510500/159915/588000/512100 等权 + MA60 + VT15（定义同 sat_vt15_control） | `runs/2026-11-21_sat_vt15_control/PREREG_ADDENDUM.md` |
| C3_60_S4（可选） | C3 底仓 60% + S4 40%，每月末再平衡 | `work/components_core.py`（C3）、prereg_satellite |
| OBS-01 日志（可选） | 只记录美债 10 年 20 日上行是否超过 30bp，以及"如果卫星减半"的假设收益差，**不影响任何臂** | `position/frozen_candidates/OBSERVATION_LIST.md` |

在位的 S2+VT15 已经由现有影子盘 v2 和 paper `vt15` 账本记录，直接拿来比，不重复跑。

## 2. 要改哪些文件（实施时；现在都没改）
**原则：新增独立目录，不改 v2 冻结文件**（RULES_v2.md / rules_v2.json / run_shadow.py / engine.py / universe_v1.csv 的哈希保持不变）。

| 文件 | 动作 | 说明 |
|---|---|---|
| `position/live/shadow_s4/run_s4_shadow.py` | 新增 | 每日收盘后运行：更新数据 → 算信号 → 写假设订单和净值。只 import `work/components_core.py` 这一套已冻结代码（指纹见 FROZEN_S4），或者把等价逻辑抄过来，再用回放比对证明逐日一致 |
| `position/live/shadow_s4/RULES_S4.md` + `FROZEN_S4_live.json` | 新增 | 规则副本，以及上一行脚本和依赖文件的 sha256（LF 口径；同时记 CRLF 口径，避免再出现 DSH 第 4 项那种问题） |
| `position/live/shadow_s4/pit_pool_refresh.py` | 新增 | 每月最后一个交易日收盘后：拉全市场 ETF 列表和近 60 日成交额，按冻结口径（剔除货币和债券、上市满 250 天）生成下月的前 40 名单 → `pool_YYYYMM.csv` |
| `position/live/shadow_s4/replay_check_s4.py` | 新增 | 用回测引擎回放最近 60 个交易日，与影子记录逐日比对（目标权重、日收益） |
| `position/live/shadow_s4/s4_log.csv`、`s4_state.json`、`books/*.csv` | 新增（运行时生成） | 见第 3 节 |
| `position/live/shadow/daily.ps1` | **二选一** | (A) 在 eod 分支末尾加一行调用 `run_s4_shadow.py`（daily.ps1 不在 FROZEN_v2 清单里，但属于 live 层，要拍板）；(B) **推荐**：不动 daily.ps1，另外注册一个计划任务 `quant_shadow_s4_eod`（15:30，周一到周五） |
| `position/live/README.md`、`HANDOFF.md` | 改文档 | 加一行说明和指向本提案 |

数据：
- 总收益收盘价沿用研究侧的口径（不复权价 + 分红/拆分调整，与 `tr_ens_data` 相同）；
- 板块标签用 `fullmkt_single_asset.sector_tag`；
- C3 需要沪深300 PE 和 10 年国债收益率（日度）；
- OBS-01 用 FRED DGS10；
- 实时社融**不需要**，因为 S4 不含 CREDIT。

## 3. 每日输出
- `s4_log.csv`：每个交易日一行，字段为 `date, phase=eod, pool_n, n_valid, n_cand, rebalance(0/1), vt_scale, targets_json, orders_json, ret_close_fill, ret_next1430_fill, nav_5bp, nav_20bp, failed, notes`。
- 成交假设：t 日收盘出信号。主口径是 t+1 收盘成交，与回测一致；另记一个 t+1 14:30 成交的口径作参考。成本分 5bp 和 20bp 两本账，现金收益为 0。
- 对照臂 BROAD_VT15、C3_60_S4 用同样的字段，单独写一个文件。
- OBS-01：`obs01_log.csv`，字段为 `date, dgs10, d20_bp, trigger(0/1), hypo_sat_ret_half`。
- 每周一份 `weekly_YYYYWW.md`：持仓、调仓、数据失败情况、和回放的偏差。只做记录，**不用收益调整规则**。

## 4. 评估时间表
- 起点：拍板之后的首个调仓日（FROZEN_S4 写的是"2026-11-21 之后的首个调仓日"）。
- **第 20 / 60 个交易日**：只看数据质量和复现程度，不看收益。
- **第 6 个月**：中期报告。数据和复现指标必须达标；收益只描述，不做决定。
- **第 12 个月**：正式评估，按第 5 节事先写好的标准给出结论。

## 5. 事先写死的成功 / 终止标准
**复现与运行（第 60 个交易日起持续检查）**
- R1：回放和影子记录的目标权重逐日一致的天数 ≥ 95%；日收益差的绝对值中位数 ≤ 5bp。
- R2：数据失败（缺价导致臂当日不可计算）的天数 ≤ 5%，并且不出现连续 5 个交易日以上的失败。
- R3：年化双边换手在回测值的 0.7–1.3 倍之间（回测 S4 为 20.72 / 20.91 / 19.39，对应 W2013 / W2017 / W2019，5bp）。

**12 个月评估（5bp 主账本）**
- **成功**（只说明"可以考虑写替换提案"，不自动替换），以下全部满足：
  - S1：S4 的最大回撤不比同期 S2+VT15 深 3pp 以上；
  - S2：S4 的累计收益 ≥ 同期 S2+VT15；
  - S3：S4 的卡玛 ≥ 同期 BROAD_VT15 的卡玛，或者在 S4 回撤更浅的前提下累计收益不低于它 2pp 以上；
  - 同时 R1–R3 都达标。
- **终止**（任一项触发就停止记录并写复盘，不改参数续跑）：
  - K1：S4 影子回撤 ≥ 25%（回测 W2013 最大回撤为 −24.9%）；
  - K2：R1 或 R2 连续 20 个交易日不达标，而且修不好；
  - K3：发现规则实现和 FROZEN_S4 不一致，需要修代码。这种情况作废当前记录，修好后重新开始计时，不能拼接；
  - K4：用户要求停止。
- 12 个月期满时没达到"成功"：记为"不替换"，S4 留作对照或归档。**评估期内不允许修改任何参数。**

## 6. 回滚
- 方案 B（独立计划任务）：删除任务 `quant_shadow_s4_eod`，再删掉或归档 `position/live/shadow_s4/`。现有 v2 影子盘、paper 账本和群播报完全不受影响。
- 方案 A（改 daily.ps1）：用 git 撤回那一行，验证 `FROZEN_v2.json` 里的哈希不变（daily.ps1 本来就不在清单里）。
- 所有影子输出都只写在 `shadow_s4/` 下，不写 `portfolio.json`、`paper/books/s2|vt15`、`shadow_log.csv`。

## 7. 需要用户拍板的地方
1. 是否实施（是 / 否）。
2. 方案 A 还是 B（推荐 B）。
3. 是否加 BROAD_VT15 对照（推荐加：它是目前唯一胜过 S4 卫星的简单对照）、C3_60_S4、OBS-01 日志。
4. 第 5 节的门槛是否照用。
