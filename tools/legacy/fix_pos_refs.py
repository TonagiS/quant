"""一次性统一 POS-03/06/07/08 口径：catalog、RULES_v2、MACRO REPORT、results.md。"""

import csv
import io
import json
import shutil
import time
from pathlib import Path

ROOT = Path(r"E:\Code\quant")
BAK = ROOT / "work" / "bak"
LOG = ROOT / "work" / "POS_REF_FIX_20261105.md"
stamp = time.strftime("%Y%m%d_%H%M%S")
BAK.mkdir(parents=True, exist_ok=True)
changes: list[str] = []

# ---------------------------------------------------------------- 1. catalog.csv
cat = ROOT / "indicators" / "catalog.csv"
shutil.copy2(cat, BAK / f"catalog.csv.bak_posfix_{stamp}.csv")
text = cat.read_text(encoding="utf-8")
rows = list(csv.DictReader(io.StringIO(text)))
fields = list(rows[0].keys())
for r in rows:
    if r["id"] == "adx14":
        old = f'{r["证据等级"]} / {r["依据"]}'
        r["证据等级"] = "D"
        r["依据"] = "POS-07 修正 base_ret 后未通过（卡玛 1.13，扰动最差 0.93，③④⑥ 不过）"
        changes.append(f"catalog.csv adx14：{old} → D / {r['依据']}")
    elif r["id"] == "adx_pct":
        old = r["依据"]
        r["证据等级"] = "D"
        r["依据"] = "POS-08 未通过（分位30% 卡玛 1.12，与固定20 的 1.13 基本无差；③④⑥ 不过）"
        changes.append(f"catalog.csv adx_pct：{old} → {r['依据']}")

buf = io.StringIO()
writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
writer.writeheader()
writer.writerows(rows)
cat.write_text(buf.getvalue(), encoding="utf-8")

# ------------------------------------------------------- 2. RULES_v2.md 口径注
rules = ROOT / "position" / "live" / "shadow" / "RULES_v2.md"
shutil.copy2(rules, BAK / f"RULES_v2.md.bak_posfix_{stamp}.md")
rt = rules.read_text(encoding="utf-8")
note = (
    "\n> **2026-11-05 口径核对（对照 registry.csv）**：上表是 2019-01 起的 `work/cost_tiers.txt` 快照；"
    "预登记口径（2019-08-13 起、场内 5bp、reb5）的 VT15 基准是 **ext 16.50% / 回撤 −16.51% / 卡玛 1.00**、"
    "**base 15.85% / −21.89% / 0.72**（POS-06/07/08/09 与 POS-09 基线同口径）。"
    "两套窗口不同源，不要混用；改 policy 时以预登记口径为准。要点 1 里的“卡玛 0.95”是上表窗口的数字。\n"
)
if "2026-11-05 口径核对" not in rt:
    anchor = "要点（2026-11-04 base_ret 修复后重跑）："
    rt = rt.replace(anchor, note.strip() + "\n\n" + anchor, 1)
    changed = True
else:
    changed = False
rules.write_text(rt, encoding="utf-8")
if changed:
    changes.append("RULES_v2.md：成本表后加“口径核对”注（VT15 预登记口径 16.50%/1.00、15.85%/0.72）")

# --------------------------------------------- 3. MACRO REPORT 的“POS-07 已通过”
macro = ROOT / "runs" / "2026-11-04_MACRO-01_failed" / "REPORT.md"
if macro.exists():
    shutil.copy2(macro, BAK / f"MACRO-01_REPORT.md.bak_posfix_{stamp}.md")
    mt = macro.read_text(encoding="utf-8")
    header = (
        "> **更正（2026-11-05）**：本文成文时 POS-07 记为通过。2026-11-04 晚 `base_ret` 缺失修复后重跑，"
        "POS-07 已**改判未通过**（卡玛 1.13、扰动最差 0.93、③④⑥ 不过），`vt15_adx` 账本已归档。"
        "下文出现的“POS-07 已通过预登记”“唯一通过预登记的增量是 ADX 减半”均以此更正为准。\n\n"
    )
    if "更正（2026-11-05）" not in mt:
        macro.write_text(header + mt, encoding="utf-8")
        changes.append("runs/2026-11-04_MACRO-01_failed/REPORT.md：顶部加更正块（POS-07 已撤回）")

# --------------------------------------------------------- 4. 变更记录本身
lines = ["# POS-03/06/07/08 引用一致性修正（2026-11-05）", "",
         "对照 `registry.csv` 最新判定（POS-06/07/08 均未通过、POS-03 探索性）。", ""]
lines += [f"- {c}" for c in changes]
lines += ["", "## 未改动（已一致）", "",
          "- `INDEX.md`：第 5 行已写明 POS-07 原“通过”撤回，第 33~35 行结论与 registry 一致。",
          "- `results.md` §1：POS-07/08 已标“无证据（未通过）”。",
          "- `RULES_v2.md` §七：`vt15_adx` 已注明“POS-07 修正后未通过，不再跟踪”。",
          "- `runs/2026-11-04_POS-06/07/08` 的 PREREG/REPORT：作为历史运行记录保留原文，结论以 registry 为准。",
          ""]
LOG.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(changes) if changes else "no changes")
print("log:", LOG)
