# 新缓存复现检查（data-cache-2026-11-14）

- 仓库提交：`3fae88c`（2026-11-14 单只ETF仓位规则系列回测），拉取 201 个文件 / +728049 行
- 数据：release `data-cache-2026-11-14`，`quant_data_cache_2026-11-14.tar.gz` 184.46 MB，SHA256 `d22c96f7eeea88485490c4f556803a3e54bc9ca6de3e833c2143ca6c8036cb45`（与 `SHA256SUMS.txt` 一致）
- 解压后新增/覆盖：`data/cache_ohlc_full`、`cache_ohlc_fullhist(_raw)`、`cache_ohlc_raw_2017`、`cache_index_proxy`、`cache_cross_market`、`cache_highvol`、`cache_etf_probe.json` 等
- 复跑：13 个 `work/*backtest*.py` + `statevol_v4_episodes.py` + `fullmkt_single_asset.py eval`，全部 exit=0，总耗时约 11 分钟
- 比对方式：工作区产物 vs `HEAD` 已提交产物，逐 csv 比行数/单元格，并对差异做「多重集合」判定（区分真实改动与行序变化）

## 结论

**复现了，还是老结果。** 全部结论文件（`summary.csv`、`summary_by_group.csv`、`pairwise.csv`、`comparison_*.csv`、`theme_*.csv`）与提交版本**逐字节相同**；明细表差异只落在三类非实质性差别上：

1. 浮点末位：最大绝对差 `2.8e-13`（如 `med_turn`、`calmar`、`xmkt_calmar`），属 numpy/pandas 求和顺序导致的表示差异
2. 行序不同、行内容相同：`bull_switch`、`bull_fast`、`highvol_stocks`、`fc_fastgate` 的 `results_long.csv` 等（多重集合比对结果为 `same rows, different order only`）
3. 列序不同、列集合相同：`fc_fastgate/diagnostics_by_name.csv`（130 列，集合一致）、`bull_switch/coverage_timing.csv`

没有任何一处出现「结论方向」级别的变化：没有新增/消失的标的、没有从正变负的中位改善、没有不同的过滤计数。

## 明细（逐脚本）

- `fullmkt_single_asset.py eval`：1720 只、951 只截面，11 个 csv 全部 identical
- `layered_v1_backtest.py`：7 identical、2 changed（`exclusions.csv` 6 格 AUM 浮点、`results_long.csv` 70 格 3e-14）
- `long_history_backtest.py`：10 identical、6 changed（全部 ≤7.9e-16）
- `statevol_v4_backtest.py` / `statevol_v4_episodes.py`：仅浮点末位（≤2.8e-13）
- `fastcap_v5_backtest.py` / `fastcap_ma_sens.py` / `ablation_v3_backtest.py` / `candidates_v2_backtest.py`：仅浮点末位
- `fc_fastgate_backtest.py`：`summary.csv` identical；`diagnostics_by_name.csv` 列序不同、`results_long.csv` 行序不同
- `bull_switch_backtest.py` / `bull_fast_backtest.py`：`summary.csv` identical；明细行序/列序不同
- `highvol_stocks_backtest.py`：`summary.csv` identical；`coverage.csv`/`results_long.csv` 行序不同
- `chinext_ai_backtest.py`：`summary.csv`/`results_long.csv` 全部 identical

原始逐文件比对日志：`repro_all.log`（本目录）。

## 对新数据方向的说明

这次 release 的缓存与本地原有缓存在数值上是同一批（复跑结果不变即为证据），所以 HANDOFF §4 里那些「未测方向」并没有因为这份缓存而变得可测：

- 国家队/ETF 份额、期货基差、两融余额仍然没有对应缓存，`data/national_team` 之外没有新增字段
- `cache_cross_market`（Yahoo 跨境）确实完整，但 2026-11-14 系列已经把它用满（`xmkt` 面板）
- 真要开 §4，需要新的数据源，不是重跑同一份缓存

## 复现方法

```
# 1. 拉缓存（GitHub 直连被限速/卡死，走镜像 + 2MB 分片 6 并发）
powershell -NoProfile -File work\cache_dl2.ps1

# 2. 解压 + 复跑 + 比对
powershell -NoProfile -File work\cache_round1.ps1
powershell -NoProfile -File work\repro_all.ps1   # 13 个脚本，约 11 分钟

# 3. 单目录比对
.\venv\Scripts\python.exe work\repro_diff.py runs/2026-11-14_layered_v1
.\venv\Scripts\python.exe work\repro_multiset.py runs/2026-11-14_fc_fastgate
```

本报告不是投资建议。
