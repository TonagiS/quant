"""对比两套 cum14 口径下的冻结减仓规则表现。

口径A（现行，规则冻结时用的）：14 个单日百分比直接相加（逐日共同代码）
口径B（端点比值）：(第 i 日份额合计 / 第 i-14 日份额合计 - 1) * 100

规则：cum14 <= -10% 触发，触发后第 5 个交易日起防守 20 个交易日。
"""
import sys
sys.path.insert(0, r"E:\Code\quant\tools")
import nt_signal_shadow as m
import numpy as np
import pandas as pd

N, M, L, K = 14, -10.0, 5, 20
OUT = r"E:\Code\quant\runs\2026-11-16_nt_ratio_cmp"

dates, cumA = m.load_shares()
dd = [pd.Timestamp(x) for x in dates]

h = pd.read_csv(r"E:\Code\quant\data\national_team\etf_shares_hist.csv", parse_dates=["date"])
p = h.pivot(index="date", columns="code", values="shares_wan").sort_index() / 1e4

cumB = []
for i, d in enumerate(dd):
    if i < N:
        cumB.append(np.nan)
        continue
    cur, prev = p.loc[d], p.loc[dd[i - N]]
    com = [c for c in cur.index if pd.notna(cur[c]) and pd.notna(prev[c])]
    cumB.append((cur[com].sum() / prev[com].sum() - 1) * 100)

df = pd.DataFrame({"date": dd, "A": cumA, "B": cumB}).dropna().reset_index(drop=True)


def rule(series):
    """返回 (signal bool 序列, defense bool 序列)"""
    sig = (series <= M).values
    state = np.zeros(len(series), dtype=bool)
    for i in range(len(series)):
        if sig[i]:
            state[i + L:min(len(series), i + L + K)] = True
    return pd.Series(sig, index=series.index), pd.Series(state, index=series.index)


sigA, defA = rule(df.A)
sigB, defB = rule(df.B)
df["sigA"], df["defA"], df["sigB"], df["defB"] = sigA, sigB, defA, defB
df["gap"] = df.B - df.A


def eps(mask):
    out = []
    prev = False
    for d, v in zip(df.date, mask):
        if v and not prev:
            out.append(d)
        prev = v
    return out


eA, eB = eps(sigA), eps(sigB)
onlyA = df[sigA & ~sigB]
onlyB = df[sigB & ~sigA]

px = pd.read_csv(r"E:\Code\quant\data\cache2019\sh510300.csv", encoding="utf-8-sig")
px["date"] = pd.to_datetime(px["date"])
px = px.set_index("date").sort_index()["close"].reindex(df.date).ffill()
ret = px.pct_change().fillna(0)


def perf(defense, start="2019-01-01", end="2099-12-31"):
    m_ = ((df.date >= start) & (df.date <= end)).values
    r = ret.values
    d = defense.values.astype(bool)
    pos = np.ones(len(d))
    pos[1:] = (~d[:-1]).astype(int)  # 昨天防守 → 今天空仓
    strat = np.prod(1 + r[m_] * pos[m_]) - 1
    P = px.values[m_]
    bh = P[-1] / P[0] - 1
    return bh * 100, strat * 100, int(d[m_].sum()), int(m_.sum())


def fwd(n=20):
    out = {}
    pos = {d: i for i, d in enumerate(px.index)}
    for name, mask in (("A", sigA), ("B", sigB)):
        rs = []
        for d in df.date[mask]:
            i = pos.get(d)
            if i is not None and i + n < len(px):
                rs.append(px.iloc[i + n] / px.iloc[i] - 1)
        s = pd.Series(rs)
        out[name] = (len(s), s.mean() * 100, s.median() * 100, (s > 0).mean() * 100 if len(s) else np.nan)
    return out


lines = []
lines.append("# cum14 两套口径 · 冻结减仓规则对比\n")
lines.append("数据 %s ~ %s，%d 个交易日；规则 n=14, m=-10%%, l=5, k=20\n"
             % (df.date.min().date(), df.date.max().date(), len(df)))
