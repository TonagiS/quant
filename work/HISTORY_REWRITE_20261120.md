# 历史重写对照表（2026-11-20）

> 公开仓库 `TonagiS/quant` 的提交历史里带过 QQ 号 / 群号，2026-11-20 用 `git filter-repo` 重写并强推清除。
> **结论：全部 26 个提交的 SHA 都变了；旧号一律作废，只在本表留底。** 代码与结果内容除下列 5 个文件外一字未改。

## 一、这次重写改了什么

- 脱敏 3 个文档：`HANDOFF.md`、`SUMMARY_20261120.md`、`position/live/shadow/README.md`（QQ 号 / 群号 → 「见本地 `group.json`」）
- 脱库 2 个文件：`work/poke_debug.log`（群号 / QQ 号 / poke 记录）、`work/history_inspect.txt`（聊天导出）
- 其余 blob 逐个比对：26 个提交、1000+ blob 内容一致，敏感串扫描 0 命中。

## 二、对冻结 / 预登记的影响

- `FROZEN_v1.json` / `FROZEN_v2.json` / `registry.csv` 里的 `code_sha256` 是**文件内容 SHA-256**，不是提交号；重写没改这些文件的字节，所以冻结核验照旧有效。
- 受影响的只有「文档里引用的提交号」：共 28 处，已全部就地换成新号，旧号在本表第三节留底。

## 三、旧号 → 新号（全量 26 条，按新号提交顺序）

| 旧号 | 新号 | 提交说明 |
|---|---|---|
| `bbeab65` | `9a04eba` | 初始提交：A股量化研究工作区（择时/仓位研究、回测脚本、结果账本） |
| `488abcd` | `1af0a1d` | docs: 交接说明（给外部协作者/Grok）——成果总览、方法纪律、别再跑清单、git 接入 |
| `3fae88c` | `7610331` | 2026-11-14 单只ETF仓位规则系列回测：全市场评估、分层v1、候选v2、长历史、消融v3、A-SV、A-FC及均线敏感性、超高波动个股、牛市开关、快速开关、G60快门槛、创业板/AI概念 |
| `e5ce54c` | `2a55913` | 2026-11-15 降息周期指标检验（rate_cycle）：PBOC政策利率/降准事件表、分regime描述、A开关回测、首降事件研究；结论不采用 |
| `ad53488` | `b911663` | 2026-11-15 缩量回踩+放量突破：预登记冻结（回测前） |
| `ca5d436` | `4e969a6` | 2026-11-15 缩量回踩+放量突破全A股回测（vol_breakout）：全A OHLCV 5001只+pre2006敏感性；逐笔无优势、组合远差于静态对照，不采用 |
| `2778e79` | `c2306eb` | 2026-11-15 红利-科技跷跷板：预登记冻结（回测前） |
| `c947dd5` | `92eaafc` | 2026-11-15 红利-科技跷跷板回测（div_tech_seesaw）：H00922 750日位置+2月确认；2014-10 后恒为 HIGH，信号近乎不切换；SEESAW 判定③未过（5/11），不采用 |
| `da8e083` | `a63b7ea` | research: 滚动起点复核 + 分层消融（ROLL-01 / ABL-01） |
| `1123e2a` | `dbef1f5` | research: 时代切片 2013-2018 vs 2019+（SPLIT-01）；2019+ 统一改标样本内/开发期 |
| `e79a9b1` | `edef8f6` | research: 等权基准按类别拆解（SPLIT-02）；撤回『新上市抬高』归因，改判分红口径 |
| `2dc387e` | `0549812` | 2026-11-20 PIT规则池+不选股分散臂：预登记冻结（回测前）；含旧数字复现校验 |
| `e2ca075` | `7e087c5` | 2026-11-20 PIT规则池+分散臂回测（pit_pool_diversified）：S2动量选股按预登记保留(3/3)但不显著、15-20bp打平；DIV-IV推荐作核心(3/3)，约59%国债；ext池复现基本吻合，宽92部分复现 |
| `89acace` | `5376376` | 2026-11-20 总收益口径统一 + VT15趋势门 + 参数取平均：预登记冻结（回测前） |
| `44eefcc` | `9389457` | 2026-11-20 总收益口径统一+VT15趋势门+参数取平均（tr_ens_trendgate）：全1693只ETF改总收益（补拉670只不复权价），ext S2 16.7%→12.6%，单支A结论不变；VT15-TG/S2ENS-VT15/S2ENS-VT15TG/A-ENS 均不采用；沪深300门（只报告）数字过线但不进判定 |
| `4a61a67` | `f147e40` | 2026-11-20 重新拼规则（recombine）：S2减法消融/底仓+卫星固定比例/显式最优组合搜索+PBO/DSR/滚动前推：预登记冻结（回测前） |
| `32f83bd` | `ea528b7` | 2026-11-20 重新拼规则（recombine）：S2减法消融3臂均不采用；底仓+卫星三档均优于单独卫星，仅80/20在5bp下2/3胜纯DIV-IV；1458组合最优=C80+N8+VT15-TG-300，PBO 0.04/DSR(N=1458)0.79/滚动前推卡玛0.72（输给固定80/20与纯DIV-IV），实质≈DIV-IV |
| `4fc079a` | `c562cec` | 2026-11-20 新组件逐个测试（components）：核心C1-C3/卫星S1-S4/风控R1-R2/全加/只加通过：预登记冻结（回测前）+数据拉取脚本 |
| `c1cc679` | `75b728f` | 2026-11-20 新组件逐个测试（components）：字面9/13采用但C1/C2/ALL-IN/ADOPTED靠短债降收益过线（漏洞）；实质有效仅C3股债性价比(核心,+0.6pp)与S4行业上限(卫星,卡玛0.39/0.35/0.63)；S1/S3不采用(S3换手-17%仍1/3)；R1@MIX从未触发；ERC改坐标下降求解（原乘法迭代不收敛，参考结果另存） |
| `a2024c7` | `af1ca78` | 2026-11-21 S4随机持仓零假设检验（s4_random）：预登记冻结（回测前） |
| `3d169f3` | `b6bdd8b` | 合并远端 SPLIT/ROLL 研究提交 |
| `9c87295` | `64d49da` | 2026-11-21 S4随机持仓零假设（s4_random）：S4对同约束随机p=0.004/0.025/0.012（显著打过）；在位对自身随机p=0.59/0.27/0.10（分不出）；板块上限单独不赚钱，S4增益来自上限×动量交互；MA60过滤价值大 |
| `bf50dbb` | `8fff4d4` | 冻结 S4（S2+同板块≤2+VT15）为影子观察候选：规则、依据与代码指纹 |
| `24dabc9` | `8c599b0` | ideas_v1: FREEZE + data pull script |
| `9e6bbe1` | `e6c119d` | ideas_v1: results + REPORT (in-sample) |
| `f1d83f4` | `4623358` | privacy: 文档不再出现 QQ/群号；桥调试日志与聊天导出脱库（群号改从外部 group.json 读） |

