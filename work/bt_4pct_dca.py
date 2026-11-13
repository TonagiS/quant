"""4% 定投法回测（研究员雷牛牛 "1+1+4"）

规则还原（视频原话）：
  1) 标的：宽基指数（沪深300 / 中证500），要求发布>7年、业绩相对稳
  2) 闸门：滚动 10 年 PE(TTM) 分位，<=20%（绿线）才允许买，>80%（红线）分批卖
  3) 节奏：单个标的的钱分 10 份，比"上一个买入点"再跌 4% 买一份，打完就呆坐

本脚本对"上一个买入点"做了两种读法：
  A 严格单锚点：全历史只认最后一个买点，永不复位 —— 结论是 2008 买过一次之后
    后面几轮低估（2012-14/2018-19/2022-24）价格都比它高，永远买不进，钱躺平。
  B 按轮次复位（默认，也是实际可执行的读法）：分位回到 50% 以上算一轮结束，
    下一轮分位<=20% 时重设锚点；卖出回笼的钱可以再投，总份数还是 10 份。

计价：中证官网 H00300/H00905 全收益指数（分红再投）；现金 0 利息（保守）。
买入触发用价格指数（≈基金净值），记账用全收益指数。
输出：E:\\Code\\quant\\runs\\20261113_4pct_dca
"""

import json
import math
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache_4pct"
OUT = ROOT / "runs" / "20261113_4pct_dca"
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

END_DATE = "20261231"
MIN_MONTHS = 24       # 分位最少历史（月）
WIN_MONTHS = 120      # 10 年窗口
CAPITAL = 1_000_000


# ---------------------------------------------------------------- 数据
def load_index(code: str) -> pd.DataFrame:
    f = CACHE / f"{code}.csv"
    if not f.exists():
        df = ak.stock_zh_index_hist_csindex(symbol=code, start_date="20050101", end_date=END_DATE)
        df.to_csv(f, index=False, encoding="utf-8-sig")
        print(f"[fetch] {code}: {len(df)} rows")
    else:
        print(f"[cache] {code}")
    df = pd.read_csv(f)
    df["date"] = pd.to_datetime(df["日期"])
    return df[["date", "收盘"]].sort_values("date").reset_index(drop=True)


def load_pe(name: str) -> pd.DataFrame:
    f = CACHE / f"pe_{name}.csv"
    if not f.exists():
        df = ak.stock_index_pe_lg(symbol=name)
        df.to_csv(f, index=False, encoding="utf-8-sig")
        print(f"[fetch] PE {name}: {len(df)} rows")
    else:
        print(f"[cache] PE {name}")
    df = pd.read_csv(f)
    df["date"] = pd.to_datetime(df["日期"])
    out = df[["date", "滚动市盈率"]].rename(columns={"滚动市盈率": "pe"})
    return out.dropna().sort_values("date").reset_index(drop=True)


def monthly_percentile(pe: pd.DataFrame) -> pd.DataFrame:
    v = pe["pe"].to_numpy(float)
    pct = np.full(len(v), np.nan)
    for i in range(len(v)):
        lo = max(0, i - WIN_MONTHS + 1)
        hist = v[lo : i + 1]
        if len(hist) < MIN_MONTHS:
            continue
        pct[i] = (hist <= v[i]).mean() * 100.0
    out = pe.copy()
    out["pct"] = pct
    return out


def build_frame(price_code: str, tr_code: str, pe_name: str) -> pd.DataFrame:
    px = load_index(price_code).rename(columns={"收盘": "px"})
    tr = load_index(tr_code).rename(columns={"收盘": "tr"})
    pe = monthly_percentile(load_pe(pe_name))
    df = px.merge(tr, on="date", how="inner").sort_values("date").reset_index(drop=True)
    df = pd.merge_asof(df, pe.rename(columns={"date": "pe_date"})[["pe_date", "pe", "pct"]],
                       left_on="date", right_on="pe_date", direction="backward")
    return df.drop(columns=["pe_date"])


