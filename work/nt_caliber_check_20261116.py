"""核对口径：9 只汇金代理 ETF 近日份额变化（单只 vs 合计）。"""
import pandas as pd
from pathlib import Path

H = Path(r"E:\Code\quant\data\national_team\etf_shares_hist.csv")
df = pd.read_csv(H, parse_dates=["date"])
p = df.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4  # 亿份
name = dict(zip(df["code"], df["name"]))
last = p.index[-1]
print("最新份额日:", last.date(), "| 标的:", len(p.columns))

print("\n== 单只 ETF 近 5 个交易日份额变化（%）==")
rows = []
for c in p.columns:
    s = p[c].dropna()
    d1 = s.iloc[-1] / s.iloc[-2] - 1
    d2 = s.iloc[-1] / s.iloc[-3] - 1
    d3 = s.iloc[-1] / s.iloc[-4] - 1
    d4 = s.iloc[-1] / s.iloc[-5] - 1
    rows.append((name[c], c, s.iloc[-1], d1 * 100, d2 * 100, d3 * 100, d4 * 100))
for r in sorted(rows, key=lambda x: x[4]):
    print("%-22s %s %10.2f亿份  1日%+6.2f%%  2日%+6.2f%%  3日%+6.2f%%  4日%+6.2f%%" % r)

print("\n== 合计（9 只求和）==")
tot = p.sum(axis=1)
for n, lbl in [(1, "1日"), (2, "2日"), (3, "3日"), (4, "4日"), (14, "14日")]:
    print("%s 合计 %+.2f%%   （端点比值；%s → %s）" % (
        lbl, (tot.iloc[-1] / tot.iloc[-1 - n] - 1) * 100,
        tot.index[-1 - n].date(), tot.index[-1].date()))

# 逐日百分比相加（规则口径）
daily = p.pct_change(fill_method=None).mean(axis=1) * 100
print("逐日累加口径：3日 %+.2f%% / 4日 %+.2f%%" % (daily.iloc[-3:].sum(), daily.iloc[-4:].sum()))
print("\n最近 5 日逐日（等权平均，%）:")
for d, v in daily.iloc[-5:].items():
    print("  ", d.date(), "%+.2f%%" % v)
