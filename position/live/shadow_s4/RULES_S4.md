# S4 影子盘规则（只读引用，不在此处定义新规则）

- **S4（主臂）**：position/frozen_candidates/FROZEN_S4.md 原文。PIT 池（月底 60 日均成交额前 40，剔除货币/债券，上市≥250 日，次交易日生效）；
  20/60/120 日动量百分位均值；收盘 > MA60；同板块 ≤2；前 5；1/vol20；每 10 个交易日（rolling，相位自 W2013 起点）；VT15（60 日窗、≥40 样本，基于未缩放收益，每 10 日在执行日调整）；信号滞后 1 日、次日收盘成交；涨停买不进、跌停/停牌卖不出。
  代码：`components_core.sat_run(START_W["W2013"], cost, sector=True)`（运行器另取 base_ret 复算次日目标，并断言与 sat_run 的日收益逐位一致）。
- **BROAD_VT15（对照）**：runs/2026-11-21_sat_vt15_control/PREREG_ADDENDUM.md；代码 `sat_vt15_control.broad_vt_run`（510300/510500/159915/588000/512100，满 150 日入选，收盘>MA60 的等分 1/N_入选，× VT15 scale，与 S4 同相位每 10 日，次日收盘成交）。
- **C3（底仓）**：`components_core.core_run(lo, cost, mult=erp_mult())`，BASE8，月末信号次日收盘成交。
- **C3_60_S4（组合）**：`recombine_core.mix(C3, S4, 0.6)`，月末再平衡到 60/40。表里的组合持仓 = 0.6×C3 目标 + 0.4×S4 持仓（近似，月内漂移不计）。
- **OBS-01（只记录）**：position/frozen_candidates/OBSERVATION_LIST.md。DGS10 对齐 A 股日历，20 日变化 > 30bp 且滞后 1 日 → 触发；假设卫星仓位减半，t+2 生效。只记录假设收益 `hypo_s4_ret_if_half`，**不影响任何臂**。
- **成本账本**：5bp（主）与 20bp，单边。现金收益 0。
- **数据口径**：研究面板（≤2026-11-13）+ 腾讯日线实时延长（总收益 = 分段 k 法，同 long_history_backtest.tr_series）。每月第一次运行时对全部合格 ETF 重拉一次，保证月末 PIT 排名完整。
- **起点**：批准日 2026-11-21 之后的首个 S4 信号日（按当前日历为 2026-11-23，次日 11-24 收盘首次成交）；此前的行 status=pre-start。
- **评估标准**：PROPOSAL_S4_SHADOW.md 第 5 节，原文照用（R1–R3、S1–S3、K1–K4）。S1/S2 需要的 S2+VT15 同期数据取自现有 v2 影子盘（position/live/shadow），本目录不重算、不写入。
- **代码指纹**：FROZEN_SHADOW_S4.json（LF 口径 sha256）。每次运行比对，不一致 → fingerprint_ok=0，按 K3 处理。
