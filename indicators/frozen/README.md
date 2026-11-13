# indicators/frozen · v1 归档与只读副本

- `run_shadow_v1.py`：v1 影子运行器，从 `work/pre_reorg_20261103.zip` 解出（2026-11-04），仅归档，不再运行。
- `RULES_v1.md` / `universe_v1.csv`：v1 规则与成份快照副本。
- `FROZEN_v1.json`：上述文件的 SHA256（v1 正式 freeze manifest）。
- `FROZEN_v1_legacy.json`：迁移前 `position/live/shadow/FROZEN.json` 的历史副本；其 `run_shadow.py` hash 对应迁移前的 v1 路径版，**已失效，仅留档**。
- `gate_prereg.py` / `r10_prereg.py` / `robust_prereg.py`：三个预登记脚本只读副本。

当前生产 runner 是 `position/live/shadow/run_shadow.py`（v2），哈希见 `position/live/shadow/FROZEN_v2.json`。