完整 40 位（`旧 新`，方便脚本查）：

```
bbeab65b047a54674b979cdb4e1a40b53dd4a36a 9a04eba6a2ebf2db8ad3053e16ce16299698ab52
488abcdc5af0eb1afe0cd7ecdc993625ecaf19cb 1af0a1dd3dc2c8963e61d447041091b772a282db
3fae88c74eb8bc537cb736091440fc25802632cc 7610331ae0a008cd707f500ea1eb9112505608ed
e5ce54ce9e7e82151f2863b326b51b7fd8545aad 2a5591382b4da63c134ae37d2ecf2a96b0eb8b27
ad534882d81fecfec6cdd4d313af0946dcb0028d b911663eebc164a81dd8a6ccd3e12dafd793d17a
ca5d4369264f62945a6a5389a902a5075fdd9e72 4e969a674e6a03125e9f1908770ec9b2307a2022
2778e794ee8f17b3abe6a8b1e8eb5c2afc857041 c2306eb2351697e7ef97fad7acfe08c22a99a098
c947dd557d7ae0fef247e96950106e54d601b71d 92eaafc656503fe1911b555c0e41e8302002cdff
da8e083d4c735b7c93ead630453807adaa15c6e8 a63b7ea113d34a8a8b1ff04275b32f82f81dc83b
1123e2a0b6fcfba037a551e49cdba3b3cf5a654e dbef1f56a938da5d85fc9cf3b4a2c3b5f641a248
e79a9b1cd6343e97ba66130b96827db069f06bc0 edef8f68de17ff3eda19b435dbd8a5bb54892b5c
2dc387e3422bb3769cfce612fc972144641bae90 054981283d5e51db2df495e3959b95063da97212
e2ca0753f6e1ef2020c7a45290f77c8c34caec97 7e087c5e321c3af416501fc0048332dd2fce2337
89acace77123ecb5fe189f86759333826bb5ec6a 537637681276b6fce63f0c17aec16a268138a284
44eefccc31780368c020fd2fe0746f5f3dfc5c9f 93894573d6d7355bc6e648aa10f1714466c9c12c
4a61a67e7ee9cc68f1f1df3cf0548da169be1425 f147e400b5990e9511636f5b450003f9fcb53b86
32f83bd2bab73b47ab9f345745863aa7b72f722c ea528b76e3b5b5bc5f27a89a07dc52a23dfacc52
4fc079acf61da11b73a42e1b72d0ba40400cd71a c562cecc786235536dae8d86f98ee425058ab769
c1cc6793ccb495b5adbcb1415f79146663e76efb 75b728fdff4c19b277f008cde8d43db020ed03e8
a2024c7900c1071a68a7af7c8691bccfc3c36701 af1ca7823a052c433ae10f150c172d71d936cbce
3d169f3439ab5068dee1516117eb12836d4d7ffc b6bdd8bd2cd935cbe1c3c32b09e1057e87857652
9c87295314880b829a3c8368289d5d83901feee9 64d49daf674d972d06de8de7eeadbc309e8b9d66
bf50dbb2f2d3df041be6b4bf7b067c6b4b485b5f 8fff4d43fc1f807369d3690344cf0be8b829daa1
24dabc94cbca31ca54f77046f2727b6be3f52708 8c599b0c94e51013bba88bac47daceda59ebcf90
9e6bbe1112432f2ec27bdd026ff454c59ebc9d98 e6c119de0f450b11063fbf32d8aa699397d67dd9
f1d83f4b1ff463e299fbaa9a2abeff53e1333d86 462335862db270fe6bf6e954d000d6ff9794626b
```

