# results/ 命名

`results/<YYYY-MM-DD>_<编号>_<简称>/`，与主项目 `runs/` 同构。每次运行至少放：

- `console.log`（完整输出）
- `meta.json`（参数、数据快照、切分、失败名单）
- 该轮的规则表 CSV（`rules_*.csv`）与组合对照 CSV（`portfolio_*.csv`）
- 预登记行的副本（`registry_row.csv`）

不覆盖历史目录；失败的运行也保留。
