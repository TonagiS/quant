"""从 registry.csv 导出信号清单，追加到 results.md 的“信号清单”一节。"""

import csv
import shutil
import time
from pathlib import Path

ROOT = Path(r"E:\Code\quant")
REG = ROOT / "registry.csv"
RESULTS = ROOT / "results.md"
BAK = ROOT / "work" / "bak"

# 证据等级：按该条自身判据的完成度分档（与 indicators/README.md 的 A/B/C/D 口径一致）
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
    "POS-08": ("D", "卡玛 1.12 < 固定 20 的 1.21"),
    "POS-03": ("探索", "非预登记网格，不计等级"),
    "POS-09": ("C", "ext 全过；base C3 换手 17.4% < 20%"),
    "TREND-01": ("D", "两变体 C1~C5 全未过"),
}

POOL = {
    "P1-ROBUST-01": "base;ext",
    "P1-GATE-01": "base;ext;long;stocks;long+stocks",
}


def cut(text: str, n: int = 70) -> str:
    text = (text or "").replace("\n", " ").replace("|", "/").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def main() -> int:
    rows = list(csv.DictReader(REG.open(encoding="utf-8-sig")))
    lines = []
    lines.append("")
    lines.append("## 13. 信号清单（从 registry.csv 导出，2026-11-05）")
    lines.append("")
    lines.append(
        "来源 `registry.csv` 全部 {} 条；证据等级按该条自身判据完成度归档，"
        "口径同第 1 节（A 全过 / B 部分成立 / C 接近或仅单池 / D 未通过；探索性条目不评级）。".format(
            len(rows)
        )
    )
    lines.append("")
    lines.append("| 编号 | 内容 | 池 | 判据 | 结果 | 证据等级 |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        rid = r["id"].strip()
        lvl, why = LEVEL.get(rid, ("—", "未归档"))
        pool = POOL.get(rid) or cut(r.get("宇宙", ""), 34)
        lines.append(
            "| {id} | {content} | {pool} | {crit} | {res} | **{lvl}**（{why}） |".format(
                id=rid,
                content=cut(r.get("假设", ""), 60),
                pool=pool,
                crit=cut(r.get("判据", ""), 60),
                res=cut(r.get("结果", ""), 90),
                lvl=lvl,
                why=why,
            )
        )

    lines.append("")
    lines.append("### 13.1 布林带 / KDJ 是否在 45 条规则内")
    lines.append("")
    lines.append("- **都不在。** `indicators/rules_45_ext.csv` 的 45 条 = 15 条规则 × 3 个池（vol / px / 指数），"
                 "规则名单：R1 强动量+放量+站上20日线、R2 强动量+缩量、R4 弱动量+缩量、R4b 弱动量+更缩量、"
                 "R5 拥挤>85、R7 顶部结构形成、R8 顶部钝化/结构、R9 底部结构形成、R10 20日新高突破+站上60日线+放量、"
                 "R11 RSI6<20、R13 跌破20日线+放量，以及 R1/R2/R4/R10 的四个“无量”变体（_no_vol）。")
    lines.append("- **也不是 catalog 里的指标**：`indicators/catalog.csv` 现有 23 个条目（动量、MA60/MA120、逆波动、"
                 "量能比、拥挤度、RSI6、MACD 结构、现金闸门、波动率目标、回撤节流、净值均线、止损、Kelly、cap30、ADX、ADX 分位等），"
                 "没有布林带（BOLL）也没有 KDJ/随机指标。")
    lines.append("- **结论口径**：这两者属于**未测**，不是“已测且无效”。要下结论得先预登记（判据、成本、对照、试验数 +1/+2），"
                 "不能拿 45 条规则的结果去套。")
    lines.append("")
    lines.append("### 13.2 导出时发现的两处不一致")
    lines.append("")
    lines.append("1. `indicators/catalog.csv` 里 `adx14` 仍标 **B**（依据写的是“POS-07 通过，卡玛 1.21”），"
                 "但 `registry.csv` 中 POS-07 在 base_ret 修正后已**改判未通过**（卡玛 1.13、扰动最差 0.93）。"
                 "catalog 需要同步改成 D，否则会被当成可用的择时条件。")
    lines.append("2. `adx_pct` 在 catalog 标 D 与 POS-08 未通过一致，可保留；但它的“依据”文本仍引用旧数字，建议一并刷新。")
    lines.append("")

    text = RESULTS.read_text(encoding="utf-8")
    if "## 13. 信号清单" in text:
        print("已存在该节，先删旧节再写")
        head = text.split("## 13. 信号清单")[0].rstrip() + "\n"
        text = head
    BAK.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(RESULTS, BAK / f"results.md.bak_signal_list_{stamp}.md")
    RESULTS.write_text(text.rstrip() + "\n" + "\n".join(lines), encoding="utf-8")
    print("appended section, rows:", len(rows))
    print("backup:", BAK / f"results.md.bak_signal_list_{stamp}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
