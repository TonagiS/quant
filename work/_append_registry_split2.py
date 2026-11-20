# -*- coding: utf-8 -*-
"""登记 SPLIT-02（等权基准成分拆解 + 窗口/分红口径修正，试验数不变）。"""
import csv
import hashlib

REG = r"E:\Code\quant\registry.csv"
SCRIPTS = [r"E:\Code\quant\runs\2026-11-20_era_split\bench_split.py",
           r"E:\Code\quant\runs\2026-11-20_era_split\split.py"]

h = hashlib.sha256()
for p in SCRIPTS:
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
sha = h.hexdigest().upper()

row = dict(
    id="SPLIT-02",
    登记时间="2026-11-20 17:58",
    假设="(a) 宽池 2019+ 等权基准 9.48%/年 是否由黄金/QDII 或 2019 年后上市标的抬高；(b) 跨池比较是否存在窗口/分红口径不一致",
    宇宙="宽ETF池92只(cache_ohlc_fullhist→2026-11-13) + 指数代理15只(cache2007)，两池统一截到 2026-11-13",
    冻结参数="等权宇宙=features.valid & rm非空；日均等权、零成本毛收益；类别取自 runs/2026-11-14_layered_v1/universe_all.csv；指数池按中证编号手工归宽基/行业",
    判据="不设新判据（归因/口径复核）。预声明：只做精确加法分解 Σ类别贡献=组合日收益，不做择优、不筛类别",
    运行命令=r"venv\Scripts\python.exe runs\2026-11-20_era_split\split.py; venv\Scripts\python.exe runs\2026-11-20_era_split\bench_split.py",
    结果="基准 9.48% 拆为 行业3.86pp/宽基3.80pp/风格1.10pp/跨境0.99pp/商品0.67pp/债券0.08pp；剔商品+跨境→8.92%；92只全部2019-01前上市(新组0只)；ETF vs 指数 2019+ 年化差 +2.92/+3.17/+2.58/+0.71pp(均值+2.35pp)",
    判定="两个初版归因均被推翻：『新上市抬高』不成立(新上市组0只)、『黄金QDII拉升』不成立(合计仅0.56pp)。真因是分红口径差 2.35pp(宽池ETF含分红 vs 指数池价格指数)。窗口对齐后指数池 2019+ 差值为 +4.09pp/+0.079(原 +4.30/+0.086)",
    备注="新规则：跨池只能比池内差值(策略−同池等权)，不能比基准水平。明确不做『钉成交额前40』(与ext池重复/前视/多重比较)；影子盘继续，11-30 复盘。2019+ 属样本内/开发期",
    code_sha256=sha,
    试验数="344",
)

with open(REG, "a", encoding="utf-8", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=["id", "登记时间", "假设", "宇宙", "冻结参数", "判据",
                                       "运行命令", "结果", "判定", "备注", "code_sha256", "试验数"])
    w.writerow(row)

with open(REG, encoding="utf-8-sig") as fh:
    rows = list(csv.reader(fh))
print("总记录（含表头）:", len(rows), "末行:", rows[-1][0], "列数:", len(rows[-1]))