# ---------------------------------------------------------------- 工具
def max_dd(curve: pd.Series) -> float:
    if curve.empty:
        return float("nan")
    return float((curve / curve.cummax() - 1.0).min())


def irr(flows, guess=0.1):
    """Newton + 二分兜底，返回年化。"""
    flows = [(d, a) for d, a in flows if abs(a) > 1e-9]
    if not flows or not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return float("nan")
    t0 = min(d for d, _ in flows)
    yrs = np.array([(d - t0).days / 365.25 for d, _ in flows])
    amt = np.array([a for _, a in flows])

    def npv(r):
        return float(np.sum(amt / (1.0 + r) ** yrs))

    lo, hi = -0.9999, 5.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return float("nan")
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if f_lo * fm <= 0:
            hi, f_hi = mid, fm
        else:
            lo, f_lo = mid, fm
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2


# ---------------------------------------------------------------- 策略 B：按轮次复位
def run_4pct(df, capital=CAPITAL, tranches=10, step=0.04, gate_low=20.0, reset_high=50.0,
             sell_pct=80.0, sell_frac=0.2, sell_cool=20, start="2007-05-01", end=None,
             reset_anchor=True):
    d = df[df["date"] >= pd.Timestamp(start)]
    if end is not None:
        d = d[d["date"] <= pd.Timestamp(end)]
    d = d.reset_index(drop=True)
    years = (d["date"].iloc[-1] - d["date"].iloc[0]).days / 365.25

    tranche = capital / tranches
    cash, units = capital, 0.0
    anchor = None
    in_round = False
    rounds = 0
    buys, sells, equity, inv = [], [], [], []
    last_sell_i = -10**9
    px, tr = d["px"].to_numpy(float), d["tr"].to_numpy(float)
    pct = d["pct"].to_numpy(float)
    dates = d["date"].tolist()

    for i in range(len(d)):
        q = pct[i]
        if not math.isnan(q):
            if reset_anchor:
                if not in_round and q <= gate_low:
                    in_round, anchor, rounds = True, None, rounds + 1
                elif in_round and q >= reset_high:
                    in_round, anchor = False, None
            else:
                if anchor is None and q <= gate_low:
                    rounds += 1

        # 卖出：估值高位，分批减仓（有冷却期，避免一天一次）
        if not math.isnan(q) and q >= sell_pct and units > 0 and (i - last_sell_i) >= sell_cool:
            qty = units * sell_frac
            cash += qty * tr[i]
            units -= qty
            sells.append({"date": dates[i], "px": round(px[i], 2), "pct": round(q, 1), "amt": round(qty * tr[i], 0)})
            last_sell_i = i

        # 买入：闸门开（估值为低估/合理偏低）且比锚点再跌 4%
        gate_open = (not math.isnan(q)) and (
            q <= gate_low if not reset_anchor else (in_round and q <= reset_high)
        )
        if gate_open and cash >= tranche - 1e-9:
            trig = anchor is None or px[i] <= anchor * (1.0 - step)
            if trig:
                units += tranche / tr[i]
                cash -= tranche
                buys.append({"date": dates[i], "px": round(px[i], 2), "pct": round(q, 1)})
                anchor = px[i]

        eq = units * tr[i] + cash
        equity.append(eq)
        inv.append(units * tr[i] / eq if eq > 0 else 0.0)

    eq = pd.Series(equity, index=d["date"])
    deployed_flows = [(b["date"], -tranche) for b in buys] + [(s["date"], s["amt"]) for s in sells]
    held_end = units * tr[-1]
    if held_end > 1e-9:
        deployed_flows.append((d["date"].iloc[-1], held_end))

    return {"equity": eq, "buys": buys, "sells": sells, "inv": inv, "years": years,
            "rounds": rounds, "flows_deployed": deployed_flows,
            "final": float(eq.iloc[-1]), "capital": capital}


