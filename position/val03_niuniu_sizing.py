"""VAL-03：把"牛牛 1+1+4"的加减仓规则原样搬到 S2 上（不做任何我自己的改造）

他的规则（视频原话）：
  * 只在估值分位低于绿线(20%)时开始买，最差也要在灰线(50%)以下；灰线以上只持有不买，红线(80%)以上分批抛售。
  * 相较"上一个买入点"每下跌 4% 买一份；单个标的的钱分 10 份。
  * 所以"仓位"= 已买份数 / 10，"减仓"= 高估时分批卖出（这里每 20 个交易日减 2 份）。

实现：先按上面的规则在沪深300（价格指数 + PE 分位）上预算出每天的"目标仓位"，
再把这个序列当作 S2 组合的总敞口（Rule），与 VT15 版本并列对比。
仓位序列只依赖沪深300 价格/估值，与组合净值无关，所以可以预先算好、不存在状态串场。

产物：runs/2026-11-13_VAL-03_niuniu_sizing/
"""
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

from position.engine import (  # noqa: E402
    Config, Fixed, MinRule, Rule, VolTarget, base_returns, features, monthly, perf,
    simulate, slice_metrics, stationary_bootstrap_ci,
)
from position.valuation_light import load_panel_any, pctf, valuation_pct  # noqa: E402

OUT = os.path.join(ROOT, "runs", "2026-11-13_VAL-03_niuniu_sizing")
os.makedirs(OUT, exist_ok=True)
COST = 0.0005
HS300_CSV = os.path.join(ROOT, "data", "cache_4pct", "000300.csv")


class ExposureSeries(Rule):
    """给定逐日目标敞口序列（0~1）的规则。"""

    def __init__(self, expo, name="预设敞口"):
        self.expo = np.asarray(expo, float)
        self.name = name

    def scale(self, j, ctx):
        return float(self.expo[j]) if 0 <= j < len(self.expo) else 1.0


def niuniu_exposure(dates, px, pct, gate_low=20.0, tranches=10, step=0.04,
                    sell_pct=80.0, sell_every=20, sell_tranches=2, reset_high=50.0):
    """按牛牛规则预算逐日目标仓位（份数/10）。"""
    k, anchor, in_round = 0, None, False
    last_sell = -10**9
    out = []
    for i in range(len(dates)):
        q = pct[i]
        if np.isfinite(q):
            if not in_round and q <= gate_low:
                in_round, anchor = True, None
            elif in_round and q >= reset_high:
                in_round, anchor = False, None
        # 高估分批卖
        if np.isfinite(q) and q >= sell_pct and k > 0 and (i - last_sell) >= sell_every:
            k = max(0, k - sell_tranches)
            last_sell = i
        # 低估 + 比上个买点跌 4% 加一份
        if np.isfinite(q) and in_round and q <= reset_high and k < tranches:
            if anchor is None or px[i] <= anchor * (1.0 - step):
                k += 1
                anchor = px[i]
        out.append(k / tranches)
    return np.asarray(out)


