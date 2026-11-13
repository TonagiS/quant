# -*- coding: utf-8 -*-
"""登记 POS-08 到 registry.csv，并补写 POS-06/07（原登记只落在 runs 目录）。"""
import csv
import hashlib
import io
import os

REG = r"E:\Code\quant\registry.csv"
FIELDS = ["id", "登记时间", "假设", "宇宙", "冻结参数", "判据", "运行命令", "结果", "判定", "备注", "code_sha256", "试验数"]


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]


rows = list(csv.DictReader(io.open(REG, encoding="utf-8-sig")))
have = {r["id"] for r in rows}

new = []
new.append(dict(
    id="POS-06",
    登记时间="2026-11-04 12:40",
    假设="VT15 上加 ADX(14)<20 时敞口减半，卡玛推到 1.0 以上且回撤压到 -20% 内",
    宇宙="ext（52 ETF，2019-08~2026-11，场内 5bp）",
    冻结参数="ADX14 / 阈值20 / 减半0.5 / VT15 / reb5",
    判据="①卡玛≥1.00 ②回撤≥-20% ③年化≥17% ④扰动(窗口10/14/20×阈18/20/22×减半0.4/0.5/0.6)最差卡玛≥0.95 ⑤三段≥同仓位对照 ⑥bootstrap Sharpe 区间不含0",
    运行命令="python work/pos06_prereg.py",
    结果="窗口20 一组塌到 0.86，④ 未过",
    判定="未通过",
    备注="事后补登记（原 PREREG/REPORT 在 runs/2026-11-04_POS-06_adx/）",
    code_sha256=sha(r"E:\Code\quant\work\pos06_prereg.py"),
    试验数="27",
))
new.append(dict(
    id="POS-07",
    登记时间="2026-11-04 13:46",
    假设="同 POS-06，把扰动窗口收窄到 12/14/16/18（看过上一轮结果后调整，登记风险）",
    宇宙="ext（52 ETF，2019-08~2026-11，场内 5bp）",
    冻结参数="ADX14 / 阈值20 / 减半0.5 / VT15 / reb5",
    判据="同 POS-06 六条",
    运行命令="python work/pos07_prereg.py",
    结果="主配置 19.65%/-16.17%/1.21，扰动 36 组最低 0.96，六条全过",
    判定="通过",
    备注="事后补登记；ADX 族参数范围两轮收窄，多重检验风险",
    code_sha256=sha(r"E:\Code\quant\work\pos07_prereg.py"),
    试验数="36",
))
new.append(dict(
    id="POS-08",
    登记时间="2026-11-04 15:20",
    假设="ADX 绝对水平随 regime 漂移（固定20 在滚动2年窗口落在 P16~P43），改用「ADX 处于过去 500 日后 30% 分位」作减半条件更稳",
    宇宙="ext（52 ETF，2019-08~2026-11，场内 5bp）",
    冻结参数="ADX14 / 分位窗口500(min_periods250) / q=30% / 减半0.5 / VT15 / reb5",
    判据="①卡玛≥1.00 ②回撤≥-20% ③年化≥17% ④扰动(q20~40%×窗口250/500/750/扩展×减半0.4/0.5/0.6)最差卡玛≥0.95 ⑤三段≥同仓位对照 ⑥bootstrap Sharpe 区间不含0 ⑦卡玛≥固定20−0.02",
    运行命令="python work/pos08_adx_pct.py",
    结果="主配置 19.26%/-17.18%/1.12；固定20 对照 19.65%/-16.17%/1.21；扰动 60 组最低 0.86（q40/250d）",
    判定="未通过（④ 0.86、⑦ 1.12<1.19）",
    备注="分位口径在震荡年更好（2021 卡玛 0.63 vs 固定20 0.34）、单边年更差（2026 1.77 vs 2.77）；分位想法来自 POS-07 后诊断，含事后成分",
    code_sha256=sha(r"E:\Code\quant\work\pos08_adx_pct.py"),
    试验数="61",
))

with io.open(REG, "a", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=FIELDS, quoting=csv.QUOTE_MINIMAL)
    for r in new:
        if r["id"] not in have:
            w.writerow(r)
            print("added", r["id"])
print("OK，当前行数:", len(list(csv.DictReader(io.open(REG, encoding="utf-8-sig")))))
