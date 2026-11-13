# 仓位控制方法 · 网上来源（2026-11-03 检索）

> 只引用可访问的原文；公式以原文为准，参数在 `ADVANCED.md` 里写我们实际用的值。
> 外部内容仅作方法参考，不构成投资建议。

| 方法 | 来源 | 关键内容 |
|---|---|---|
| 波动率目标 / EWMA / GARCH | [Quantpedia: An Introduction to Volatility Targeting](https://quantpedia.com/an-introduction-to-volatility-targeting/) | 杠杆 = 目标波动 / 已实现波动；EWMA 给近期更高权重；建议 t-2 数据避免前视；最大杠杆可封顶；波动目标能平滑波动、在股票/信用上常提升 Sharpe |
| 风险平价：逆波动 / ERC / 最大分散化 | [Quantpedia: Risk Parity Asset Allocation](https://quantpedia.com/risk-parity-asset-allocation/) | 逆波动是"naive risk parity"；ERC 用 126 日协方差做凸优化，让每只资产风险贡献相等；最大分散化最大化"加权波动 / 组合波动" |
| CPPI | [Quantpedia: Introduction to CPPI](https://quantpedia.com/introduction-to-cppi-constant-proportion-portfolio-insurance/) | floor、cushion=NAV-floor、风险资产=M×cushion；有"最大回撤 floor"（floor=峰值×(1-最大回撤)）和动态乘数变体；缺点是有 gap risk、交易成本 |
| 波动率管理组合（学术） | [Moreira & Muir, Volatility-Managed Portfolios (NBER executive summary)](https://conference.nber.org/conf_papers/f88104/f88104.executive_summary.pdf) | 按已实现方差缩敞口，可在不牺牲平均收益的情况下提升风险调整收益（原文为 PDF，本仓库只作方向参考） |
| VaR / CVaR 风险预算 | [Quantpedia: An Introduction to Value at Risk Methodologies](https://quantpedia.com/an-introduction-to-value-at-risk-methodologies/) | VaR 是给定概率下的最大损失；CVaR 是尾部平均损失；风险平价里可用 VaR/CVaR/CDaR 替代波动作为风险度量 |
| Kelly / Optimal F | [Quantpedia: Beware of Excessive Leverage – Kelly and Optimal F](https://quantpedia.com/beware-of-excessive-leverage-introduction-to-kelly-and-optimal-f/) | Kelly 按 edge/odds 定仓位，实务常用半 Kelly；估计误差大时容易过度下注（本仓库 POS-01 已测，半 Kelly 有害） |

## 我们没测或没条件测的

- **ATR / 百分比风险模型**：需要 high/low 数据；当前 `data/cache2019` 只有 close/amount，所以没有硬造 ATR。要测得先补 high/low 缓存。
- **HRP（层次风险平价）**：需要聚类/递归二分，5 只标的的边际收益很小；本轮先用 ERC/最大分散化代替。
- **GARCH(1,1)**：需要 `arch` 包；本轮用 EWMA 近似（Quantpedia 也把 EWMA 作为 GARCH 的轻量替代）。
- **杠杆版波动目标**：本仓库 long-only、不加杠杆，杠杆上限固定 1.0；原文允许 cap=2，和我们的产品约束不同。
