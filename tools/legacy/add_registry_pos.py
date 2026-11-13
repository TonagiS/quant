import csv
import os

path = r"E:\Code\quant\registry.csv"
row = [
    "POS-01",
    "2026-11-03 19:10:44",
    "仓位层（总敞口/集中度/止损）能在不牺牲收益的前提下降低回撤，波动率目标是主候选",
    "base;ext（ETF，2019-08-13~2026-11-02）",
    "k5 reb5 cost0.0005 delay1；VT 8/12/15/20%（60日窗口）；cap 25/30%；stop -12%/MA60；DD 10/15/20%；H1~H6 详见 position/PREREG.md",
    "H1 卡玛≥同仓位+0.05、回撤改善≥5pp、Sharpe不差、≥2/3段；H2 回撤≥3pp且卡玛≥基线；H3 卡玛降≤0.03；H4 卡玛+0.03或回撤5pp；H5 卡玛≥max单规则-0.02且仓位≥60%；H6 保留80%改善且换手降20%",
    "python position/run_grid.py",
    "VT12/VT15 卡玛 0.90/0.99(ext)、0.69/0.72(base)，对同仓位固定 +0.33/+0.43、+0.23/+0.26，三段全正；DD 0.25/0.17；净值均线 0.49/0.34；现金闸门 0.37/0.38；止损 0.54/0.41 与 0.38/0.35；Kelly 0.08/0.11；cap30 0.52/0.45；组合 0.91/0.73（仓位 50%/47%）；事后 VT15+cap40 0.96/0.72",
    "H1 通过；H2/H4/H5/H6 未通过；H3 边界未通过（ext -0.04）",
    "同一批数据非样本外；测试段改善主要来自 2026 回撤；VT15+cap40 为事后补充未预登记；policy 提案见 position/POLICY_v1.md，未接线",
    "cae39184fbba862a",
    "24",
]

with open(path, "r", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.reader(f))
assert rows[0][0] == "id", rows[0]
if not any(r[0] == "POS-01" for r in rows[1:]):
    with open(path, "a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(row)
    print("appended", len(row), "columns")
else:
    print("POS-01 already present")
