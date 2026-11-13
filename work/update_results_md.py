"""results.md：§13 加中文等级/证据类型列，§6 重写试验数口径。"""

import csv
import re
from pathlib import Path

ROOT = Path(r"E:\Code\quant")
RESULTS = ROOT / "results.md"
REG = ROOT / "registry.csv"

LEVEL = {
    "P1-ROBUST-01": ("A", "两池 5 条判据全过"),
    "P1-GATE-01": ("B", "回撤 4/5 成立，卡玛 3/5 未过预登记"),
    "P2-COST-01": ("D", "两池盈亏平衡 25/29bp < 30bp，成本脆弱"),
    "P2-TURN-01": ("C", "无变体全过，V2 仅差 C3"),
    "POS-01": ("B", "H1（VT15）通过；H2/H4/H5/H6 未过"),
    "MACRO-01": ("C", "只压回撤不增收益"),
    "MACRO-02": ("D", "全部不优于基线"),
    "MACRO-03": ("D", "相关 -0.19 且分组非单调"),
    "POS-06": ("D", "修正 base_ret 后③④⑥ 未过"),
    "POS-07": ("D", "同 POS-06；原“通过”已撤回"),
    "POS-08": ("D", "卡玛 1.12 与固定20 的 1.13 基本无差，③④⑥ 未过"),
    "POS-03": ("探索", "非预登记网格，不计等级"),
    "POS-09": ("C", "ext 全过；base C3 换手 17.4% < 20%"),
    "TREND-01": ("D", "两变体 C1~C5 全未过"),
}
CN = {"A": "有证据", "B": "证据不足", "C": "证据不足", "D": "无证据", "探索": "不适用"}
TYPE = {
    "P1-ROBUST-01": "参数稳健（同批数据，非样本外）",
    "P1-GATE-01": "参数稳健（同批数据，非样本外）",
    "P2-COST-01": "参数稳健（同批数据，非样本外）",
    "P2-TURN-01": "参数稳健（同批数据，非样本外）",
    "POS-01": "参数稳健（同批数据，非样本外）",
    "MACRO-01": "同批数据（非样本外）",
    "MACRO-02": "同批数据（非样本外）",
    "MACRO-03": "同批数据（非样本外）",
    "POS-06": "预登记重跑（同批数据，非样本外）",
    "POS-07": "预登记重跑（同批数据，非样本外）",
    "POS-08": "预登记重跑（同批数据，非样本外）",
    "POS-03": "探索性（未预登记）",
    "POS-09": "预登记（同批数据，非样本外）",
    "TREND-01": "预登记（同批数据，非样本外）",
}