def run_buyhold(df, capital=CAPITAL, start="2007-05-01", end=None):
    d = df[df["date"] >= pd.Timestamp(start)]
    if end is not None:
        d = d[d["date"] <= pd.Timestamp(end)]
    d = d.reset_index(drop=True)
    units = capital / d["tr"].iloc[0]
    eq = units * d["tr"]
    eq.index = d["date"]          # 保证 x 轴是日期，别用位置索引
    years = (d["date"].iloc[-1] - d["date"].iloc[0]).days / 365.25
    return {"equity": eq, "buys": [], "sells": [], "inv": [1.0] * len(eq), "years": years, "rounds": 1,
            "flows_deployed": [(d["date"].iloc[0], -capital), (d["date"].iloc[-1], float(eq.iloc[-1]))],
            "final": float(eq.iloc[-1]), "capital": capital}


def run_dca(df, capital=CAPITAL, start="2007-05-01", end=None, months=120):
    d = df[df["date"] >= pd.Timestamp(start)]
    if end is not None:
        d = d[d["date"] <= pd.Timestamp(end)]
    d = d.reset_index(drop=True)
    per = capital / months
    flag = ~d["date"].dt.to_period("M").duplicated()
    units, cash, flows, equity, inv, n = 0.0, capital, [], [], [], 0
    for i in range(len(d)):
        p = float(d["tr"].iloc[i])
        if flag.iloc[i] and n < months and cash >= per - 1e-9:
            units += per / p
            cash -= per
            n += 1
            flows.append((d["date"].iloc[i], -per))
        eq = units * p + cash
        equity.append(eq)
        inv.append(units * p / eq)
    eq = pd.Series(equity, index=d["date"])
    years = (d["date"].iloc[-1] - d["date"].iloc[0]).days / 365.25
    flows.append((eq.index[-1], float(eq.iloc[-1])))
    return {"equity": eq, "buys": [], "sells": [], "inv": inv, "years": years, "rounds": 1,
            "flows_deployed": flows, "final": float(eq.iloc[-1]), "capital": capital}


def run_nogate(df, capital=CAPITAL, start="2007-05-01", end=None, tranches=10, step=0.04):
    d = df[df["date"] >= pd.Timestamp(start)]
    if end is not None:
        d = d[d["date"] <= pd.Timestamp(end)]
    d = d.reset_index(drop=True)
    tranche = capital / tranches
    cash, units, anchor = capital, 0.0, None
    buys, equity, inv = [], [], []
    px, tr, dates = d["px"].to_numpy(float), d["tr"].to_numpy(float), d["date"].tolist()
    for i in range(len(d)):
        if cash >= tranche - 1e-9 and (anchor is None or px[i] <= anchor * (1.0 - step)):
            units += tranche / tr[i]
            cash -= tranche
            buys.append({"date": dates[i], "px": round(px[i], 2), "pct": None})
            anchor = px[i]
        eq = units * tr[i] + cash
        equity.append(eq)
        inv.append(units * tr[i] / eq)
    eq = pd.Series(equity, index=d["date"])
    years = (d["date"].iloc[-1] - d["date"].iloc[0]).days / 365.25
    flows = [(b["date"], -tranche) for b in buys] + [(eq.index[-1], float(eq.iloc[-1]))]
    return {"equity": eq, "buys": buys, "sells": [], "inv": inv, "years": years, "rounds": 1,
            "flows_deployed": flows, "final": float(eq.iloc[-1]), "capital": capital}


def metrics(name, r):
    final, cap, yrs = r["final"], r["capital"], max(r["years"], 1e-9)
    tg = final / cap
    return {
        "策略": name,
        "期末/本金": round(tg, 3),
        "总资产年化%": round((tg ** (1 / yrs) - 1) * 100, 2),
        "投入资金IRR%": round(irr(r["flows_deployed"]) * 100, 2),
        "最大回撤%": round(max_dd(r["equity"]) * 100, 1),
        "平均仓位%": round(float(np.mean(r["inv"])) * 100, 1),
        "买入": len(r["buys"]),
        "卖出": len(r["sells"]),
        "轮次": r["rounds"],
        "首买": str(r["buys"][0]["date"].date()) if r["buys"] else "-",
    }


