"""用 Pillow 画 POS-01 净值对比图（ext/base 两panel）。"""
import pandas as pd
import numpy as np
from PIL import Image, ImageDraw, ImageFont

RUN = r"E:\Code\quant\runs\2026-11-03_POS-01"
OUT = RUN + r"\equity_compare.png"
FONT = r"C:\Windows\Fonts\msyh.ttc"
BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

W, H = 1280, 900
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
f_title = ImageFont.truetype(BOLD, 28, index=0)
f_sub = ImageFont.truetype(FONT, 18, index=0)
f_leg = ImageFont.truetype(FONT, 17, index=0)
f_tick = ImageFont.truetype(FONT, 14, index=0)

COLORS = {"S2": (120, 120, 120), "VT12": (31, 119, 180), "VT15": (44, 160, 44), "DD": (214, 39, 40)}
LABELS = {"S2": "S2 满仓", "VT12": "波动目标 12%", "VT15": "波动目标 15%", "DD": "回撤节流"}

d.text((30, 18), "POS-01 仓位管理：净值曲线（2019-08-13 ~ 2026-11-02，单边 5bp）", font=f_title, fill=(20, 20, 20))
d.text((30, 54), "same signal (S2), only position layer changed.  grey=S2 full, blue=VT12, green=VT15, red=drawdown throttle",
       font=f_tick, fill=(100, 100, 100))

panels = [("ext 52 ETFs", 120, 410), ("base 30 ETFs", 520, 810)]
left, right = 90, W - 60
for pool, top, bottom in panels:
    df = pd.read_csv(f"{RUN}\\equity_{pool.split()[0]}.csv", parse_dates=["date"])
    df = df.set_index("date")
    rets = {k: df[k].fillna(0.0).to_numpy() for k in COLORS}
    eq = {k: np.cumprod(1 + v) for k, v in rets.items()}
    n = len(df)
    # y 轴范围（log）
    vals = np.concatenate([eq[k] for k in COLORS])
    lo, hi = max(0.4, vals.min() * 0.92), vals.max() * 1.08
    y = lambda v: bottom - (np.log10(v) - np.log10(lo)) / (np.log10(hi) - np.log10(lo)) * (bottom - top)
    x = lambda i: left + i / max(n - 1, 1) * (right - left)

    # 网格
    for gv in [0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0]:
        if lo <= gv <= hi:
            yy = y(gv)
            d.line([(left, yy), (right, yy)], fill=(232, 232, 232), width=1)
            d.text((left - 48, yy - 8), f"{gv:g}", font=f_tick, fill=(120, 120, 120))
    # 年份刻度
    years = pd.Series(df.index.year, index=df.index)
    for yr in sorted(years.unique()):
        idx = int(np.argmax(years.to_numpy() == yr))
        xx = x(idx)
        d.line([(xx, bottom), (xx, bottom + 4)], fill=(80, 80, 80), width=1)
        d.text((xx - 16, bottom + 8), str(yr), font=f_tick, fill=(80, 80, 80))

    d.text((left, top - 30), pool, font=f_sub, fill=(20, 20, 20))
    # 曲线
    for k in ["S2", "VT12", "VT15", "DD"]:
        pts = [(x(i), y(v)) for i, v in enumerate(eq[k])]
        d.line(pts, fill=COLORS[k], width=3 if k != "S2" else 2, joint="curve")

    # 图例 + 指标（先铺白底，避免曲线穿字）
    ly = top + 6
    d.rectangle([right - 315, top - 4, right + 5, top + 104], fill="white")
    for k in ["S2", "VT12", "VT15", "DD"]:
        d.line([(right - 300, ly + 8), (right - 270, ly + 8)], fill=COLORS[k], width=3)
        r = rets[k]
        cagr = (1 + r).prod() ** (252 / len(r)) - 1
        e = np.cumprod(1 + r)
        mdd = (e / np.maximum.accumulate(e) - 1).min()
        calmar = cagr / abs(mdd) if mdd < 0 else np.nan
        d.text((right - 262, ly), f"{LABELS[k]}  {cagr*100:.1f}%  Calmar {calmar:.2f}",
               font=f_leg, fill=COLORS[k])
        ly += 24

d.text((left, H - 34), "Source: work/cache2019; baseline S2 reproduced exactly (18.84% / -33.58% / Calmar 0.56). Not out-of-sample.",
       font=f_tick, fill=(110, 110, 110))
img.save(OUT)
print("saved", OUT)
