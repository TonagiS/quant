"""POS-02 净值对比图（Pillow，无需 matplotlib）。"""
import pandas as pd
import numpy as np
from PIL import Image, ImageDraw, ImageFont

RUN = r"E:\Code\quant\runs\2026-11-03_POS-02"
OUT = RUN + r"\equity_compare.png"
FONT = r"C:\Windows\Fonts\msyh.ttc"
BOLD = r"C:\Windows\Fonts\msyhbd.ttc"

W, H = 1280, 900
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
f_title = ImageFont.truetype(BOLD, 26, index=0)
f_sub = ImageFont.truetype(FONT, 16, index=0)
f_leg = ImageFont.truetype(FONT, 15, index=0)
f_tick = ImageFont.truetype(FONT, 13, index=0)

COLS = {"S2": (120, 120, 120), "VT15": (31, 119, 180), "ERC_VT15": (44, 160, 44),
        "ERC_VT15_CAP40": (0, 150, 150), "CPPI3_75P": (214, 39, 40)}
LABELS = {"S2": "S2 满仓", "VT15": "VT15", "ERC_VT15": "ERC+VT15",
          "ERC_VT15_CAP40": "ERC+VT15+cap40", "CPPI3_75P": "CPPI 峰值75% M=3"}

d.text((28, 16), "POS-02 仓位方法对比：净值曲线（2019-08-13 ~ 2026-11-02，5bp）", font=f_title, fill=(20, 20, 20))
d.text((28, 50), "same S2 signal, only position layer changed.  grey=S2, blue=VT15, green=ERC+VT15, teal=+cap40, red=CPPI",
       font=f_sub, fill=(100, 100, 100))

panels = [("ext", "ext 52 ETFs", 110, 400), ("base", "base 30 ETFs", 500, 790)]
left, right = 90, W - 55
for pool, title, top, bottom in panels:
    df = pd.read_csv(RUN + "\\equity.csv")
    date_col = f"{pool}_date"
    dates = pd.to_datetime(df[date_col])
    n = len(df)
    y = None
    eqs = {}
    for k in COLS:
        r = df[f"{pool}_{k}"].fillna(0.0).to_numpy()
        eqs[k] = np.cumprod(1 + r)
    vals = np.concatenate([eqs[k] for k in COLS])
    lo, hi = max(0.5, vals.min() * 0.92), vals.max() * 1.10
    y = lambda v: bottom - (np.log10(v) - np.log10(lo)) / (np.log10(hi) - np.log10(lo)) * (bottom - top)
    x = lambda i: left + i / max(n - 1, 1) * (right - left)

    for gv in (0.5, 0.75, 1, 1.5, 2, 3, 4, 5):
        if lo <= gv <= hi:
            yy = y(gv)
            d.line([(left, yy), (right, yy)], fill=(232, 232, 232), width=1)
            d.text((left - 46, yy - 7), f"{gv:g}", font=f_tick, fill=(120, 120, 120))
    years = dates.dt.year.to_numpy()
    for yr in sorted(set(years)):
        idx = int(np.argmax(years == yr))
        xx = x(idx)
        d.line([(xx, bottom), (xx, bottom + 4)], fill=(80, 80, 80), width=1)
        d.text((xx - 15, bottom + 7), str(yr), font=f_tick, fill=(80, 80, 80))

    d.text((left, top - 26), title, font=f_sub, fill=(20, 20, 20))
    for k in COLS:
        pts = [(x(i), y(v)) for i, v in enumerate(eqs[k])]
        d.line(pts, fill=COLS[k], width=3 if k != "S2" else 2, joint="curve")

    # 图例 + 指标
    ly = top + 2
    d.rectangle([right - 330, top - 8, right + 5, top + 96], fill="white")
    for k in COLS:
        d.line([(right - 322, ly + 8), (right - 292, ly + 8)], fill=COLS[k], width=3)
        r = df[f"{pool}_{k}"].fillna(0.0).to_numpy()
        cagr = (1 + r).prod() ** (252 / len(r)) - 1
        eq = np.cumprod(1 + r)
        mdd = (eq / np.maximum.accumulate(eq) - 1).min()
        calmar = cagr / abs(mdd) if mdd < 0 else np.nan
        d.text((right - 284, ly), f"{LABELS[k]}  {cagr*100:.1f}%  Calmar {calmar:.2f}",
               font=f_leg, fill=COLS[k])
        ly += 19

d.text((28, H - 28), "Source: data/cache2019; not out-of-sample. CPPI 峰值 floor 版本牺牲收益换回撤，不适合本策略。",
       font=f_tick, fill=(110, 110, 110))
img.save(OUT)
print("saved", OUT)