def main():
    log = []

    def p(*a):
        s = " ".join(str(x) for x in a)
        print(s)
        log.append(s)

    hs = pd.read_csv(HS300_CSV)
    hs["date"] = pd.to_datetime(hs["日期"])
    hs = hs[["date", "收盘"]].rename(columns={"收盘": "px"}).sort_values("date").reset_index(drop=True)

    p(f"VAL-03 牛牛规则当加减仓 · {pd.Timestamp.now().isoformat(timespec='seconds')} · 成本 {COST*1e4:.0f}bp")
    rows_all, subs_all, boot_all, diag = [], [], [], []

    for pool in ("ext", "long"):
        close = load_panel_any(pool)
        f = features(close)
        start, T = f["start"], len(close)
        idx = close.index
        f["base_ret"] = base_returns(close, f, Config(cost=COST))

        # 把沪深300 序列对齐到组合交易日
        hsr = pd.merge_asof(pd.DataFrame({"date": idx}), hs, on="date", direction="backward")
        px = hsr["px"].to_numpy(float)
        pct = valuation_pct(idx)

        # 预先算好三种口径的目标仓位
        expo = {
            "NN20": niuniu_exposure(idx, px, pct, gate_low=20.0),
            "NN50": niuniu_exposure(idx, px, pct, gate_low=50.0),
            "NN20xVT15": None,   # 占位，下面用 MinRule
        }
        p(f"\n===== {pool}：{close.shape[1]} 只，{idx[start].date()} ~ {idx[-1].date()} =====")
        for k, v in expo.items():
            if v is None:
                continue
            seg = v[start:]
            p(f"  牛牛仓位 {k}: 平均 {seg.mean():.0%}  空仓天数 {np.mean(seg <= 1e-9):.0%}  "
              f"满仓天数 {np.mean(seg >= 0.999):.0%}")

        cfgs = [
            ("S2", "S2 满仓", lambda: Config(rule=Fixed(1.0))),
            ("VT15", "VT15（在跑的那套）", lambda: Config(rule=VolTarget(0.15, every=1))),
            ("NN20", "牛牛仓位(绿20才买)", lambda: Config(rule=ExposureSeries(expo["NN20"], "NN20"))),
            ("NN50", "牛牛仓位(灰50以下买)", lambda: Config(rule=ExposureSeries(expo["NN50"], "NN50"))),
            ("NN20VT", "牛牛仓位 ∩ VT15", lambda: Config(rule=MinRule(
                [ExposureSeries(expo["NN20"], "NN20"), VolTarget(0.15, every=1)], "NN20∩VT15"))),
            ("NN50VT", "牛牛仓位 ∩ VT15", lambda: Config(rule=MinRule(
                [ExposureSeries(expo["NN50"], "NN50"), VolTarget(0.15, every=1)], "NN50∩VT15"))),
        ]

        runs, ctrls, rows = {}, {}, []
        for key, label, fac in cfgs:
            cfg = fac()
            cfg.cost = COST
            res = simulate(close, f, cfg)
            runs[key] = res
            m = slice_metrics(res, start, T)
            c = float(np.clip(m["avg_expo"], 0, 1))
            ctl = simulate(close, f, Config(cost=COST, rule=Fixed(c)))
            ctrls[key] = ctl
            cm = slice_metrics(ctl, start, T)
            rows.append({"池": pool, "机制": label, "key": key,
                         "年化": pctf(m["cagr"]), "夏普": round(m["sharpe"], 2),
                         "回撤": pctf(m["mdd"]), "卡玛": round(m["calmar"], 2),
                         "平均仓位": f"{m['avg_expo']:.0%}", "换手": f"{m['turn_two_way']:.0%}",
                         "同仓位固定年化": pctf(cm["cagr"]), "卡玛-固定": round(m["calmar"] - cm["calmar"], 2)})
            p(f"  {label:<22} 年化{rows[-1]['年化']:>8} 夏普{rows[-1]['夏普']:>5} 回撤{rows[-1]['回撤']:>8} "
              f"卡玛{rows[-1]['卡玛']:>5} 仓位{rows[-1]['平均仓位']:>4} 卡玛-固定{rows[-1]['卡玛-固定']:>6}")
        tbl = pd.DataFrame(rows)
        tbl.to_csv(os.path.join(OUT, f"summary_{pool}.csv"), index=False, encoding="utf-8-sig")
        rows_all.append(tbl)

        # 三段
        n_live = T - start
        bounds = [(start, start + int(n_live * .5)), (start + int(n_live * .5), start + int(n_live * .75)),
                  (start + int(n_live * .75), T)]
        for key in ("VT15", "NN20", "NN50", "NN20VT", "NN50VT"):
            seg = []
            for nm, (lo, hi) in zip(("训练", "验证", "测试"), bounds):
                sm = slice_metrics(runs[key], lo, hi)
                seg.append(f"{nm} {sm['cagr']*100:.1f}%/卡玛{sm['calmar']:.2f}")
            p(f"  [{key}] " + "  ".join(seg))
            subs_all.append({"池": pool, "key": key, "三段": " | ".join(seg)})

        # bootstrap
        m0 = monthly(runs["VT15"]["ret"], idx, start)
        for key in ("NN20", "NN50", "NN20VT", "NN50VT"):
            ci = stationary_bootstrap_ci(monthly(runs[key]["ret"], idx, start), m0)
            boot_all.append({"池": pool, "对比": f"{key} vs VT15",
                             "Sharpe差_5%": round(ci["sharpe_lo"], 3), "Sharpe差_50%": round(ci["sharpe_med"], 3),
                             "Sharpe差_95%": round(ci["sharpe_hi"], 3),
                             "CAGR差_5%": f"{ci['cagr_lo']*100:.2f}%", "CAGR差_50%": f"{ci['cagr_med']*100:.2f}%",
                             "CAGR差_95%": f"{ci['cagr_hi']*100:.2f}%"})

        diag.append({"池": pool,
                     "NN20平均仓位": round(float(np.mean(expo["NN20"][start:])), 3),
                     "NN20空仓占比": round(float(np.mean(expo["NN20"][start:] <= 1e-9)), 3),
                     "NN50平均仓位": round(float(np.mean(expo["NN50"][start:])), 3),
                     "VT15平均仓位": round(float(np.mean(runs["VT15"]["expo"][start:])), 3)})

    pd.concat(rows_all, ignore_index=True).to_csv(os.path.join(OUT, "summary_all.csv"),
                                                 index=False, encoding="utf-8-sig")
    bt = pd.DataFrame(boot_all)
    bt.to_csv(os.path.join(OUT, "bootstrap.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(diag).to_csv(os.path.join(OUT, "diag.csv"), index=False, encoding="utf-8-sig")
    p("\n[bootstrap 月度 90% 区间]")
    p(bt.to_string(index=False))
    p("\n[仓位诊断]")
    p(pd.DataFrame(diag).to_string(index=False))

    # 图：仓位序列
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False
        fig, ax = plt.subplots(2, 1, figsize=(11.5, 7), sharex=True)
        for k, pool in enumerate(("ext", "long")):
            close = load_panel_any(pool)
            f = features(close)
            idx = close.index
            hs_ = pd.read_csv(HS300_CSV)
            hs_["date"] = pd.to_datetime(hs_["日期"])
            hs_ = hs_[["date", "收盘"]].rename(columns={"收盘": "px"}).sort_values("date")
            hsr = pd.merge_asof(pd.DataFrame({"date": idx}), hs_, on="date", direction="backward")
            pct = valuation_pct(idx)
            e20 = niuniu_exposure(idx, hsr["px"].to_numpy(float), pct, gate_low=20.0)
            e50 = niuniu_exposure(idx, hsr["px"].to_numpy(float), pct, gate_low=50.0)
            ax[k].plot(idx, e20, label="牛牛仓位(绿20)", lw=1.3)
            ax[k].plot(idx, e50, label="牛牛仓位(灰50)", lw=1.1, alpha=0.8)
            ax[k].step(idx, np.clip(0.15 / (pd.Series(close.pct_change(fill_method=None).mean(axis=1)).rolling(
                60, min_periods=40).std() * np.sqrt(252)).fillna(0.15), 0, 1),
                label="VT15 仓位", lw=1.0, alpha=0.7, where="post")
            ax[k].set_title(f"{pool} 池：三种加减仓标准的仓位")
            ax[k].set_ylim(0, 1.05); ax[k].grid(alpha=0.3); ax[k].legend(loc="upper left", fontsize=9)
        fig.tight_layout(); fig.savefig(os.path.join(OUT, "exposure.png"), dpi=130)
        p("chart -> exposure.png")
    except Exception as exc:  # noqa: BLE001
        p("chart skipped:", exc)

    open(os.path.join(OUT, "console.log"), "w", encoding="utf-8").write("\n".join(log))
    print(f"\n写入 {OUT}")


if __name__ == "__main__":
    main()
