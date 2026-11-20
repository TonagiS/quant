# ext_rerun 冻结说明（2026-11-21，先提交后运行）

用户 pt72u：「再看看这些口径在 s2+vt15 的 ext 舒适区跑的咋样」。
- 池：原 ext 固定 52 只（position/pools.get_pool("ext")），总收益价 panel_tr2，**事后挑选的池（幸存者/选择偏差），结果偏乐观**；无 PIT 成员约束（全期都可选）。
- 窗口：EXT19 = 数据截断到 2019-01-02、保留 ≥300 个观测的标的（同 pit_pool_repro 复现口径，预热后约 2019-08 起）；EXT17 = 截断到 2017-01-01（只用当时已有数据的标的）。都到 2026-11-13。
- 对照：同一套臂在 PIT 前 40 池上跑 W2019（2019-01-02）、W2017，以及与 EXT19 同起点的 PIT 对照。
- 臂：S2、S2+VT15、S4（同板块≤2）、S2+VT15+CREDIT、S4+CREDIT、S4+CREDIT+STOP12、S4+CREDIT+CNY_1P、S4+CREDIT+OFF03+STOP12；C3 50/60/80% × {S4+CREDIT+STOP12, S2+VT15}。规则同 slowdecline（复用 work/slowdecline.py）。滞后 1 日；5bp / 20bp。
- 报告：CAGR/MDD/Calmar、2023+（短样本）、2021-02-18→2024-02-02 危机段、年化双边换手。样本内，不构成投资建议。
