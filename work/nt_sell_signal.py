"""检验：把「国家队减持」当卖出信号，到底有没有用。

信号（都能在 T 日收盘后知道，次日收盘执行，不留未来函数）
--------------------------------------------------------
* 口径：9 只沪市宽基 ETF 的份额合计（只统计当日和前一交易日都存在的代码，
  避免 588000 上市造成假跳变），日相对变化 chg1。
* sig_d1: chg1 <= -1%      （单日净赎回 1% 以上）
* sig_d2: chg1 <= -2%
* sig_d3: 3 日累计 <= -2%
* sig_d5: 5 日累计 <= -3%
* buy_d1: chg1 >= +1%（对照组：增持当买入信号）

三块检验
--------
1. 前瞻收益：信号日后 5/10/20 个交易日 510300 的收益，跟全样本基准比
2. 叠加卖出：信号出现后空仓 k 个交易日（其余满仓 510300），k=5/10/20
3. 状态过滤：只在前一日 chg 为正/负 时持仓（跟着国家队做多）

成本按单边 5bp。输出 runs/20261108_nt_sell_signal/study.md
"""

from __future__ import annotations

import csv
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "20261108_nt_sell_signal"
SHARES = ROOT / "data" / "national_team" / "etf_shares_hist.csv"
PRICE = ROOT / "data" / "cache2019" / "sh510300.csv"
COST = 0.0005


def load_shares() -> tuple[list[str], list[float]]:
    by_date: dict[str, dict[str, float]] = {}
    for r in csv.DictReader(SHARES.open(encoding="utf-8-sig", newline="")):
        by_date.setdefault(r["date"], {})[r["code"]] = float(r["shares_wan"])
    dates = sorted(by_date)
    chg = []
    for i, d in enumerate(dates):
        if i == 0:
            chg.append(float("nan"))
            continue
        a, b = by_date[dates[i - 1]], by_date[d]
        common = [c for c in b if c in a]
        chg.append((sum(b[c] for c in common) / sum(a[c] for c in common) - 1) * 100)
    return dates, chg


def load_price() -> tuple[list[str], list[float]]:
    rows = list(csv.DictReader(PRICE.open(encoding="utf-8-sig", newline="")))
    return [r["date"] for r in rows], [float(r["close"]) for r in rows]


