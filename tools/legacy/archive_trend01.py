"""TREND-01 归档与登记更新。"""
import csv
import json
import shutil
from pathlib import Path

R = Path(r"E:\Code\quant")
RUN = R / "runs/2026-11-05_TREND-01"

res = ("base: V-A 卡玛0.412/dd-35.64/盈亏22bp/换手3043%/delay2 0.379/分段-0.439,+0.106；V-B 0.337/-41.27/20bp/2988%/0.355/-0.396,+0.018。"
       "ext: V-A 0.532/-30.57/24bp/3704%/0.438/-0.349,+0.058；V-B 0.418/-33.91/20bp/3691%/0.393/-0.536,+0.067。"
       "20日事件NW_t(顶/底)：base V-A -1.34/+1.55、V-B -0.11/+1.32；ext V-A -1.72/+1.42、V-B -0.47/+2.48，均|t|<3。"
       "S2：base 0.445、ext 0.557。")
verdict = "未通过（两变体 C1~C5 全未过；事件方向符合但 20 日 |NW_t|<3；替换 MA60 后卡玛均低于 S2）"

for rel in ("registry.csv", "indicators/trend/prereg/registry_trend.csv"):
    p = R / rel
    rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
    for r in rows:
        if r["id"] == "TREND-01":
            r["结果"] = res
            r["判定"] = verdict
    with open(p, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("registry updated", rel)

shutil.copy2(R / "indicators/trend/code/trend01_prereg.py", RUN / "trend01_prereg.py")
shutil.copy2(R / "registry.csv", RUN / "registry_row.csv")
print("archived", [p.name for p in RUN.iterdir()])

index = R / "INDEX.md"
s = index.read_text(encoding="utf-8")
row = ("| `runs/2026-11-05_TREND-01` | 预登记 | `python indicators/trend/code/trend01_prereg.py` | 2026-11-02 | "
       "V-A/V-B 事件方向符合但 20 日 NW_t<3（最高 2.48）；替换 MA60 后卡玛 0.412/0.532 < S2 0.445/0.557 → **未通过** | "
       "`trend01_summary.csv`, `trend01_events_*.csv` |\n")
anchor = "| `runs/2026-11-03_P2-TURN-01` |"
if "2026-11-05_TREND-01" not in s and anchor in s:
    s = s.replace(anchor, row + anchor, 1)
    index.write_text(s, encoding="utf-8")
    print("INDEX updated")

resmd = R / "results.md"
t = resmd.read_text(encoding="utf-8")
add = """
## 7.2 TREND-01 趋势转换信号（2026-11-05）

- V-A（收盘<MA60 且 MA60 下行=顶部 / 反之底部）、V-B（跌破前 40 日最低收盘=顶部 / 突破前 40 日最高=底部），前视测试 130/208 次截断重算全部一致。
- 事件研究（base/ext，20 日超额 NW_t，顶/底）：base V-A −1.34/+1.55、V-B −0.11/+1.32；ext V-A −1.72/+1.42、V-B −0.47/+2.48 —— **方向全部符合预期，但没有一个 |t|≥3**；ext V-B 底部 2.48 最接近。信号日距 60 日高/低已回吐约 11~16%（中位）。
- 组合替换（MA60 → 底开后允/顶后清）：卡玛 base 0.412/0.337、ext 0.532/0.418，均低于 S2 的 0.445/0.557；回撤 base −35.6/−41.3、ext −30.6/−33.9；盈亏平衡 20~24bp（<30bp）；换手 0.84~0.93× S2。
- 判据：C1~C5 全部未过（C2 卡玛差 −0.03~−0.14；C4 delay2 均低于 S2；C5 两段差一号负一号正）。
- 判定：**未通过**。证据等级：事件方向“证据不足”（方向对、强度不够）；组合层“无证据”。累计试验数 165（+2）。

## 8. 运行环境"""
t = t.replace("## 8. 运行环境", add, 1)
resmd.write_text(t, encoding="utf-8")
print("results.md updated")

rd = R / "indicators/trend/README.md"
u = rd.read_text(encoding="utf-8")
if "TREND-01 已完成" not in u:
    u += "\n\n## 已完成\n- TREND-01（2026-11-05）：V-A/V-B 事件方向符合但 20 日 |NW_t|<3；替换 MA60 后卡玛低于 S2 → **未通过**，产物 `runs/2026-11-05_TREND-01/`。\n"
    rd.write_text(u, encoding="utf-8")
print("trend README updated")
