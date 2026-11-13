"""一次性补丁：decision_1430.py 移除无证据信号，保留动量/60日线/逆波动。先备份再改。"""
import hashlib
import os
import shutil

ROOT = r"E:\Code\quant"
SRC = os.path.join(ROOT, "decision_1430.py")
BAK = os.path.join(ROOT, "bak", "decision_1430_20261103.py")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def rep(s, old, new, tag):
    assert old in s, f"未找到片段: {tag}"
    assert s.count(old) == 1, f"片段不唯一: {tag}"
    return s.replace(old, new)


shutil.copy2(SRC, BAK)
print("backup ->", BAK, sha(BAK)[:16])
s = open(SRC, encoding="utf-8").read()

s = rep(s, '''"""
14:30 尾盘决策脚本（MACD 钝化/结构 + 拥挤度 + 动量）

依赖：akshare pandas numpy requests；同目录需要 macd_structure.py 和 portfolio.json
环境变量：DEEPSEEK_API_KEY（必填，不要写进代码）、DEEPSEEK_MODEL（可选，默认 deepseek-chat）
"""''', '''"""
14:30 尾盘决策脚本（动量排序 + 60 日线 + 逆波动加权）

已验证（仅 ETF 主题池、2019-2026、同一批数据，不是样本外）：
  * 动量排序（20/60/120 日截面排名）与 60 日线过滤具备参数稳健性（P1-ROBUST-01）；
  * 逆波动加权只在 ext 池有增益（base 无、指数池无）；
  * 成本脆弱：盈亏平衡 25bp（base）/ 29bp（ext）单边，换手高，实盘成本会显著吃掉超额（P2-COST-01/P2-TURN-01）。
已移除（均无证据）：拥挤度>85、顶部钝化/结构、量能确认。
保留但未验证：底部钝化/结构，仅作观察项。

依赖：akshare pandas numpy requests；同目录需要 macd_structure.py 和 portfolio.json
环境变量：DEEPSEEK_API_KEY（必填，不要写进代码）、DEEPSEEK_MODEL（可选，默认 deepseek-chat）
"""''', "docstring")

s = rep(s, 'INTRADAY_FRACTION = 0.8     # 14:30 时已成交量占全天的粗略比例\n', "", "intraday constant")

old_crowd = s[s.index("# ==========================================\n# 拥挤度"):
              s.index("# ==========================================\n# 主流程")]
s = s.replace(old_crowd, "", 1)
assert "def crowding_pct" not in s

s = rep(s, "    results, skipped, unit_votes = [], [], {True: 0, False: 0}",
        "    results, skipped = [], []", "counters")

s = rep(s, '''            dif, _ = calc_macd(close_t)
            tags = []
            for side in ("bottom", "top"):
                state, age = analyze(close_t, dif, side, **DEFAULTS)
                if state not in ("无", "数据不足"):
                    tags.append(label(state, age, side))
            crowd, as_yuan = crowding_pct(amount, rt)
            if as_yuan is not None:
                unit_votes[as_yuan] += 1''', '''            dif, _ = calc_macd(close_t)
            tags = []
            # 只保留底部信号；顶部钝化/结构已移除（无证据）
            state, age = analyze(close_t, dif, "bottom", **DEFAULTS)
            if state not in ("无", "数据不足"):
                tags.append(label(state, age, "bottom"))
            vol20 = float(pd.Series(close_t).pct_change().tail(20).std() * 100)''', "macd block")

s = rep(s, '''                "拥挤度(%)": round(crowd, 0) if crowd == crowd else np.nan,
                "MACD结构": " / ".join(tags) if tags else "无",
                ">20日线": "✅" if price > close.tail(20).mean() else "❌",''',
        '''                "20日波动(%)": round(vol20, 2),
                "底部结构": " / ".join(tags) if tags else "无",
                ">60日线": "✅" if price > close.tail(60).mean() else "❌",''', "row fields")

s = rep(s, '''    if unit_votes[True] + unit_votes[False]:
        warn = "（两边都有，说明判定不稳，拥挤度请谨慎看待）" if min(unit_votes.values()) > 0 else ""
        print(f"历史成交额列单位判定: 元 {unit_votes[True]} 只 / 手 {unit_votes[False]} 只{warn}")
''', "", "unit votes print")

s = rep(s, '''    print(f"{'14:30 尾盘决策（MACD结构含临时价，属预判）':^80}")
    print("=" * 100)
    print(f"{'板块':<12}{'今日':>8}{'1月':>9}{'3月':>9}{'拥挤度':>8}  {'MACD结构':<22}{'>20日线'}")
    print("-" * 100)
    for r in results:
        print(f"{r['名称']:<12}{r['今日涨跌(%)']:>7}%{r['1月动量(%)']:>8}%{r['3月动量(%)']:>8}%"
              f"{r['拥挤度(%)']:>7}%  {r['MACD结构']:<22}{r['>20日线']}")''',
        '''    print(f"{'14:30 尾盘决策（动量排序 + 60日线；顶部/拥挤度/量能已移除）':^80}")
    print("=" * 100)
    print(f"{'板块':<12}{'今日':>8}{'1月':>9}{'3月':>9}{'20日波动':>9}  {'底部结构':<22}{'>60日线'}")
    print("-" * 100)
    for r in results:
        print(f"{r['名称']:<12}{r['今日涨跌(%)']:>7}%{r['1月动量(%)']:>8}%{r['3月动量(%)']:>8}%"
              f"{r['20日波动(%)']:>8}%  {r['底部结构']:<22}{r['>60日线']}")''', "dashboard")

start = s.index("def build_prompt(")
end = s.index("def call_llm(")
s = s[:start] + '''def build_prompt(holdings_str, assets, cash, pos_pct, results):
    data = "板块 | 今日涨跌% | 1月动量% | 3月动量% | 20日波动% | 底部结构 | >60日线\\n"
    for r in results:
        data += (f"{r['名称']} | {r['今日涨跌(%)']}% | {r['1月动量(%)']}% | {r['3月动量(%)']}% | "
                 f"{r['20日波动(%)']}% | {r['底部结构']} | {r['>60日线']}\\n")
    return f"""
你是一名冷静的量化风控总监。现在是交易日 14:30。
{holdings_str}
总资产：{assets:.0f}元，现金：{cash}元，当前总仓位：{pos_pct:.1f}%。

【14:30 实时截面数据】
{data}
已验证的规则（仅 ETF 主题池、2019-2026、同一批数据、不是样本外）：
- 动量排序：20/60/120 日动量截面排名的均值，排名靠前的板块更值得持有。
- 趋势过滤：收盘价在 60 日线之上才算合格候选。
- 逆波动加权：权重与 20 日波动成反比；本脚本只给波动，不计算买卖数量。
- 成本提示：盈亏平衡约 25~29bp 单边，换手很高，实盘成本会显著吃掉超额。

已移除、不要再提的信号：拥挤度>85、顶部钝化/顶部结构、量能确认（均无证据）。
仍保留但未验证：底部钝化/结构只作观察项，出现不代表买点。

请基于数据给出定性建议，禁止自己计算买卖数量：
1.【风险提示】持仓里哪些标的跌破 60 日线、动量排名靠后？说明依据和不确定性。
2.【观察名单】哪些板块动量靠前且站上 60 日线？还缺什么确认条件。
3.【仓位意见】整体是否需要调整仓位？如果信号互相矛盾，请直接指出矛盾，不要强行给结论。
"""


''' + s[end:]

open(SRC, "w", encoding="utf-8").write(s)
print("patched", SRC, "new sha", sha(SRC)[:16], "bytes", os.path.getsize(SRC))
