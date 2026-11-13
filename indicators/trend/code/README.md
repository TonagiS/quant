# code/ 约定

- 一个假设一个脚本：`td_<侧别>_<编号>_<简称>.py`，例如 `td_down_01_atr_pct.py`。
- 只读复用：`sys.path` 指到 `indicators/code`，直接用 `backtest_composite`、`pools`、`macd_structure` 的函数；不复制、不改写。
- 数据只读 `data/cache2019`、`data/cache2007`，不重新取数（快照 2026-11-02）。
- 脚本必须自带：参数打印、数据区间/切分、失败名单、逐条判据的通过/未通过、结果 CSV 落 `results/<日期>_<编号>_<名称>/`。
- 随机对照沿用主项目口径（随机打分/移位随机），不要自创。
