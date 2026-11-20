# 冻结与提交号审计（2026-11-21 frozen_hash_audit）

- 依据：`work/HISTORY_REWRITE_20261120.md`（26 条旧号 → 新号对照）。旧历史在本地还能找到（`backup/pre-rewrite-20261121` 分支和本地对象库），除 `f1d83f4` 外都在。
- 原则：**只报告，不修改**任何冻结文件（`position/live/` 下的文件一个都没动）。
- 复核脚本：`sha_check.sh`（只读）；输出见 `sha_check.txt`。

## 1. 重写是否只动了提交号和隐私文件、提交顺序有没有变
共 26 对，其中 25 对可以比对；`f1d83f4`（旧的隐私提交）本地没有这个对象，无法比对。

对这 25 对逐个检查：
- **父提交映射**：25/25 一致。新提交的父提交正好是旧父提交映射过去的新号，所以顺序和合并结构都没变。
- **作者、日期、提交说明**：25/25 完全一致。
- **文件树差异**：25/25 只涉及重写说明里列的 5 个文件：`HANDOFF.md`、`SUMMARY_20261120.md`、`position/live/shadow/README.md`（各改 1 行，QQ 号和群号被换掉）、`work/history_inspect.txt`、`work/poke_debug.log`（从库里删除）。其余文件一字未改，冻结文件和代码都在其中。

重写之后的改号提交 `9bfc7ec`：
- 其中 17 个文件只是把旧号换成新号，而且是等长替换：做完替换后与新文件逐字节相同。
- 9 个 `snapshot.json`：`git_commit` 换成新号，并新增一个字段 `git_commit_before_rewrite` 保存旧号，此外没有其他改动。
- `HANDOFF.md`：一处起点提交号改成新号，另加一行指向对照表的说明。
- 新增 `work/HISTORY_REWRITE_20261120.md`。
- **没有任何规则或数字被改动。**

2026-11-21 的本地重排（slowdecline 的 3 个提交）：
- `d43dc84 → cd96eff`、`deaab83 → cf50bd0`、`3805b81 → e324f83`，三对的 `git patch-id` 完全相同，说明改动内容一样。
- 随后的 `c31e50b` 只改了 slowdecline 的 REPORT：把冻结提交号写成"cd96eff（重写前本地号 d43dc84）"，并删掉一处用户消息编号。

## 2. 冻结提交是否早于对应的结果提交（新历史）
| 运行 | 冻结 → 结果 | 冻结是祖先 | 冻结文件在结果提交前被改过 | 之后被改 |
|---|---|---|---|---|
| vol_breakout | b911663 → 4e969a6 | 是 | 否 | 否 |
| div_tech_seesaw | c2306eb → 92eaafc | 是 | 否 | 否 |
| pit_pool_diversified | 0549812 → 7e087c5 | 是 | 否 | 否 |
| tr_ens_trendgate | 5376376 → 9389457 | 是 | 否 | 否 |
| recombine | f147e40 → ea528b7 | 是 | 否 | 否 |
| components | c562cec → 75b728f | 是 | 否 | 否 |
| s4_random | af1ca78 → 64d49da | 是 | 否 | 否 |
| FROZEN_S4（依据 64d49da → 冻结 8fff4d4；之后影子观察） | 8fff4d4 | 是（依据在前，按设计） | — | 只有 9bfc7ec 等长改号 |
| ideas_v1 | 8c599b0 → e6c119d | 是 | 否 | 只有 9bfc7ec 等长改号 |
| slowdecline（含四起点表） | cd96eff → cf50bd0、e324f83 | 是 | 否 | 否 |
| ext_rerun | b47675a → 98d8ea3 | 是 | 否 | 否 |
| prereg_satellite（本次） | f899349 → 843a8aa | 是 | 否 | 否 |

git 无法证明的部分：
- **FROZEN_v1 / v2**（2026-11-03、11-04 冻结）早于仓库建立（首个提交 9a04eba，2026-11-14），git 只能证明这些文件从首个提交起没被改过。
- **ROLL-01 / ABL-01 / SPLIT-01/02、rate_cycle、analyst_revision** 的冻结和结果在同一个提交里，或者没有单独的冻结提交，git 无法证明先后。
- git 只能证明提交的先后，不能证明结果是在冻结提交之后才生成的。有几次间隔只有 2–17 分钟。

## 3. 冻结文件 sha256 复核（当前工作区）
| 清单 | 文件 | 结果 | 分类 |
|---|---|---|---|
| FROZEN_v1 | indicators/frozen/RULES_v1.md | 一致 | — |
| FROZEN_v1 | indicators/frozen/run_shadow_v1.py | LF 不一致；**换成 CRLF 后一致** | 只是换行符（Windows 冻结、git 存 LF） |
| FROZEN_v1 | indicators/frozen/universe_v1.csv | 同上，CRLF 后一致 | 只是换行符 |
| FROZEN_v1_legacy | shadow/RULES_v1.md、engine.py | 一致 | — |
| FROZEN_v1_legacy | shadow/universe_v1.csv | CRLF 后一致 | 只是换行符 |
| FROZEN_v1_legacy | shadow/run_shadow.py（f82e47ad…，v1 迁移后的版本） | **不一致** | 已被 v2 取代、预期内：FROZEN_v2 记录 run_shadow.py 改动后重新冻结，当前文件与 v2 的哈希一致（CRLF）。v1 迁移后的那一版不在仓库里，**无法 diff** |
| FROZEN_v2 | shadow/rules_v2.json、engine.py | 一致 | — |
| FROZEN_v2 | shadow/run_shadow.py、universe_v1.csv | CRLF 后一致 | 只是换行符 |
| FROZEN_v2 | shadow/RULES_v2.md | **不一致**（LF 和 CRLF 都不一致） | **事后加了注释，规则没改**：`tools/legacy/fix_pos_refs.py` 在 2026-11-05 插入一段"2026-11-05 口径核对"（第 39 行，说明 VT15 基准口径 16.50%/1.00、15.85%/0.72 来自哪一套窗口）。删掉这一段后 sha256 = 09221143852c05de…，**与冻结值完全一致**。不是改哈希字符串，也不是只改空白，但不涉及任何规则或参数 |
| FROZEN_S4 | work/components_bt.py、components_core.py | 一致（前 16 位） | — |

- 这些文件从 9a04eba 起在 git 里都没有再改过：`indicators/frozen/` 和 `position/live/shadow/` 之后只有隐私提交改过 README.md。两次重写也都没碰到它们。
- **结论**：没有发现实质性的规则改动。DSH 待办第 4 项（v1/v2 sha 不一致）可以解释为：4 个文件只是 CRLF/LF 换行差异；RULES_v2.md 是 11-05 加了一段口径注释；legacy run_shadow.py 已被 v2 取代。
- 建议（未执行，需要拍板）：在 FROZEN_v2.json 旁边补一条说明，或者记录 LF 口径的哈希。按要求没有改任何冻结文件。