lines.append("| 口径 | 触发日数 | 触发段数 | 2026触发日 | 首触发 | 末触发 |")
lines.append("|---|---|---|---|---|---|")
for name, sig in (("A 单日相加（现行）", sigA), ("B 端点比值", sigB)):
    y26 = df[sig & (df.date.dt.year == 2026)]
    lines.append("| %s | %d | %d | %d | %s | %s |" % (
        name, sig.sum(), len(eA if name.startswith("A") else eB),
        y26.shape[0], df.date[sig].min().date(),
        df.date[sig].max().date() if sig.any() else "-"))

lines.append("\n## 510300 表现（全样本 2019 起）")
lines.append("| 口径 | 空仓天数 | 样本天数 | 买入持有 | 规则收益 |")
lines.append("|---|---|---|---|---|")
for name, defense in (("A 单日相加", defA), ("B 端点比值", defB)):
    bh, st, cov, tot = perf(defense)
    lines.append("| %s | %d | %d | %+.2f%% | %+.2f%% |" % (name, cov, tot, bh, st))

lines.append("\n## 2026 年至今")
lines.append("| 口径 | 空仓天数 | 样本天数 | 买入持有 | 规则收益 |")
lines.append("|---|---|---|---|---|")
for name, defense in (("A 单日相加", defA), ("B 端点比值", defB)):
    bh, st, cov, tot = perf(defense, start="2026-01-01")
    lines.append("| %s | %d | %d | %+.2f%% | %+.2f%% |" % (name, cov, tot, bh, st))

lines.append("\n## 逐年（A/B/持有）")
lines.append("| 年 | 样本日 | 持有 | A 规则 | A空仓 | B 规则 | B空仓 |")
lines.append("|---|---|---|---|---|---|---|")
for y in range(2019, 2027):
    s, e = "%d-01-01" % y, "%d-12-31" % y
    msk = ((df.date >= s) & (df.date <= e)).values
    if msk.sum() == 0:
        continue
    row = [str(y), str(int(msk.sum()))]
    P = px.values[msk]
    row.append("%+.1f%%" % ((P[-1] / P[0] - 1) * 100))
    for defense in (defA, defB):
        bh, st, cov, _ = perf(defense, start=s, end=e)
        row += ["%+.1f%%" % st, str(cov)]
    lines.append("| " + " | ".join(row) + " |")

fs = fwd(20)
lines.append("\n## 触发日后 20 个交易日 510300（全样本）")
lines.append("| 口径 | n | 均值 | 中位 | 胜率 |")
lines.append("|---|---|---|---|---|")
for k in ("A", "B"):
    n, mu, med, win = fs[k]
    lines.append("| %s | %d | %+.2f%% | %+.2f%% | %.0f%% |" % (k, n, mu, med, win))

lines.append("\n## 口径差异")
lines.append("- 最新：A=%+.2f%%，B=%+.2f%%，差 %+.2fpp" % (df.A.iloc[-1], df.B.iloc[-1], df.B.iloc[-1] - df.A.iloc[-1]))
lines.append("- 全样本 |B-A|：中位 %.2fpp，均值 %.2fpp，最大 %.2fpp" % (
    df.gap.abs().median(), df.gap.abs().mean(), df.gap.abs().max()))
lines.append("- 只 A 触发、B 不触发：%d 天" % len(onlyA))
lines.append("- 只 B 触发、A 不触发：%d 天" % len(onlyB))
lines.append("- 两者同时触发的日子占 A 触发的 %.1f%%" % ((sigA & sigB).sum() / max(1, sigA.sum()) * 100))

for tag, sub in (("只A", onlyA), ("只B", onlyB)):
    if len(sub):
        lines.append("\n### %s触发的日子（最多 15 条）" % tag)
        for _, r in sub.head(15).iterrows():
            lines.append("- %s  A=%+.2f%%  B=%+.2f%%" % (r.date.date(), r.A, r.B))

report = "\n".join(lines)
print(report)
import os
os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "report.md"), "w", encoding="utf-8") as fh:
    fh.write(report + "\n")
df.to_csv(os.path.join(OUT, "cum14_series.csv"), index=False, encoding="utf-8-sig")
print("\n[saved]", OUT)
