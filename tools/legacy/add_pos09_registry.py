import csv
import hashlib

p = r"E:\Code\quant\registry.csv"
rows = list(csv.reader(open(p, encoding="utf-8-sig")))
eng = hashlib.sha256(open(r"E:\Code\quant\position\engine.py", "rb").read()).hexdigest()[:16]
row = [
    "POS-09", "2026-11-04 18:40",
    "VT15+缓冲带k+2能把盈亏平衡成本推到≥30bp且不损收益",
    "ext;base（52/30 ETF，2019-08-13~2026-11-02）",
    "k5 buffer7 reb5 delay1 VT15 60d every5，对称成本，网格5/10/20/30/40/60bp",
    "C1 盈亏平衡≥30bp；C2 5bp CAGR≥VT15-1pp且卡玛≥VT15-0.05；C3 换手≤VT15×0.80；C4 delay2与剔除前3超额>0；两池同时",
    "python position/buffer_vt.py",
    "ext 全过（盈亏50.8bp，CAGR 18.37%，卡玛1.20，换手比0.77）；base C1 33.8bp、C2过、C4过，但C3换手比0.826>0.80",
    "未通过（base C3 17.4%<20%；buffer8 敏感性看起来会过，待下一轮预登记）",
    "buffer8/reb10/VT12/VT20 仅作敏感性；buffer8 base 0.791/换手18.68x",
    eng, "6",
]
if not any(r[0] == "POS-09" for r in rows[1:]):
    rows.append(row)
with open(p, "w", encoding="utf-8", newline="") as f:
    csv.writer(f).writerows(rows)
print("rows", len(rows) - 1, "eng", eng)
