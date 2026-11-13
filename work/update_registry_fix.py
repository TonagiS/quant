import csv
from pathlib import Path

p = Path(r"E:\Code\quant\registry.csv")
with open(p, encoding="utf-8-sig", newline="") as f:
    rows = list(csv.reader(f))
header = rows[0]
idx = {k: i for i, k in enumerate(header)}
for r in rows[1:]:
    rid = r[idx["id"]]
    if rid == "POS-06":
        r[idx["结果"]] = ("修正 base_ret 后：VT15 16.50%/-16.51%/1.00；主配置 16.61%/-14.73%/1.13，"
                          "扰动最差 0.84，bootstrap [-0.006,+0.159]；③④⑥ 未过")
        r[idx["判定"]] = "未通过（修正 base_ret 后复核）"
        r[idx["备注"]] = (r[idx["备注"]] + "；2026-11-04 复核：base_ret 缺失修复后重跑，判据不变").strip("；")
    elif rid == "POS-07":
        r[idx["结果"]] = ("修正 base_ret 后：VT15 16.50%/-16.51%/1.00；主配置 16.61%/-14.73%/1.13，"
                          "扰动最差 0.93，bootstrap [-0.006,+0.159]；③ 16.61<17、④ 0.93<0.95、⑥ 含 0 → 未通过")
        r[idx["判定"]] = "未通过（原“通过”撤回：base_ret 缺失）"
        r[idx["备注"]] = (r[idx["备注"]] + "；2026-11-04 复核：base_ret 缺失修复后重跑，判据不变，改判未通过").strip("；")
    elif rid == "POS-08":
        r[idx["结果"]] = ("修正 base_ret 后：固定20 16.61%/-14.73%/1.13；分位30% 16.63%/-14.80%/1.12，"
                          "扰动最差 0.89，③④⑥ 未过")
        r[idx["判定"]] = "未通过（修正后复核）"
        r[idx["备注"]] = (r[idx["备注"]] + "；2026-11-04 复核：base_ret 缺失修复后重跑，判据不变").strip("；")
    elif rid in ("MACRO-01", "MACRO-02", "MACRO-03"):
        r[idx["备注"]] = (r[idx["备注"]] + "；VT15 基准当时未设 base_ret，数字待复核（结论方向不变）").strip("；")

if not any(r[0] == "POS-03" for r in rows[1:]):
    rows.append([
        "POS-03", "2026-11-04", "场外分档赎回费下的仓位层网格（探索性，非预登记）",
        "ext;base（52/30 ETF，2019-08-13~2026-11-02）",
        "reb 10/15/20/25/30/35；cap 100%/40%；费率为场外分档+5bp 佣金",
        "探索性：无预登记判据，按卡玛/成本排序；不计入预登记试验数",
        "python work/offex_grid.py",
        "修正 base_ret 后：ext 最佳 VT20 reb35 21.45%/-22.89%/0.94；base 最佳 VT15 reb35 13.51%/-19.40%/0.70；20/25 日档仍受赎回费拖累",
        "待复核（探索性；修正 base_ret 后已重跑）",
        "2026-11-04 复核：base_ret 缺失修复后重跑，旧表作废",
        "adfff3e2d239c6a0", "0",
    ])

with open(p, "w", encoding="utf-8", newline="") as f:
    csv.writer(f).writerows(rows)
print("updated", len(rows) - 1, "rows")
