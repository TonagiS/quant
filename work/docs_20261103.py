"""更新 results.md / INDEX.md / shadow/README.md / registry 备注（2026-11-03 变更）。"""
import csv
import os

R = r"E:\Code\quant"


def edit(path, pairs):
    s = open(path, encoding="utf-8").read()
    for old, new in pairs:
        assert old in s, f"{path} 未找到: {old[:60]}"
        s = s.replace(old, new, 1)
    open(path, "w", encoding="utf-8").write(s)
    print("updated", path)


edit(os.path.join(R, "results.md"), [
    ("| S2 的成本安全垫只有 25bp / 29bp 单边（判据要求 ≥30bp） | **无证据**（成本脆弱） | 参数稳健 | P2-COST-01 |",
     "| S2 的成本安全垫只有 25bp / 29bp 单边（判据要求 ≥30bp） | **无证据**（成本脆弱） | 参数稳健 | P2-COST-01（**已结项，不开 P3**） |"),
    ("| 降低换手能否救回成本边界（V1 加倍周期 / V2 缓冲带 / V3 两者） | **无证据**（无变体同时过 C1~C4） | 参数稳健 | P2-TURN-01 |",
     "| 降低换手能否救回成本边界（V1 加倍周期 / V2 缓冲带 / V3 两者） | **无证据**（无变体同时过 C1~C4） | 参数稳健 | P2-TURN-01（已结项） |"),
    ("- **成本改造**：V1/V2/V3 已测、全部未通过；V2 只差 C3（换手降幅）。若要继续，只能新编号（例如缓冲带 + 更长持有期、或按成交额限制换手），不得回改本轮判据。",
     "- **成本问题结项（2026-11-03）**：维持“成本脆弱”，不开 P3；V2 缓冲带仅作为影子观察臂（只记换手/滑点，不比收益）。"),
    ("## 7. 运行环境",
     """## 7. 2026-11-03 变更

- `decision_1430.py`：备份 `bak/decision_1430_20261103.py`（sha 036f7933…）后修改，新 sha 065002b9…；
  移除拥挤度>85、顶部钝化/结构、量能确认；保留动量排序、60 日线、逆波动（注释已写“仅 ETF 主题池有效、成本脆弱”）。
- `shadow/`：v1 规则未动；新增观察臂 `v2_observ.py`（缓冲带 k+2，只记换手/滑点/价差，不比收益）、`daily.ps1` 包装器。
- **计划任务未注册**：14:30/15:10 两个任务的提权请求被拒绝（沙箱策略），命令已写好，待授权执行。

## 8. 运行环境"""),
])

edit(os.path.join(R, "INDEX.md"), [
    ("| `shadow/` | 影子运行 | `shadow/run_shadow.py --phase 1430\\|eod` | 逐日 | 规则 v1 已冻结；60 日回放一致（目标集合 0 天不一致、最大权重差 0）；每日两轮待启动 | `shadow_log.csv`, `replay_report.json`, `FROZEN.json` |",
     "| `shadow/` | 影子运行 | `shadow/daily.ps1 -phase 1430\\|eod` | 逐日 | v1 冻结 + V2 观察臂（只记换手/滑点）；60 日回放一致；**计划任务未注册**（提权被拒），待授权 | `shadow_log.csv`, `v2_observ.csv`, `replay_report.json` |\n| `decision_1430.py` | 主脚本 | — | — | 2026-11-03 改版：删拥挤度/顶部结构/量能确认，保留动量/60日线/逆波动；备份在 `bak/decision_1430_20261103.py` | sha 065002b9… |"),
])

edit(os.path.join(R, "shadow", "README.md"), [
    ("**尚未安装每日计划任务**——需要你确认后我再注册（见文末）。",
     "**计划任务未注册**：14:30 / 15:10 两个任务的提权请求被沙箱策略拒绝（2026-11-03），命令见文末，待授权。"),
    ("# shadow/ 使用说明",
     "# shadow/ 使用说明\n\n**V2 观察臂**：`v2_observ.py`（缓冲带 k+2）只在 15:10 由 `daily.ps1` 调用，记录换手、滑点、价差到 `v2_observ.csv`；不参与 v1、不比收益、不改规则。"),
    ("```powershell\nschtasks /Create /TN \"quant_shadow_1430\" /TR \"powershell -NoProfile -Command \\\"cd E:\\Code\\quant; .\\venv\\Scripts\\python.exe shadow\\run_shadow.py --phase 1430\\\"\" /SC WEEKDAYS /ST 14:30\nschtasks /Create /TN \"quant_shadow_eod\"  /TR \"powershell -NoProfile -Command \\\"cd E:\\Code\\quant; .\\venv\\Scripts\\python.exe shadow\\run_shadow.py --phase eod\\\"\"  /SC WEEKDAYS /ST 15:10\n```\n我没有擅自注册：这属于系统级配置，按纪律等你点头。",
     "```powershell\n$t1 = 'powershell -NoProfile -ExecutionPolicy Bypass -File \"E:\\Code\\quant\\shadow\\daily.ps1\" -phase 1430'\n$t2 = 'powershell -NoProfile -ExecutionPolicy Bypass -File \"E:\\Code\\quant\\shadow\\daily.ps1\" -phase eod'\nschtasks /Create /TN \"quant_shadow_1430\" /TR $t1 /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 14:30 /F\nschtasks /Create /TN \"quant_shadow_eod\"  /TR $t2 /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:10 /F\n```\n需要管理员权限或在本会话批准 danger-full-access 才能写入系统任务目录。"),
])

p = os.path.join(R, "registry.csv")
rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
for r in rows:
    if r["id"] == "P2-COST-01":
        r["备注"] = r["备注"] + "；2026-11-03 结项：维持成本脆弱，不开 P3"
    if r["id"] == "P2-TURN-01":
        r["备注"] = r["备注"] + "；2026-11-03 结项：V2 仅作影子观察臂"
with open(p, "w", newline="", encoding="utf-8-sig") as fp:
    w = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
print("registry notes updated")
