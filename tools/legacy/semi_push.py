"""生成半导体三线快报并推到群里（group.json 里配置的群）。"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(r"E:\Code\quant")
BRIDGE = Path(r"E:\Code\dsh-agent\bridge.json")
GROUP = Path(r"E:\Code\dsh-agent\group.json")
RUN = ROOT / "runs" / "20261105_semi_sector_snapshot"

# 数据来自 work/semi_snapshot.py 的计算结果 + data/daily 快照
ROWS = [
    {
        "name": "半导体设备",
        "code": "sz159516",
        "close": 0.481,
        "d1": 0.481 / 0.440 - 1,
        "d2": 0.440 / 0.463 - 1,
        "r20": -0.261,
        "r60": -0.248,
        "dd60": -0.395,
        "dd250": -0.518,
        "pos250": 0.22,
        "ma60_gap": -0.263,
        "ma20_gap": -0.111,
        "amt": 0.95,
    },
    {
        "name": "半导体",
        "code": "sh512480",
        "close": 0.772,
        "d1": 0.772 / 0.707 - 1,
        "d2": 0.707 / 0.738 - 1,
        "r20": -0.192,
        "r60": -0.203,
        "dd60": -0.318,
        "dd250": -0.486,
        "pos250": 0.13,
        "ma60_gap": -0.193,
        "ma20_gap": -0.053,
        "amt": 0.98,
    },
    {
        "name": "科创芯片",
        "code": "sh588810",
        "close": 0.914,
        "d1": 0.914 / 0.815 - 1,
        "d2": 0.815 / 0.857 - 1,
        "r20": -0.212,
        "r60": -0.223,
        "dd60": -0.331,
        "dd250": -0.477,
        "pos250": 0.16,
        "ma60_gap": -0.207,
        "ma20_gap": -0.055,
        "amt": 0.89,
    },
]


def p(x: float) -> str:
    return f"{x * 100:+.1f}%"


def build_text(as_of: str = "11-04") -> str:
    lines = [f"[半导体三线｜截至{as_of}收盘]"]
    lines.append(
        "结论：深跌后的单日暴力反抽，不是趋势反转。三条线都还在 MA60 下方 19~26%，"
        "按 S2 口径一律不合格，不追。"
    )
    for r in ROWS:
        lines.append(
            f"{r['name']}（{r['code'][2:]}）{r['close']:.3f}｜{as_of} {p(r['d1'])}"
            f"（前一日 {p(r['d2'])}）｜20日 {p(r['r20'])}｜60日 {p(r['r60'])}｜"
            f"距60日高 {p(r['dd60'])}｜距MA60 {p(r['ma60_gap'])}｜"
            f"5/20日额比 {r['amt']:.2f}"
        )
    lines.append(
        "位置：250日分位 0.13~0.22（半导体最低 0.13），距250日高 -48~-52%，"
        "跌得够深但没到极端低点；量能没放大（额比 0.89~0.98），反抽是空头回补的样子。"
    )
    lines.append(
        "结构：设备最弱（20日 -26.1%、距60日高 -39.5%），科创芯片弹性最大（+12.1%），"
        "半导体居中。要转多，先看能不能站回 MA20（还差 5~11%），再看 MA60。"
    )
    lines.append("（尾巴敲敲桌子）观察为主，别接飞刀；真要动就等 MA20 收复后回踩不破。")
    return "\n".join(lines)


def send(text: str) -> str:
    cfg = json.loads(BRIDGE.read_text(encoding="utf-8-sig"))
    gid = str(json.loads(GROUP.read_text(encoding="utf-8-sig")).get("group", "")).strip()
    body = json.dumps(
        {"text": text, "group": gid, "forward": False, "name": "鲸鱼娘"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{cfg.get('port', 8721)}/notify",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Token": str(cfg.get("token", "")),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def main() -> int:
    text = build_text()
    (RUN / "push_text.txt").write_text(text, encoding="utf-8")
    print(text)
    print("\nchars:", len(text))
    try:
        print("send ->", send(text))
    except Exception as exc:  # noqa: BLE001
        print("send failed:", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
