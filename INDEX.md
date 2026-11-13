# INDEX · quant 回测与影子运行索引

**数据快照**：除特别注明外，所有 run 的行情末日为 **2026-11-02**（个别 ETF 最后一根 bar 停在 10-30，见各 run 的 `composite_meta.json`）。跨 run 的数字不能逐位比较，引用时必须带快照日。

**2026-11-04 修正**：POS-06/07/08、POS-03、`RULES_v2` 成本表曾因缺 `f["base_ret"]` 高估 VT15 基线；已修 engine 并重跑。**POS-07 原“通过”撤回，现为未通过**。详见 `work/REVIEW_20261104.md`。POS-01/02 也有 11-04 当前 engine 重跑副本（`runs/2026-11-04_POS-01` / `POS-02`），11-03 目录保留旧快照。

**2026-11-03 目录迁移**：研究代码 → `indicators/code/`，指标结果 → `indicators/results/`，预登记只读副本 → `indicators/frozen/`；实盘/模拟盘 → `position/live/`；行情缓存/每日快照 → `data/`。下表“命令”列是历史 run 的原始命令；现在从 run 目录执行时，脚本路径换成 `..\..\indicators\code\xxx.py`，实盘脚本换成 `position\live\...`。

**目录约定**
- 每个 run 只保留 6 个核心文件（脚本 / 控制台全文 / meta.json / rules.csv / portfolio.csv / 该轮主结果 csv），其余在 `extra/`。
- `indicators/frozen/`：v1 runner 归档 + 预登记只读副本（r10 / robust / gate）；v1 哈希见 `FROZEN_v1.json`；v2 见 `position/live/shadow/FROZEN_v2.json`。
- 预登记登记簿：`registry.csv`（根目录），每个 run 目录内有一份副本。
- `indicators/`：指标研究区——手册、catalog、45 规则快照、`code/`、`results/`、`frozen/`。
- `indicators/trend/`：**涨势/跌势指标分开研究**（2026-11-04 建）；预登记簿 `prereg/registry_trend.csv`，脚本目录 `code/`，产物 `results/`，参数与证据口径 `config.json`。尚未跑任何检验。
- `position/`：仓位管理——`engine.py`、`run_grid.py`、`advanced.py`、`PREREG.md`、`POLICY_v1.md`、`ADVANCED.md`、`SOURCES.md`；`position/live/` 放实盘/模拟盘。
- `data/`：行情缓存（`cache2019` / `cache2007` / `cache_base`）与每日快照（`daily`），研究与实盘共用。

