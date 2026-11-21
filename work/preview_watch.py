# -*- coding: utf-8 -*-
"""预览：第二段关注板块 + 复原/失守门槛（仅私聊预览，不动群播报）"""
import sys
from pathlib import Path

sys.path.insert(0, r"E:\Code\quant\tools")
import single_asset_daily as s  # noqa: E402

DS = "2026-11-13"
close = s.load_codes([c for _, c in s.WATCH], (s.DATA, s.DATA_FULL)).loc[:DS]
P = s.parts(close)
i = len(close) - 1

lines = ["【关注板块 · 单支仓位 + 门槛】(11-13 预览)"]
for label, code in s.WATCH:
    pos = float(P["pos"][code].iloc[i])
    T = int(P["T"][code].iloc[i])
    px = float(close[code].iloc[i])
    base = float(close[code].iloc[i - 20])          # 20 个交易日前收盘
    if pos == 0:
        lvl = base * 1.01                           # 回到 20 日动量 +1% 才复原
        tag = f"空仓 · 复原 {lvl / px - 1:+.1%}（价 {lvl:.3f}）"
    else:
        lvl = base                                  # 动量掉回 0 以下就减仓
        ma = close[code].rolling(200).mean()
        bull = px > float(ma.iloc[i]) and float(ma.iloc[i]) > float(ma.iloc[i - 60])
        g_new = 0.5 if bull else 0.0
        vc = float(P["volcap"][code].iloc[i])
        tp = float(P["taper"][code].iloc[i])
        nxt = g_new * vc * tp
        tag = f"{pos:.2f} T{T} · 失守 {lvl / px - 1:+.1%}（价 {lvl:.3f}→{nxt:.2f}）"
    lines.append(f"  {label}({code[2:]}) {tag} · 现价 {px:.3f}")

out = Path(r"E:\Code\quant\work\preview_watch.txt")
out.write_text("\n".join(lines), encoding="utf-8")
