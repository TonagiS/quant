# -*- coding: utf-8 -*-
"""登记 SPLIT-01（口径重述，试验数不变）。"""
import csv
import hashlib
import os

REG = r"E:\Code\quant\registry.csv"
SCRIPT = r"E:\Code\quant\runs\2026-11-20_era_split\split.py"

h = hashlib.sha256()
with open(SCRIPT, "rb") as fh:
    for b in iter(lambda: fh.read(1 << 20), b""):
        h.update(b)
sha = h.hexdigest().upper()

row = dict(
    id="SPLIT-01",
    登记时间="2026-11-20 17:26",
    假设="把 2013 起/2017 起两条曲线切成 2013-2018 与 2019+ 两段后，『策略打不过等权』是否只存在于 2019+ 段；用无选池偏差的指数代理池能否复核",
    宇宙="宽ETF池92只(cache_ohlc_fullhist，2013起/2017起) + 指数代理15只(cache2007 idx_*，2013起/2017起)",
    冻结参数="同实盘口径：k=5 reb=10 rolling invvol delay=1 单边5bp min_names=8；VT15=min(1,15%/vol60)；等权=同池全池等权同5bp同调仓；切点 2019-01-01",
    判据="不设新判据（口径重述）。预声明只报段内年化/最大回撤/卡玛/夏普/均仓，不做择优、不挑段",
    运行命令=r"venv\Scripts\python.exe runs\2026-11-20_era_split\split.py",
    结果="宽池 S2−等权 年化：2013–2018 +1.70pp（卡玛 −0.000）、2019+ −5.36pp（卡玛 −0.230）；指数池：2013–2018 +0.10pp、2019+ +4.30pp（卡玛 +0.086）。VT15−等权 卡玛：宽池 2013–2018 +0.013 / 2019+ −0.125；指数池 +0.119 / +0.068。宽池 2019+ 等权 9.59%/年 vs 指数池 6.37%",
    判定="无新判据，重述。结论：『打不过等权』只在「宽池+2019+」成立，不能推广；指数池（无选池偏差）2019+ 段策略反而赢 +4.30pp。连带更正 ROLL-01 的『系统性』表述为该段驱动，并更正 multiwindow 报告结论 2",
    备注="2019+ 与 2013–2018 均标『样本内/开发期』，非样本外。引擎起点只切统计窗口、不改持仓路径（调仓相位自引擎起点固定），故两起点 2019+ 数字必然相同",
    code_sha256=sha,
    试验数="344",
)

with open(REG, "a", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["id", "登记时间", "假设", "宇宙", "冻结参数", "判据",
                                       "运行命令", "结果", "判定", "备注", "code_sha256", "试验数"])
    w.writerow(row)

with open(REG, encoding="utf-8-sig") as fh:
    rows = list(csv.reader(fh))
print("总记录（含表头）:", len(rows), "末行:", rows[-1][0], "试验数:", rows[-1][-1])
print("列数一致:", len({len(r) for r in rows}) == 1, len(rows[0]))
