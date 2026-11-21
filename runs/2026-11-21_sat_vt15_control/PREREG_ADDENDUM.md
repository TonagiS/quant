# 预登记附录：卫星对照加入 VT15 版（2026-11-21 sat_vt15_control）

- 状态：**回测前冻结**。本附录和 `work/sat_vt15_control.py` 先提交、推送，结果在之后的提交里。冻结前只做过冒烟测试：每种随机 2 个种子、3 个起点，输出写到 /tmp，只检查行数和缺失值，没有看任何指标。
- 母预登记：`runs/2026-11-21_prereg_satellite/PREREG.md`（冻结 f899349）。**除下面列出的地方外，全部照母预登记执行**：60/40 固定；C3 与 DIV-IV 两版底仓；114 个滚动起点（2014-06~2023-11，每个都算到 2026-11-13）；4 个非重叠分段；5/10/20bp；总收益、PIT、滞后 1 日、只做多。
- 为什么做：母检验的宽基对照没有叠 VT15，S4 的领先里可能有一部分来自 VT15。这次要把"选股 + 板块上限"和"VT15"的贡献拆开。注意：母检验的随机卫星（N1）本来就带 VT15。
- 样本内，不构成投资建议。

## 对照卫星
| 代号 | 定义 | 角色 |
|---|---|---|
| BROAD_MA60_VT15 | 母检验的宽基 5 只等权 + MA60（510300/510500/159915/588000/512100）。每个调仓信号日把目标权重乘以 scale = clip(0.15 / 年化σ, 0, 1)。σ 用**未缩放**宽基卫星（同成本）近 60 日的日收益算；有效样本少于 40 天时 scale=1。剩下的放现金，s+1 收盘成交 | **主对照** |
| RANDOM_N1（VT15） | 与母检验相同：同约束随机（MA60 + 每板块 ≤2）+ VT15，种子 20000–20199，取 200 个种子的中位数 | **主对照** |
| S2_VT15 | 在位策略，S4 去掉板块上限，其余完全相同（`components_core.sat_run(..., sector=False)`，含 VT15） | **单独判定**：检验板块上限的贡献 |
| RANDOM_N3（VT15） | 随机 + MA60，不设板块上限，+VT15，种子 21000–21199，中位数 | 只报 |
| BROAD_MA60（无 VT） | 母检验的对照，复现用 | 只报 |
| 纯底仓 | 次要 | 只报 |

## 判定（照搬母预登记）
- 对每个对照：起点 Δ>0 占比 ≥ 70%，且 Δ 中位数 > +0.05，且 4 段中 ≥3 段为正。
- **主判定（5bp）**：某个底仓对 BROAD_MA60_VT15 和 RANDOM_N1 两个都通过，记为该底仓"通过"；两个底仓都通过才算整体通过。
- **"S4 胜过 S2+VT15"**：用同样的三条，单独给出结论（5bp 为主，另说明 20bp）。
- 20bp 单独说明，10bp 只报。不因结果改任何东西。

## 试验登记
新增比较：2 个底仓 × 3 个判定对照（BROAD_MA60_VT15、RANDOM_N1、S2_VT15）= **6**。RANDOM_N1 与母检验是同一组种子，不算新随机试验。结果提交时在 registry.csv 登记 `SAT-VT15-01`，试验数 6。

## 产出与命令
`python3 work/sat_vt15_control.py run` → `all_metrics.csv.gz`、`start_diffs.csv`、`verdict_table.csv`、`verdict.json`、`REPORT.md`

## 代码指纹（sha256）
| 文件 | sha256 |
|---|---|
| work/sat_vt15_control.py | 5c871d95ab1ca585ea1512c2a083eb125edcf45c8f1bdc958c0ca1acf8ed8f6d |
| work/prereg_satellite.py | c362d280ffd53d1d4364db0196f4dbbacba8865929a3a43bf30fe2e3f2d8d51e（与母预登记一致） |
| work/components_core.py | 3146eb133dd836f076860d2a1f02d8d2b3257bc8e3984915f460eff442c44f41 |
| work/s4_random_null.py | 12ba59b6b8787cb78f6625da7f4132cfb1531878633ff42f6e0466478c7bb8e8 |
| work/recombine_core.py | 522e86562659257f66c1ac1c23dd27085ee394e1cfa3f22d1a32eaf99d0771f0 |
| work/pit_pool_backtest.py | 01f1f2eda47bf277e2031b89ace34c6b5f75c03d7b905230559ad97a4289d883 |
| position/engine.py | a3f50ea1d57afbcb67352b5751dbfee2c7e275fa5d5a0cefedd97e1b4f11bd4f |