def med(v):
    v = sorted(v)
    if not v:
        return float("nan")
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def stats_from_nav(nav: list[float], days_per_year: float = 242) -> tuple[float, float, float]:
    peak, mdd = nav[0], 0.0
    for x in nav:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    n = len(nav)
    cagr = (nav[-1] / nav[0]) ** (days_per_year / n) - 1
    return cagr, mdd, (cagr / abs(mdd) if mdd else float("nan"))


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    sdates, chg = load_shares()
    pdates, pclose = load_price()
    pidx = {d: i for i, d in enumerate(pdates)}
    cidx = {d: i for i, d in enumerate(sdates)}

    def cum(d: str, n: int) -> float:
        i = cidx[d]
        if i - n + 1 < 1:
            return float("nan")
        return (sum(chg[i - n + 1: i + 1]))

    sigs = {
        "sig_d1(≤-1%)": lambda d: chg[cidx[d]] <= -1.0,
        "sig_d2(≤-2%)": lambda d: chg[cidx[d]] <= -2.0,
        "sig_d3(3日≤-2%)": lambda d: cum(d, 3) <= -2.0,
        "sig_d5(5日≤-3%)": lambda d: cum(d, 5) <= -3.0,
        "buy_d1(≥+1%)": lambda d: chg[cidx[d]] >= 1.0,
    }

    lines = ["# 「国家队减持」当卖出信号 · 检验", ""]
    lines.append(f"- 份额样本：{sdates[0]} ~ {sdates[-1]}（{len(sdates)} 天）")
    lines.append(f"- 价格样本：510300 {pdates[0]} ~ {pdates[-1]}；成本单边 {COST * 10000:.0f}bp")
    both = [d for d in sdates if d in pidx]
    lines.append(f"- 交集：{len(both)} 天（{both[0]} ~ {both[-1]}）")
    lines.append("")

    # 基准：全样本前瞻收益
    def fwd(i: int, n: int) -> float:
        j = i + n
        return (pclose[j] / pclose[i] - 1) * 100 if j < len(pclose) else float("nan")

    base = {n: [fwd(i, n) for i in range(len(pclose) - n)] for n in (5, 10, 20)}
    lines.append("## 1. 信号日之后 510300 的收益（%）")
    lines.append("")
    lines.append("| 信号 | 命中天数 | 后5日 中位/均值 | 后10日 中位/均值 | 后20日 中位/均值 |")
    lines.append("|---|---|---|---|---|")
    base_row = "| 全样本基准 | {} | {:.2f} / {:.2f} | {:.2f} / {:.2f} | {:.2f} / {:.2f} |".format(
        len(base[5]),
        med(base[5]), st.mean(base[5]), med(base[10]), st.mean(base[10]), med(base[20]), st.mean(base[20]),
    )
    lines.append(base_row)
    hit_days: dict[str, list[str]] = {}
    for name, fn in sigs.items():
        days = [d for d in both if chg[cidx[d]] == chg[cidx[d]] and fn(d) and pidx[d] + 20 < len(pclose)]
        hit_days[name] = days
        cells = []
        for n in (5, 10, 20):
            v = [fwd(pidx[d], n) for d in days]
            v = [x for x in v if x == x]
            cells.append(f"{med(v):.2f} / {st.mean(v):.2f}" if v else "-")
        lines.append(f"| {name} | {len(days)} | " + " | ".join(cells) + " |")

    # 2. 叠加卖出
    lines.append("")
    lines.append("## 2. 叠加检验：满仓 510300，信号后空仓 k 天")
    lines.append("")
    lines.append("| 策略 | 年化 | 最大回撤 | 卡玛 | 空仓天数占比 |")
    lines.append("|---|---|---|---|---|")

    def run_overlay(sig_fn, k: int, mode: str = "exit_k"):
        nav = [1.0]
        pos = 1.0
        cool = 0
        flat_days = 0
        prev_px = pclose[0]
        for i in range(1, len(pdates)):
            d = pdates[i]
            px = pclose[i]
            ret = px / prev_px - 1
            nav[-1] *= 1 + pos * ret
            prev_px = px
            # 收盘后用当日信号决定次日仓位
            if d in cidx and chg[cidx[d]] == chg[cidx[d]]:
                if sig_fn(d):
                    cool = k
            if cool > 0:
                new_pos = 0.0
            elif mode == "exit_k":
                new_pos = 1.0
            else:
                new_pos = 1.0
            if new_pos != pos:
                nav[-1] *= 1 - COST
            if new_pos == 0:
                flat_days += 1
            pos = new_pos
            if cool > 0:
                cool -= 1
            nav.append(nav[-1])
        nav = nav[1:]
        cagr, mdd, calmar = stats_from_nav(nav)
        return cagr, mdd, calmar, flat_days / len(nav)

    bh = [1.0]
    for i in range(1, len(pdates)):
        bh.append(bh[-1] * (1 + pclose[i] / pclose[i - 1] - 1))
    cagr, mdd, calmar = stats_from_nav(bh)
    lines.append(f"| 买入持有 510300 | {cagr * 100:.2f}% | {mdd * 100:.2f}% | {calmar:.2f} | 0% |")
    for name in ("sig_d1(≤-1%)", "sig_d2(≤-2%)", "sig_d3(3日≤-2%)", "sig_d5(5日≤-3%)"):
        for k in (5, 10, 20):
            c, m, ca, flat = run_overlay(sigs[name], k)
            lines.append(f"| {name} 空仓{k}天 | {c * 100:.2f}% | {m * 100:.2f}% | {ca:.2f} | {flat * 100:.0f}% |")

    # 3. 状态过滤
    lines.append("")
    lines.append("## 3. 状态过滤：只在国家队份额「增/减」时持仓")
    lines.append("")

    def run_regime(cond, label):
        nav = [1.0]
        pos = 1.0
        flat = 0
        for i in range(1, len(pdates)):
            d = pdates[i]
            nav[-1] *= 1 + pos * (pclose[i] / pclose[i - 1] - 1)
            new_pos = 1.0 if cond(d) else 0.0
            if new_pos != pos:
                nav[-1] *= 1 - COST
            flat += 1 if new_pos == 0 else 0
            pos = new_pos
            nav.append(nav[-1])
        nav = nav[1:]
        c, m, ca = stats_from_nav(nav)
        lines.append(f"| {label} | {c * 100:.2f}% | {m * 100:.2f}% | {ca:.2f} | {flat / len(nav) * 100:.0f}% |")
        return c, m, ca

    lines.append("| 策略 | 年化 | 最大回撤 | 卡玛 | 空仓占比 |")
    lines.append("|---|---|---|---|---|")
    run_regime(lambda d: d in cidx and chg[cidx[d]] > 0, "只在前一日份额净增时持仓")
    run_regime(lambda d: d in cidx and chg[cidx[d]] <= 0, "只在前一日份额净减时持仓（反向）")
    run_regime(lambda d: d in cidx and cum(d, 5) > 0, "只在 5 日累计份额净增时持仓")
    run_regime(lambda d: d in cidx and cum(d, 5) <= 0, "只在 5 日累计份额净减时持仓（反向）")

    # 4. 条件化：信号在什么位置才有效
    ma60 = []
    for i in range(len(pclose)):
        seg = pclose[max(0, i - 59): i + 1]
        ma60.append(sum(seg) / len(seg))

    def pct250(d: str) -> float:
        i = pidx.get(d)
        if i is None or i < 60:
            return float("nan")
        seg = pclose[max(0, i - 249): i + 1]
        lo, hi = min(seg), max(seg)
        return (pclose[i] - lo) / (hi - lo) * 100 if hi > lo else 50.0

    lines.append("")
    lines.append("## 4. 条件化：卖出信号在什么位置才管用")
    lines.append("")
    lines.append("信号统一用 5 日累计 ≤ -1%（净赎回），按当时的指数位置拆开看后 20 日收益：")
    lines.append("")
    lines.append("| 条件 | 天数 | 后20日中位 | 后20日均值 | 命中率(下跌占比) |")
    lines.append("|---|---|---|---|---|")

    def show(cond, label):
        v = [fwd(pidx[d], 20) for d in both if pidx[d] + 20 < len(pclose) and cum(d, 5) <= -1.0 and cond(d)]
        v = [x for x in v if x == x]
        if not v:
            lines.append(f"| {label} | 0 | - | - | - |")
            return
        lines.append(
            f"| {label} | {len(v)} | {med(v):+.2f} | {st.mean(v):+.2f} | {sum(1 for x in v if x < 0) / len(v) * 100:.0f}% |"
        )

    show(lambda d: pclose[pidx[d]] > ma60[pidx[d]], "指数在 60 日均线之上")
    show(lambda d: pclose[pidx[d]] <= ma60[pidx[d]], "指数在 60 日均线之下")
    show(lambda d: pct250(d) >= 70, "指数在 250 日分位 ≥70%")
    show(lambda d: pct250(d) <= 30, "指数在 250 日分位 ≤30%")

    # 5. 组合规则：高位 + 净赎回 才卖
    lines.append("")
    lines.append("## 5. 组合规则：只有在「高位 + 净赎回」时才卖")
    lines.append("")
    lines.append("| 规则 | 年化 | 最大回撤 | 卡玛 | 空仓占比 |")
    lines.append("|---|---|---|---|---|")

    def show5(sig_fn, k, label):
        c, m, ca, flat = run_overlay(sig_fn, k)
        lines.append(f"| {label} | {c * 100:.2f}% | {m * 100:.2f}% | {ca:.2f} | {flat * 100:.0f}% |")

    show5(
        lambda d: cum(d, 5) <= -1.0 and pct250(d) == pct250(d) and pct250(d) >= 65,
        10,
        "分位≥65% 且 5日净赎回≤-1%，空仓10天",
    )
    show5(
        lambda d: cum(d, 5) <= -1.0 and pclose[pidx[d]] > ma60[pidx[d]],
        10,
        "指数在60日线上且 5日净赎回≤-1%，空仓10天",
    )
    show5(
        lambda d: cum(d, 5) >= 1.0 and pct250(d) == pct250(d) and pct250(d) <= 35,
        10,
        "（对照组）分位≤35% 且 5日净申购≥1%，触发也空仓10天",
    )

    (RUN / "study.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