## 四、已就地改号的文件（25 个）

- `HANDOFF.md`
- `position/frozen_candidates/FROZEN_S4.md`
- `runs/2026-11-14_ablation_v3/snapshot.json`
- `runs/2026-11-14_candidates_v2/snapshot.json`
- `runs/2026-11-14_fastcap_ma_sens/snapshot.json`
- `runs/2026-11-14_fastcap_v5/snapshot.json`
- `runs/2026-11-14_layered_v1/REPORT.md`
- `runs/2026-11-14_layered_v1/snapshot.json`
- `runs/2026-11-14_long_history/snapshot.json`
- `runs/2026-11-14_statevol_v4/snapshot.json`
- `runs/2026-11-15_div_tech_seesaw/REPORT.md`
- `runs/2026-11-15_div_tech_seesaw/verdict.json`
- `runs/2026-11-15_vol_breakout/REPORT.md`
- `runs/2026-11-15_vol_breakout/verdict.json`
- `runs/2026-11-20_components/REPORT.md`
- `runs/2026-11-20_pit_pool_diversified/REPORT.md`
- `runs/2026-11-20_recombine/REPORT.md`
- `runs/2026-11-20_tr_ens_trendgate/REPORT.md`
- `runs/2026-11-20_tr_ens_trendgate/single/snapshot.json`
- `runs/2026-11-20_tr_ens_trendgate/verdict.json`
- `runs/2026-11-21_ideas_v1/FREEZE.md`
- `runs/2026-11-21_ideas_v1/REPORT.md`
- `runs/2026-11-21_s4_random/REPORT.md`
- `work/div_tech_backtest.py`
- `work/div_tech_summary.py`

运行快照（`snapshot.json`）另外保留了 `git_commit_before_rewrite` 字段，旧号不会丢；`verdict.json` / `REPORT.md` / 冻结文档里的引用只做等长替换。

## 五、以后怎么记代码指纹

- 用**文件内容 sha256**（`sha256sum` / `git hash-object`）——S4 冻结已经是这么记的，后续照此办理；
- 或整棵树的 tree hash（`git rev-parse <rev>^{tree}`）；
- **不要用 commit SHA**：历史一重写 / rebase / squash 就全废。
