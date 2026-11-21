# 冻结：卫星权重扫描（2026-11-21 sat_weight_scan）

- 状态：**出结果前冻结**。冻结前只做过：(1) 冒烟测试（3 个起点，输出写到 /tmp，没看数字）；(2) 复现核对：从 2014-01-02 起，S4 = 10.66% / −24.92% / 0.428，S4+CREDIT = 12.22% / −16.92% / 0.722，与 parts_v2 的 bases.csv 一致。
- 问题：卫星从 0 加到 40%，有没有哪个**小权重**能提高年化、又不降低卡玛？
- **样本内**，用户授权的描述性扫描，不是预登记检验。不构成投资建议。

## 设计
- 卫星权重 w = 0 / 10 / 20 / 30 / 40%，底仓 = 1 − w。月末再平衡回目标比例（`recombine_core.mix`）。w = 0 就是纯底仓，用同一套 mix 约定。
- 卫星：
  - **S4**：FROZEN_S4，`components_core.sat_run(sector=True)`，含 VT15；
  - **S4+CREDIT**：S4 再乘社融乘数 `slowdecline.G["credit"]`，通过 `K.apply_mult` 在 t+2 生效，与 slowdecline/parts_v2 相同。社融信号 2017-01-16 之前恒为开；akshare 数据只到 2026-04，当前为关。
- 底仓：C3、DIV-IV。成本：5bp、20bp。
- 评估沿用 prereg_satellite：滚动起点 2014-06~2023-11 共 114 个，都算到 2026-11-13；4 个非重叠分段（G1 2014-06-03→2016、G2 2017–19、G3 2020–22、G4 2023→2026-11-13）。总收益、PIT、滞后 1 日、只做多。

## 报告内容
- 每个 w：卡玛、年化、回撤在各起点上的分布（中位数、10%/90% 分位、最差回撤）。
- 相对纯底仓：Δ年化、Δ卡玛、Δ回撤的中位数；Δ年化>0 的起点占比、Δ卡玛≥0 的起点占比、**两者同时成立**的起点占比；分段 Δ。
- **描述性门槛（事先写死）**：某个 w 如果"两者同时成立"的起点占比 ≥ 70%，并且 4 段中 ≥3 段两者同时成立，记为"小权重改善年化且不降卡玛"。

## 试验登记
2 卫星 × 2 底仓 × 4 个非零权重 = **16**（5/20bp 是同一试验的成本敏感性，不另计）。结果提交时登记 `SAT-WSCAN-01`。

## 命令与指纹
`python3 work/sat_weight_scan.py run` → `all_metrics.csv.gz`、`summary.csv`、`pure_core.csv`、`qualifies.json`、`REPORT.md`

| 文件 | sha256 |
|---|---|
| work/sat_weight_scan.py | 1ce0cc72037d52747ba9180aca24241a1a2b7d4141561c541fe8acfa1e5e235d |
| work/prereg_satellite.py | c362d280ffd53d1d4364db0196f4dbbacba8865929a3a43bf30fe2e3f2d8d51e |
| work/slowdecline.py | 2ebe399e0abbf2d1653c147e07edeffa347367c21f898c5cd956fbcb3c9df0e2 |
| work/ideas_v1.py | 48036439644f0d8cfbdb28e9309fd61d22588327fb52770a904e43859d63dd89 |
| work/components_core.py | 3146eb133dd836f076860d2a1f02d8d2b3257bc8e3984915f460eff442c44f41 |
| work/recombine_core.py | 522e86562659257f66c1ac1c23dd27085ee394e1cfa3f22d1a32eaf99d0771f0 |
| position/engine.py | a3f50ea1d57afbcb67352b5751dbfee2c7e275fa5d5a0cefedd97e1b4f11bd4f |
