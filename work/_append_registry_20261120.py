# -*- coding: utf-8 -*-
"""把 2026-11-20 的两个 run 登记进 registry.csv（追加，不改已有行）。"""
import csv
import hashlib
import os

REG = r"E:\Code\quant\registry.csv"
ROOT = r"E:\Code\quant"


def sha(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest().upper()


rows = [
    dict(
        id="ROLL-01",
        登记时间="2026-11-20 16:58",
        假设="既有『宽池上策略打不过等权』是否由 4 个自然年起点的选择造成；VT15 降回撤是否在每个起点都成立",
        宇宙="宽ETF池92只(2013-01~2026-11-13) + 实盘ext池52只(2019-09~2026-11-19)，两个池分开报",
        冻结参数="S2/VT15 k=5 reb=10 rolling delay=1 单边5bp；VT15=min(1,15%/vol60)；每月首个交易日起点，窗口>=756交易日；BH=同池等权每日再平衡零成本",
        判据="R1 宽池 S2-BH 年化中位<0；R2 ext S2-BH>0；R3 VT15回撤更浅起点比例>=80%(两池)；R4 VT15卡玛更高>=70%(两池)",
        运行命令=r"venv\Scripts\python.exe runs\2026-11-20_roll_starts\roll.py",
        结果="129/50 个起点：R1 -3.11pp(为正1.6%)✓；R2 +12.10pp(100%)✓；R3 100%/100%✓；R4 100%/100%✓。诊断：VT15卡玛-BH 宽池 -0.040(仅14.7%为正)、ext +0.704；VT15年化-S2 中位 +1.64pp/+0.97pp",
        判定="通过（四条全过）。四条结论：宽池跑输是系统性的不是起点挑的；ext 超额=选池红利不可外推；VT15 降回撤 129/129 且 50/50 无例外；但 VT15 救不回选股负信息，宽池上仍追不上等权",
        备注="不是样本外，同一批历史数据的稳健性复核。起点分布 P25~P75 在 ±1pp 内。ext 52 只 2019-09~2023-10 共 50 起点（末段窗口不足 3 年按预登记丢弃）",
        code_sha256=sha(os.path.join(ROOT, "runs", "2026-11-20_roll_starts", "roll.py")),
        试验数="338",
    ),
    dict(
        id="ABL-01",
        登记时间="2026-11-20 16:58",
        假设="分层归因：动量选前5 / 逆波动权重 / VT15 择时，各自对组合的净贡献是多少（含 VT 在等权基座上的边际价值）",
        宇宙="宽ETF池92只(2013-01~2026-11-13，13.35年) + 实盘ext池52只(2019-08~2026-11-19，6.99年)",
        冻结参数="reb=10 rolling delay=1 单边5bp min_names=8；A 全池等权满仓 / B 全池等权+VT15 / C 全池等权固定65% / D 动量k5逆波动满仓 / E 动量k5逆波动+VT15 / F 动量k5等权满仓 / G 动量k5等权+VT15",
        判据="AB1 卡玛(B)-卡玛(A)>=+0.05 且回撤浅>=5pp(两池)；AB2 卡玛(B)>=卡玛(C)+0.03(两池)；AB3 宽池 年化(D)-年化(A)<0 且 ext>0；AB4 逆波动相对等权卡玛差不低于-0.03(>=+0.03记正贡献)；AB5 只报",
        运行命令=r"venv\Scripts\python.exe runs\2026-11-20_ablation\ablate.py",
        结果="宽池：A 8.64%/-40.4%/0.214；B 8.04%/-29.3%/0.274；C 5.98%/-27.6%/0.217；D 6.31%/-49.8%/0.127；E 5.93%/-28.6%/0.207。ext：A 6.48%/-36.9%/0.176；B 4.15%/-37.9%/0.110；C 4.66%/-25.3%/0.184；D 17.65%/-31.8%/0.554；E 15.96%/-19.6%/0.813",
        判定="AB1 未通过(池依赖: 宽池+0.060✓/ext -0.066✗)；AB2 未通过(池依赖: 宽池+0.057✓/ext -0.075✗)；AB3 通过(宽-2.32pp/ext+11.18pp)；AB4 通过且两池两档全正(+0.06~+0.15，bootstrap 90%不含0)；AB5 池依赖(宽-0.067/ext+0.703)",
        备注="VT15 在等权基座上的有效性取决于样本里有没有熊市(宽池含2015/2018有效，ext池2019起无大熊无效)；逆波动是唯一两池两档都为正的层；HANDOFF 里『VT15 在等权基座失效 0.31→0.20』应限定为四层流水线整体",
        code_sha256=sha(os.path.join(ROOT, "runs", "2026-11-20_ablation", "ablate.py")),
        试验数="344",
    ),
]

with open(REG, "a", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["id", "登记时间", "假设", "宇宙", "冻结参数", "判据",
                                       "运行命令", "结果", "判定", "备注", "code_sha256", "试验数"])
    for r in rows:
        w.writerow(r)

with open(REG, encoding="utf-8-sig") as fh:
    lines = list(csv.reader(fh))
print("总记录数（含表头）:", len(lines))
print("末两行 id:", lines[-2][0], lines[-1][0])
print("列数一致:", len({len(x) for x in lines}) == 1, "列数:", len(lines[0]))