def analyze(df, label, start, end=None, tag=""):
    rows, store = [], {}
    plans = [
        ("4%定投·分位<20买·>80卖（轮次复位）", dict(gate_low=20, sell_pct=80)),
        ("4%定投·分位<20买·>80卖（单锚点）", dict(gate_low=20, sell_pct=80, reset_anchor=False)),
        ("4%定投·分位<30买·>80卖", dict(gate_low=30, sell_pct=80)),
        ("4%定投·分位<50买·>80卖", dict(gate_low=50, sell_pct=80)),
    ]
    for nm, kw in plans:
        r = run_4pct(df, start=start, end=end, **kw)
        store[nm] = r
        rows.append(metrics(nm, r))
    for nm, fn in (("纯4%网格·无估值闸门·不卖", run_nogate),
                   ("买入持有", run_buyhold),
                   ("月定投120期", run_dca)):
        r = fn(df, start=start, end=end)
        store[nm] = r
        rows.append(metrics(nm, r))
    tbl = pd.DataFrame(rows)
    print(f"\n===== {label}（{start} ~ {end or '最新'}）=====")
    print(tbl.to_string(index=False))
    tbl.to_csv(OUT / f"summary_{tag}.csv", index=False, encoding="utf-8-sig")
    return tbl, store


def bucket_diag(df, tag):
    px = df["px"].to_numpy(float)
    v = np.full(len(px), np.nan)
    v[:-252] = px[252:] / px[:-252] - 1.0
    w = np.full(len(px), np.nan)
    w[:-756] = px[756:] / px[:-756] - 1.0
    t = pd.DataFrame({"pct": df["pct"], "f1": v, "f3": w}).dropna()
    t["bucket"] = pd.cut(t["pct"], [-1, 20, 50, 80, 200], labels=["<20 低估", "20-50 偏低", "50-80 偏高", ">80 高估"])
    g = t.groupby("bucket", observed=True).agg(天数=("f1", "size"), 未来1年均=("f1", "mean"), 未来1年中位=("f1", "median"),
                                               未来3年均=("f3", "mean"), 未来3年中位=("f3", "median"))
    g.to_csv(OUT / f"bucket_{tag}.csv", encoding="utf-8-sig")
    print(f"\n[估值分位 → 未来收益 {tag}，%]")
    print(g.assign(**{c: (g[c] * 100).round(1) for c in ("未来1年均", "未来1年中位", "未来3年均", "未来3年中位")}).to_string())
    return g