def cut(text: str, n: int) -> str:
    text = (text or "").replace("\n", " ").replace("|", "/").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def build_section() -> str:
    rows = list(csv.DictReader(REG.open(encoding="utf-8-sig")))
    out = [
        "## 13. 信号清单（从 registry.csv 导出，2026-11-05）",
        "",
        "来源 `registry.csv` 全部 {} 条；证据等级（A~D）按该条自身判据完成度归档，"
        "中文等级与第 1 节口径一致（有证据 / 证据不足 / 无证据）。".format(len(rows)),
        "",
        "| 编号 | 内容 | 池 | 判据 | 结果 | 等级 | 中文等级 | 证据类型 | 依据 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        rid = r["id"].strip()
        lvl, why = LEVEL.get(rid, ("—", "未归档"))
        out.append(
            "| {id} | {content} | {pool} | {crit} | {res} | **{lvl}** | {cn} | {typ} | {why} |".format(
                id=rid,
                content=cut(r.get("假设", ""), 50),
                pool=cut(r.get("宇宙", ""), 30),
                crit=cut(r.get("判据", ""), 55),
                res=cut(r.get("结果", ""), 80),
                lvl=lvl,
                cn=CN.get(lvl, "—"),
                typ=TYPE.get(rid, "—"),
                why=why,
            )
        )
    out += [
        "",
        "### 13.1 布林带 / KDJ 是否在 45 条规则内",
        "",
        "- **都不在。** `indicators/rules_45_ext.csv` 的 45 条 = 15 条规则 × 3 个池（vol / px / 指数），"
        "规则名单：R1 强动量+放量+站上20日线、R2 强动量+缩量、R4 弱动量+缩量、R4b 弱动量+更缩量、"
        "R5 拥挤>85、R7 顶部结构形成、R8 顶部钝化/结构、R9 底部结构形成、R10 20日新高突破+站上60日线+放量、"
        "R11 RSI6<20、R13 跌破20日线+放量，以及 R1/R2/R4/R10 的四个“无量”变体。",
        "- **也不在 catalog 里**：`indicators/catalog.csv` 现有 23 个条目，没有布林带（BOLL）也没有 KDJ/随机指标。",
        "- **结论口径**：这两者属于**未测**，不是“已测且无效”；要下结论得先预登记（试验数 +1/+2）。",
        "",
        "### 13.2 引用一致性（2026-11-05 修正）",
        "",
        "- `catalog.csv`：`adx14` 由 **B 改 D**，依据改为“POS-07 修正 base_ret 后未通过（卡玛 1.13、扰动最差 0.93）”；"
        "`adx_pct` 依据同步刷新（分位 1.12 与固定 20 的 1.13 基本无差）。",
        "- `RULES_v2.md`：成本表后加“口径核对”注——该表是 2019-01 起快照，预登记口径（2019-08 起）的 VT15 基准是 "
        "ext 16.50%/−16.51%/1.00、base 15.85%/−21.89%/0.72，两套窗口不同源、不可混用。",
        "- `runs/2026-11-04_MACRO-01_failed/REPORT.md`：顶部加更正块，说明“POS-07 已通过”系旧口径。",
        "- 变更明细细表见 `work/POS_REF_FIX_20261105.md`。",
        "",
    ]
    return "\n".join(out)


def build_trials() -> str:
    rows = list(csv.DictReader(REG.open(encoding="utf-8-sig")))
    total = 0
    lines = [
        "## 6. 累计试验数",
        "",
        "**现行口径（2026-11-05 固定）：累计预登记试验 = 165 次**，等于 `registry.csv` 各行"
        "“试验数”列之和。",
        "",
        "| 条目 | 试验数 |",
        "|---|---|",
    ]
    for r in rows:
        try:
            n = int(float(r.get("试验数") or 0))
        except ValueError:
            n = 0
        total += n
        lines.append(f"| {r['id'].strip()} | {n} |")
    lines += [
        f"| **合计** | **{total}** |",
        "",
        "- TREND-01 的 2 次已包含在内；POS-03 是探索性网格，registry 记 0，不计入。",
        "- **R10 的 1 次预登记在 registry 建表之前**，只记在本文件第 2 节，不在 165 里；"
        "若把 R10 也算进历史总数，则为 166。",
        "- 11-04 之前行文里的“164 次”= 163（registry，当时尚无 TREND-01）+ R10 的 1 次，属旧口径，**已废弃**。",
        "- 后续 Bonferroni / SPA 一律按 **165** 计；新增条目时先改这张表，再动上限。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    text = RESULTS.read_text(encoding="utf-8")

    # §13 整节替换
    if "## 13. 信号清单" in text:
        head, _, tail = text.partition("## 13. 信号清单")
        text = head.rstrip() + "\n\n" + build_section()
    else:
        text = text.rstrip() + "\n\n" + build_section()

    # §6 整节替换
    m = re.search(r"## 6\. 累计试验数.*?(?=\n## 7\.)", text, re.S)
    if m:
        text = text[: m.start()] + build_trials() + text[m.end() :]
    else:
        print("warning: §6 not found")

    RESULTS.write_text(text, encoding="utf-8")
    print("results.md updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
