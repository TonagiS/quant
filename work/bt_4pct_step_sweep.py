"""4% 定投法 · 步长敏感性：把 4% 换成 10%（顺带扫全部档位）

复用 work/bt_4pct_dca.py 的规则实现（估值分位闸门 + 网格 + 分批卖），只动步长与份数。
产物：runs/2026-11-13_4pct_step_sweep/
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

WORK = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("bt4", os.path.join(WORK, "bt_4pct_dca.py"))
bt4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt4)

OUT = os.path.join(bt4.ROOT, "runs", "2026-11-13_4pct_step_sweep")
os.makedirs(OUT, exist_ok=True)
CAP = bt4.CAPITAL


def run(df, step, tranches=10, start="2007-05-01", end=None, gate_low=20.0):
    r = bt4.run_4pct(df, capital=CAP, tranches=tranches, step=step, gate_low=gate_low,
                     sell_pct=80.0, start=start, end=end)
    yrs = max(r["years"], 1e-9)
    tg = r["final"] / CAP
    return {
        "期末/本金": round(tg, 3),
        "年化%": round((tg ** (1 / yrs) - 1) * 100, 2),
        "投入资金IRR%": round(bt4.irr(r["flows_deployed"]) * 100, 2),
        "最大回撤%": round(bt4.max_dd(r["equity"]) * 100, 1),
        "平均仓位%": round(float(np.mean(r["inv"])) * 100, 1),
        "买入次数": len(r["buys"]),
        "轮次": r["rounds"],
        "总投入%": round(len(r["buys"]) * 100.0 / tranches, 0),
    }, r


def main():
    log = []

    def p(*a):
        s = " ".join(str(x) for x in a)
        print(s)
        log.append(s)

    pools = {
        "hs300": bt4.build_frame("000300", "H00300", "沪深300"),
        "zz500": bt4.build_frame("000905", "H00905", "中证500"),
    }
    steps = [0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.15]
    rows = []

    for pool, df in pools.items():
        df = df[df["date"] >= pd.Timestamp("2007-05-01")].reset_index(drop=True)
        for tag, start, end in (("全期", "2007-05-01", None), ("2015起", "2015-01-01", None)):
            for st in steps:
                m, _ = run(df, st, start=start, end=end)
                rows.append({"池": pool, "区间": tag, "步长%": int(st * 100), "份数": 10, **m})
            # 10% X 5 份：和 4% X 10 份的"弹药深度"接近（0.90^5=0.590 vs 0.96^10=0.665）
            m, _ = run(df, 0.10, tranches=5, start=start, end=end)
            rows.append({"池": pool, "区间": tag, "步长%": 10, "份数": 5, **m})
            m, _ = run(df, 0.04, tranches=20, start=start, end=end)
            rows.append({"池": pool, "区间": tag, "步长%": 4, "份数": 20, **m})
            # 对照
            for nm, fn in (("买入持有", bt4.run_buyhold), ("月定投120期", bt4.run_dca)):
                r = fn(df, capital=CAP, start=start, end=end)
                yrs = max(r["years"], 1e-9)
                tg = r["final"] / CAP
                rows.append({"池": pool, "区间": tag, "步长%": "—", "份数": nm,
                             "期末/本金": round(tg, 3), "年化%": round((tg ** (1 / yrs) - 1) * 100, 2),
                             "投入资金IRR%": round(bt4.irr(r["flows_deployed"]) * 100, 2),
                             "最大回撤%": round(bt4.max_dd(r["equity"]) * 100, 1),
                             "平均仓位%": round(float(np.mean(r["inv"])) * 100, 1),
                             "买入次数": "-", "轮次": "-", "总投入%": "-"})

    tbl = pd.DataFrame(rows)
    tbl.to_csv(os.path.join(OUT, "step_sweep.csv"), index=False, encoding="utf-8-sig")
    for pool in pools:
        for tag in ("全期", "2015起"):
            sub = tbl[(tbl["池"] == pool) & (tbl["区间"] == tag)]
            p(f"\n===== {pool} {tag} =====")
            p(sub.to_string(index=False))

    # 5 年滚动窗口：每个步长 vs 买入持有胜率
    sens = []
    for pool, df in pools.items():
        d = df[df["date"] >= pd.Timestamp("2007-05-01")].reset_index(drop=True)
        for y in range(2007, 2023):
            s, e = f"{y}-01-01", f"{y+5}-01-01"
            if pd.Timestamp(e) > d["date"].max():
                e = str(d["date"].max().date())
            row = {"池": pool, "起始年": y}
            for st in steps:
                m, _ = run(d, st, start=s, end=e)
                row[f"{int(st * 100)}%"] = m["期末/本金"]
            rb = bt4.run_buyhold(d, capital=CAP, start=s, end=e)
            row["持有"] = round(rb["final"] / CAP, 3)
            rd = bt4.run_dca(d, capital=CAP, start=s, end=e)
            row["月定投"] = round(rd["final"] / CAP, 3)
            sens.append(row)
    st_tbl = pd.DataFrame(sens)
    st_tbl.to_csv(os.path.join(OUT, "rolling5y.csv"), index=False, encoding="utf-8-sig")
    p("\n[5 年滚动窗口 MOIC]")
    p(st_tbl.to_string(index=False))

    win = {"池": [], "步长%": [], "跑赢持有": [], "跑赢月定投": [], "窗口数": []}
    for pool in pools:
        sub = st_tbl[st_tbl["池"] == pool]
        for st in steps:
            k = f"{int(st * 100)}%"
            win["池"].append(pool)
            win["步长%"].append(int(st * 100))
            win["跑赢持有"].append(int((sub[k] > sub["持有"]).sum()))
            win["跑赢月定投"].append(int((sub[k] > sub["月定投"]).sum()))
            win["窗口数"].append(len(sub))
    wdf = pd.DataFrame(win)
    wdf.to_csv(os.path.join(OUT, "winrate.csv"), index=False, encoding="utf-8-sig")
    p("\n[5 年窗口胜率]")
    p(wdf.to_string(index=False))

    open(os.path.join(OUT, "console.log"), "w", encoding="utf-8").write("\n".join(log))

    # 图：步长 → 年化 / 卡玛
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(1, 2, figsize=(12, 4.6))
        for pool, c in (("hs300", "tab:blue"), ("zz500", "tab:orange")):
            sub = tbl[(tbl["池"] == pool) & (tbl["区间"] == "全期") & (tbl["份数"] == 10)].copy()
            sub = sub[sub["步长%"] != "—"]
            x = sub["步长%"].astype(int).to_numpy()
            ax[0].plot(x, sub["年化%"].to_numpy(), "o-", color=c, label=pool)
            ax[1].plot(x, (sub["年化%"] / sub["最大回撤%"].abs()).to_numpy(), "o-", color=c, label=pool)
        ax[0].set_title("步长 vs 总资产年化（10 份，2007-05~今）")
        ax[0].set_xlabel("步长 %"); ax[0].set_ylabel("年化 %"); ax[0].grid(alpha=0.3); ax[0].legend()
        ax[1].set_title("步长 vs 卡玛（年化/|回撤|）")
        ax[1].set_xlabel("步长 %"); ax[1].set_ylabel("卡玛"); ax[1].grid(alpha=0.3); ax[1].legend()
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, "step_sweep.png"), dpi=130)
        p("chart -> step_sweep.png")
    except Exception as exc:  # noqa: BLE001
        p("chart skipped:", exc)

    print(f"\n写入 {OUT}")


if __name__ == "__main__":
    main()