def main():
    df = build_frame("000300", "H00300", "沪深300")
    df = df[df["date"] >= pd.Timestamp("2005-04-08")].reset_index(drop=True)
    print("rows", len(df), "pct ready", int(df["pct"].notna().sum()),
          "first pct", df.loc[df["pct"].notna(), "date"].min().date())
    last = df.iloc[-1]
    print(f"latest {last['date'].date()} px={last['px']:.2f} pe={last['pe']:.2f} pct={last['pct']:.1f}")
    bucket_diag(df, "hs300")

    main_tbl, store = analyze(df, "沪深300 全收益", "2007-05-01", None, tag="hs300_full")
    analyze(df, "沪深300 后泡沫期", "2015-01-01", None, tag="hs300_2015")

    key = "4%定投·分位<20买·>80卖（轮次复位）"
    r = store[key]
    pd.DataFrame(r["buys"]).to_csv(OUT / "buys_main.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(r["sells"]).to_csv(OUT / "sells_main.csv", index=False, encoding="utf-8-sig")
    print(f"\n[主方案 {key}] 买入 {len(r['buys'])} 次")
    print(pd.DataFrame(r["buys"]).to_string(index=False))

    # 起始年敏感性：5 年窗口
    sens = []
    for y in range(2007, 2023):
        s, e = f"{y}-01-01", f"{y+5}-01-01"
        if pd.Timestamp(e) > df["date"].max():
            e = str(df["date"].max().date())
        row = {"起始年": y, "区间": f"{s[:4]}~{e[:4]}"}
        for nm, kw in (("定投20", dict(gate_low=20)), ("定投50", dict(gate_low=50))):
            rr = run_4pct(df, start=s, end=e, **kw)
            row[f"{nm}_MOIC"] = round(rr["final"] / rr["capital"], 3)
            row[f"{nm}_笔数"] = len(rr["buys"])
        rr = run_buyhold(df, start=s, end=e)
        row["持有_MOIC"] = round(rr["final"] / rr["capital"], 3)
        rr = run_dca(df, start=s, end=e)
        row["月定投_MOIC"] = round(rr["final"] / rr["capital"], 3)
        sens.append(row)
    sens_tbl = pd.DataFrame(sens)
    sens_tbl.to_csv(OUT / "summary_startyear.csv", index=False, encoding="utf-8-sig")
    print("\n[起始年 5 年窗口 MOIC]")
    print(sens_tbl.to_string(index=False))
    n = len(sens_tbl)
    w20 = int((sens_tbl["定投20_MOIC"] > sens_tbl["持有_MOIC"]).sum())
    w50 = int((sens_tbl["定投50_MOIC"] > sens_tbl["持有_MOIC"]).sum())
    w20d = int((sens_tbl["定投20_MOIC"] > sens_tbl["月定投_MOIC"]).sum())
    print(f"\n5年窗口跑赢买入持有: 定投20 {w20}/{n}、定投50 {w50}/{n}；跑赢月定投: {w20d}/{n}")

    try:
        df5 = build_frame("000905", "H00905", "中证500")
        df5 = df5[df5["date"] >= pd.Timestamp("2007-05-01")].reset_index(drop=True)
        bucket_diag(df5, "zz500")
        analyze(df5, "中证500 全收益", "2007-05-01", None, tag="zz500_full")
    except Exception as exc:  # noqa: BLE001
        print("中证500 skipped:", type(exc).__name__, exc)

    json.dump({"latest": {"date": str(last["date"].date()), "px": float(last["px"]),
                          "pe": float(last["pe"]), "pct": float(last["pct"])},
               "win_hold_20": w20, "win_hold_50": w50, "win_dca_20": w20d, "windows": n},
              open(OUT / "meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # 图
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(2, 1, figsize=(11.5, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
        d = df[df["date"] >= pd.Timestamp("2007-05-01")]
        for nm, kw, c in (("4%定投 <20买 >80卖", dict(gate_low=20), "tab:blue"),
                          ("4%定投 <50买 >80卖", dict(gate_low=50), "tab:orange")):
            r = run_4pct(df, start="2007-05-01", **kw)
            ax[0].plot(r["equity"].index, r["equity"].values / CAPITAL, label=nm, lw=1.5, color=c)
        rb = run_buyhold(df, start="2007-05-01")
        rd = run_dca(df, start="2007-05-01")
        ax[0].plot(rb["equity"].index, rb["equity"].values / CAPITAL, label="买入持有", lw=1.0, color="gray")
        ax[0].plot(rd["equity"].index, rd["equity"].values / CAPITAL, label="月定投120期", lw=1.0, color="tab:green", alpha=0.8)
        ax[0].axhline(1.0, color="black", lw=0.6, ls="--")
        ax[0].set_ylabel("资产 / 本金"); ax[0].legend(loc="upper left"); ax[0].grid(alpha=0.3)
        ax[0].set_title("沪深300 全收益 · 4%定投法回测（现金0息）")
        ax[1].fill_between(d["date"], d["pct"], 20, where=d["pct"] <= 20, color="green", alpha=0.35, label="分位<20 买")
        ax[1].fill_between(d["date"], d["pct"], 80, where=d["pct"] >= 80, color="red", alpha=0.35, label="分位>80 卖")
        ax[1].plot(d["date"], d["pct"], lw=0.7, color="gray")
        ax[1].set_ylim(0, 100); ax[1].set_ylabel("PE 分位 %"); ax[1].legend(loc="upper left"); ax[1].grid(alpha=0.3)
        fig.tight_layout(); fig.savefig(OUT / "equity.png", dpi=130)
        print("chart ->", OUT / "equity.png")
    except Exception as exc:  # noqa: BLE001
        print("chart skipped:", type(exc).__name__, exc)


if __name__ == "__main__":
    main()