| 目录 | 类型 | 命令 | 快照 | 一句话结论 | 核心文件 |
|---|---|---|---|---|---|
| `runs/2026-11-02b_long` | 组合回测 | `python indicators\code\backtest_composite.py --pool long --start 20070101` | 2026-11-02 | 15 只指数全历史；纯动量 5.6% 胜等权 3.3%，但趋势过滤反而降到 2.5% | `composite_*`, `console_long.log` |
| `runs/2026-11-02b_long+stocks` | 组合回测 | `... --pool long+stocks --start 20070101` | 2026-11-02 | 37 只；动量 5.4% ≈ 等权 5.3%，0 条规则过稳健判据 | 同上 |
| `runs/2026-11-02b_stocks` | 组合回测 | `... --pool stocks --start 20070101` | 2026-11-02 | 22 只；动量 5.3% < 等权 8.1%，胜随机 69.5% → 个股无动量 alpha | 同上 |
| `runs/2026-11-02b_r10_prereg` | 预登记 | `python indicators\code\r10_prereg.py` | 2026-11-02 | R10 10 日买入：个股池 NW −1.40、指数 2007-2018 段 1.49 → **未通过** | `console_r10.log`, `r10_result.csv` |
| `runs/2026-11-02_P1-ROBUST-01` | 预登记 | `python indicators\code\robust_prereg.py` | 2026-11-02 | S2 在 9 参数 + 剔除 + 逐年 + 滚动下五条判据全过（base、ext） | `robust_combos_*.csv`, `robust_summary_v2.csv` |
| `runs/2026-11-02_P1-GATE-01` | 预登记 | `python indicators\code\gate_prereg.py` | 2026-11-02 | MA120 闸门：回撤 4/5 池改善、卡玛 3/5 → **未通过** | `gate_pools.csv`, `gate_sensitivity.csv` |
| `runs/2026-11-02_P2-COST-01` | 预登记 | `python indicators\code\cost_prereg.py` | 2026-11-02 | **成本脆弱**：盈亏平衡 25/29bp（要求 ≥30bp），年化双边换手 3640%/3988% | `cost_grid_*.csv`, `cost_summary.csv` |
| `runs/2026-11-05_TREND-01` | 预登记 | `python indicators/trend/code/trend01_prereg.py` | 2026-11-02 | V-A/V-B 事件方向符合但 20 日 NW_t<3（最高 2.48）；替换 MA60 后卡玛 0.412/0.532 < S2 0.445/0.557 → **未通过** | `trend01_summary.csv`, `trend01_events_*.csv` |
| `runs/2026-11-03_P2-TURN-01` | 预登记 | `python indicators\code\turn_prereg.py` | 2026-11-02 | **未通过**：V1 保换手丢成本、V2 保成本丢换手（-21%/-24%）、V3 两头不讨好；V2 仅差 C3 | `turn_summary.csv`, `turn_meta.json` |
| `runs/2026-11-03_POS-01` | 仓位研究 | `python position/run_grid.py` | 2026-11-02 | 24 变体×2 池：波动目标 15% 卡玛 0.99/0.72（基线 0.56/0.45），回撤 -16.2%/-21.4%；回撤节流/止损/Kelly 无效或有害 | `summary_all.csv`, `bootstrap_all.csv`, `REPORT.md` |
| `runs/2026-11-04_POS-02` | 网上仓位方法 | `python position/advanced.py` | 2026-11-02 | 波动目标仍最佳；ERC+VT15 略降回撤（卡玛 1.01/0.76，Sharpe 无显著改善）；EWMA/CVaR 略差；CPPI 不适合 | `summary.csv`, `bootstrap_vs_vt.csv`, `REPORT.md` |
| `runs/2026-11-04_POS-03_offex` | 场外费率网格 | `python work/offex_grid.py` | 2026-11-02 | 修正后 ext 最佳 VT20 reb35 21.45%/-22.89%/0.94；base VT15 13.51%/-19.40%/0.70；5 日档被赎回费打掉约一半收益 | `REPORT.md`, `REPORT_EXT.md`, `summary_all.csv` |
| `runs/2026-11-04_POS-05_dd` | 预登记 | `python work/pos05_prereg.py` | 2026-11-02 | VT15+MA200 半仓+20%国债：11.79%/-16.35%/0.72；③④⑤ 未过 | `PREREG.md`, `REPORT.md`, `grid.csv` |
| `runs/2026-11-04_POS-06_adx` | 预登记 | `python work/pos06_prereg.py` | 2026-11-02 | 修正后 16.61%/-14.73%/1.13，扰动最差 0.84，③④⑥ 未过 → **未通过** | `PREREG.md`, `REPORT.md`, `grid.csv` |
| `runs/2026-11-04_POS-07_adx` | 预登记 | `python work/pos07_prereg.py` | 2026-11-02 | 修正后 16.61%/-14.73%/1.13，③④⑥ 未过；**原“通过 1.21”因 base_ret 缺失撤回** | `PREREG.md`, `REPORT.md`, `grid.csv` |
| `runs/2026-11-04_POS-08_adx_pct` | 预登记 | `python work/pos08_adx_pct.py` | 2026-11-02 | 固定20 16.61%/1.13；分位30% 16.63%/-14.80%/1.12，扰动最差 0.89，③④⑥ 未过 → 未通过 | `PREREG.md`, `REPORT.md`, `grid.csv` |
| `runs/2026-11-04_POS-09_buffer` | 预登记 | `python position/buffer_vt.py` | 2026-11-02 | VT15+buffer7：ext 全过（18.37%/1.20、盈亏 50.8bp），base C3 换手比 0.826 差一点未过；buffer8 仅敏感性 | `PREREG.md`, `REPORT.md`, `summary.csv` |
| `runs/2026-11-04_POOL-01_expand` | 扩池实验 | `python work/pool_expand.py` | 2026-11-02 | 候选不足 10.7%→2.3%，但 VT15 年化 14.32%→5.75%~8.31%；**维持 52 只** | `REPORT.md` |
| `runs/2026-11-04_MACRO-01_failed` | 数据可行性 | `python work/*macro*` | 2026-11-04 | 股债性价比/融资余额/涨停家数三指标全部否决；方向不变，VT15 数字待复核 | `REPORT.md` |
| `runs/2026-11-04_SIG-01_crossasset` | 跨资产 | `python work/cross_asset_test.py` | 2026-11-02 | 69 只长周期 10.05%/0.27，52 只 V2 16.03%/0.70；不扩池、不改信号 | `REPORT.md` |
| `position/live/shadow/` | 影子运行 | `position\live\shadow\daily.ps1 -phase 1430\|eod` | 逐日 | v1 归档、v2（14 自然日 + 场外分档）运行；原计划任务迁移后查询不到，需重新注册 | `shadow_log.csv`, `v2_observ.csv`, `FROZEN_v2.json` |
| `position/live/decision_1430.py` | 主脚本 | — | — | 2026-11-03 改版：删拥挤度/顶部结构/量能确认，保留动量/60日线/逆波动 | sha 065002b9… |
| `position/live/paper/` | 模拟盘 | `python position\live\paper_account.py --execute\|--mark` | 每交易日 | s2 / vt15 双账本各 10 万，场外分档费；vt15_adx 账本已归档撤下 | `books/s2`, `books/vt15` |
| `position/live/shadow/v2_observ.csv` | 观察臂 | 每日 15:10 由 daily.ps1 调用 | 逐日 | 只记换手/滑点；首日换手 1.0、滑点 +31.5bp | `v2_observ.csv` |
| `runs/2026-11-02_T4-SHARE-COVER` | 数据可行性 | `python work/t4_share_cover.py` | 2026-11-02 | 沪市 40 只 ETF 份额覆盖 90%、20/40 全样本可用；深市无可用源 | `share_cover.csv`, `share_code_coverage.csv` |

**运行环境**
- Python：`venv\Scripts\python.exe`（3.11.8，akshare 1.20.1）。
- 缓存：`data/cache2019`（2019 起点）、`data/cache2007`（2007 起点）、`data/cache_base`（旧 25 只 ETF，遗留）。
- 2026-11-03 重整：原根目录 `cache/` 复制为 `data/cache_base`；根目录旧输出移到 `indicators/results/`；权限阻塞已解除。
