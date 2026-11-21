# 冻结清单哈希说明（FROZEN_v1 / FROZEN_v1_legacy / FROZEN_v2 的附注，2026-11-21）

> 这是**附注文件**，不修改任何冻结清单或冻结文件本身。审计全文：[`runs/2026-11-21_frozen_hash_audit/AUDIT.md`](../../runs/2026-11-21_frozen_hash_audit/AUDIT.md)，复核脚本 `runs/2026-11-21_frozen_hash_audit/sha_check.sh`（只读）。
> 适用：本目录 `FROZEN_v1.json`、`FROZEN_v1_legacy.json`，以及 `position/live/shadow/FROZEN_v2.json`。按 "不动 position/live/" 的规定，本附注放在这里，没有放到 `position/live/shadow/`。

## 1. CRLF 与 LF
- 冻结清单是在 Windows 工作区（CRLF 换行）里算的 sha256。git 仓库存的是 LF，所以在 Linux 检出后，部分文件的哈希会对不上。
- 把文件换成 CRLF 再算，就和清单完全一致。**只是换行符不同，内容没变。**

| 文件 | 清单值（CRLF 口径） | LF 口径 sha256（仓库 / Linux 检出） |
|---|---|---|
| indicators/frozen/run_shadow_v1.py | e7a8e9b0fbbdcf6ead6a72e382da607b7f8bb985bd065d40680a44ed8d66ee06 | c752827d4642c6d48a654c7954a94d4bbc278821e11af4cc07776ff9ff2c0531 |
| indicators/frozen/universe_v1.csv | 0539ca147dbb6dc829a59c3282ed0c6a1e652e9c462289aa9e1513b14260e884 | d0f069fd34cfbce470030787047138dc6f9a3566f12842023c3effe334a04859 |
| position/live/shadow/universe_v1.csv | 0539ca147dbb6dc829a59c3282ed0c6a1e652e9c462289aa9e1513b14260e884 | d0f069fd34cfbce470030787047138dc6f9a3566f12842023c3effe334a04859 |
| position/live/shadow/run_shadow.py（v2） | 2b4f3a0c2a2eb593a28874f326c87b73a0a0a185893622a99fa9879fd244fd13 | ec14851350a5a4c813f3d648dea0527a38fe470d72632f0d02049d9324f20139 |

其余被固定的文件（RULES_v1.md ×2、engine.py、rules_v2.json）在 LF 口径下直接一致。

## 2. RULES_v2.md：事后加了注释，规则没改
- 当前文件的 sha256 是 4ea676c7…（LF）。不管 LF 还是 CRLF，都和清单值 09221143852c05de… 对不上。
- 原因：`tools/legacy/fix_pos_refs.py` 在 **2026-11-05**（冻结后一天）插入了一段注释，以 "> **2026-11-05 口径核对（对照 registry.csv）**" 开头（当前第 39 行），内容是 VT15 基准的口径说明（16.50%/1.00、15.85%/0.72 来自哪一套窗口）。
- **删掉这一段后，sha256 = 09221143852c05de68a1c624c1d2afe9c336423aefe519d07e3268b36dfda70c，和冻结值完全一致。** 所以规则和参数都没改，只是加了一段说明。

## 3. FROZEN_v1_legacy 里的 run_shadow.py（f82e47ad…）
- 这是 v1 迁移目录后的版本，已经被 v2 重新冻结取代（见 FROZEN_v2.json 的 note）。仓库里没有这一版，无法 diff。
- 当前的 run_shadow.py 与 v2 清单一致（CRLF 口径）。

## 结论
没有实质性的规则改动。DSH 待办第 4 项（v1/v2 sha 不一致）已于 2026-11-21 **结项**。
