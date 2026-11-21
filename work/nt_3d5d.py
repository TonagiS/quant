import pandas as pd

d = pd.read_csv(r"E:\Code\quant\data\national_team\etf_shares_hist.csv", parse_dates=["date"])
p = d.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4
names = d.drop_duplicates("code").set_index("code")["name"]
px = {"510050": 2.883, "510300": 4.070, "510310": 3.956, "510330": 4.240,
      "510500": 6.415, "512500": 3.633, "512100": 2.546, "588000": 1.314, "588080": 1.273}
px = {int(k): v for k, v in px.items()}
last, d3, d5 = p.index[-1], p.index[-4], p.index[-6]
rows = []
for c in p.columns:
    a, b3, b5 = p.loc[last, c], p.loc[d3, c], p.loc[d5, c]
    rows.append(dict(code=c, name=names[c], now=round(a, 1),
                     d3=round(a - b3, 2), d3pct=round((a / b3 - 1) * 100, 2),
                     d3amt=round((a - b3) * px[c], 1),
                     d5=round(a - b5, 2), d5pct=round((a / b5 - 1) * 100, 2),
                     d5amt=round((a - b5) * px[c], 1)))
r = pd.DataFrame(rows)
print(r.to_string(index=False))
print("TOTAL d3 %+.2f 亿份 / %+.0f 亿元 / %+.2f%%" % (r.d3.sum(), r.d3amt.sum(), (p.sum(axis=1).iloc[-1] / p.sum(axis=1).iloc[-4] - 1) * 100))
print("TOTAL d5 %+.2f 亿份 / %+.0f 亿元 / %+.2f%%" % (r.d5.sum(), r.d5amt.sum(), (p.sum(axis=1).iloc[-1] / p.sum(axis=1).iloc[-6] - 1) * 100))
tot = p.sum(axis=1)
print((tot.iloc[-6:] / 1e0).round(1).to_string())
print("daily total change:", (tot.diff() / 1e0).iloc[-4:].round(2).to_string())
