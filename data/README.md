# data · 行情数据（研究与实盘共用）

**原则**：数据只在这里放一份；研究、影子、模拟盘都读这里。不同起点的缓存不要混用。

| 目录 | 内容 | 起点 | 谁在读 |
|---|---|---|---|
| `cache2019/` | base/ext 52 只 ETF 日线（腾讯/新浪，前复权） | 2019-01-02 | `indicators/code/backtest_composite.py`、`position/engine.py`、`replay_check.py` |
| `cache2007/` | long 指数 + 25 只蓝筹（2007 起） | 2007-01-04 | `gate_prereg.py`、`r10_prereg.py` 等 2007 段 |
| `cache_base/` | 旧 25 只 ETF 缓存 | 2010 前后 | 历史遗留，新代码默认不用；保留用于旧 run 复现 |
| `daily/` | 影子每日快照 `YYYYMMDD.json`（14:30 价、收盘价、失败明细） | 逐日 | `run_shadow.py` 写；`paper_account.py`、`v2_observ.py` 读 |

## 命名

- ETF：`sh510300.csv` / `sz159915.csv`（交易所前缀 + 6 位代码）。
- 指数：`idx_sh000300_20070101.csv`。
- 个股：`stk_sh600519_hfq_20070101.csv`。
- 每行：`date, close, amount`（腾讯接口；成交额缺失的旧数据为 NaN）。

## 更新

- 研究回测默认用缓存；缓存缺失或过期时由 `pools.load_one` 调 akshare 拉取并写回对应目录。
- `daily/` 由 `position/live/shadow/run_shadow.py` 每个交易日写入；非交易日不写。
- 缓存只增不删；换起点时新建目录（如 `cache2020/`），不要覆盖旧缓存。

## 注意

- `cache_base/` 是从旧根目录 `cache/` 复制来的，属于历史遗留；新回测优先用 `cache2019`/`cache2007`。
- 不要用 `work/` 下的旧缓存路径；`work/` 只放临时脚本和杂项。
- 数据快照末日：`cache2019` 为 **2026-11-02**；跨日重跑前先确认最后一根 bar 日期。
