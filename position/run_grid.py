"""仓位管理网格实验：在 S2 上对比各类仓位规则。

用法：
  python position/run_grid.py                 # ext + base
  python position/run_grid.py --pool ext      # 只跑 ext

产物写到 runs/<YYYY-MM-DD>_POS-01/：
  summary_<pool>.csv / summary_all.csv   全样本指标（含"同平均仓位固定"对照）
  subperiod_<pool>.csv                   训练/验证/测试三段
  yearly_<pool>.csv                      基线/候选的逐年
  bootstrap_<pool>.csv                   关键对比的月度 bootstrap 区间
  equity_<pool>.csv                      净值曲线
  console.log / meta.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, DrawdownThrottle, EquityMA, CashGate, Kelly, MinRule,
    features, load_panel, monthly, perf, simulate, slice_metrics,
    stationary_bootstrap_ci, yearly_table,
)


def variants():
    """(key, label, cfg) 列表。每次重新构造规则实例，避免状态串场。"""
    V = [
        ("S2", "S2 基线(满仓)", lambda: Config(rule=Fixed(1.0))),
        ("F80", "固定80%仓", lambda: Config(rule=Fixed(0.80))),
        ("F60", "固定60%仓", lambda: Config(rule=Fixed(0.60))),
        ("VT08", "波动目标8%", lambda: Config(rule=VolTarget(0.08, every=1))),
        ("VT10", "波动目标10%", lambda: Config(rule=VolTarget(0.10, every=1))),
        ("VT12", "波动目标12%", lambda: Config(rule=VolTarget(0.12, every=1))),
        ("VT15", "波动目标15%", lambda: Config(rule=VolTarget(0.15, every=1))),
        ("VT20", "波动目标20%", lambda: Config(rule=VolTarget(0.20, every=1))),
        ("VT12E5", "波动目标12%(每5日调)", lambda: Config(rule=VolTarget(0.12, every=5))),
        ("VT12E20", "波动目标12%(每20日调)", lambda: Config(rule=VolTarget(0.12, every=20))),
        ("VT12W20", "波动目标12%(20日波动)", lambda: Config(rule=VolTarget(0.12, window=20, min_periods=15))),
        ("VT12W120", "波动目标12%(120日波动)", lambda: Config(rule=VolTarget(0.12, window=120, min_periods=80))),
        ("DD", "回撤节流(10/15/20%)", lambda: Config(rule=DrawdownThrottle())),
        ("VT12DD", "波动12%+回撤节流", lambda: Config(rule=MinRule(
            [VolTarget(0.12, every=1), DrawdownThrottle()], "波动12%+回撤节流"))),
        ("EQMA100", "净值100日线(线下50%)", lambda: Config(rule=EquityMA(100, 0.5))),
        ("EQMA200", "净值200日线(线下50%)", lambda: Config(rule=EquityMA(200, 0.5))),
        ("CASH120", "等权指数120日线(线下空仓)", lambda: Config(rule=CashGate(120, 0.0))),
        ("CASH120H", "等权指数120日线(线下半仓)", lambda: Config(rule=CashGate(120, 0.5))),
        ("CAP30", "个券上限30%", lambda: Config(cap=0.30)),
        ("CAP25", "个券上限25%", lambda: Config(cap=0.25)),
        ("STOP12", "止损-12%(自成交价)", lambda: Config(stop=0.12)),
        ("STOPMA60", "跌破60日线止损", lambda: Config(stop=0.15, stop_mode="ma60")),
        ("KELLY", "半Kelly(120日)", lambda: Config(rule=Kelly(120, 0.5))),
        ("COMBO", "组合:12%+回撤+上限30%", lambda: Config(
            cap=0.30, rule=MinRule([VolTarget(0.12, every=1), DrawdownThrottle()], "波动12%+回撤节流"))),
    ]
    return V


def pct(x):
    return "" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:.2f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="ext,base")
    ap.add_argument("--out", default="")
    ap.add_argument("--cost", type=float, default=0.0005)
    a = ap.parse_args()

    out_dir = a.out or os.path.join(ROOT, "runs", f"{date.today()}_POS-01")
    os.makedirs(out_dir, exist_ok=True)
    log_lines = []

    def log(*args):
        s = " ".join(str(x) for x in args)
        print(s)
        log_lines.append(s)

    pools = [p.strip() for p in a.pool.split(",") if p.strip()]
    all_summary, all_sub, all_boot, all_eq = [], [], [], []
    meta = {"timestamp": str(pd.Timestamp.now()), "cost_bps": a.cost * 1e4, "pools": {}}

    for pool in pools:
        close = load_panel(pool)
        f = features(close)
        start = f["start"]
        T = len(close)
        log(f"\n{'=' * 70}\n池 {pool}: {close.shape[1]} 只, {close.index[0].date()}~{close.index[-1].date()}, "
            f"可投起点 {close.index[start].date()} ({T - start} 天)")
        meta["pools"][pool] = {"n_codes": int(close.shape[1]), "start": str(close.index[start].date()),
                               "end": str(close.index[-1].date())}

        # 基线自检；同时把"未加仓控的 S2 收益"给波动规则估波动（避免 sqrt 反馈）
        base = simulate(close, f, Config(cost=a.cost, rule=Fixed(1.0)))
        bm = slice_metrics(base, start, T)
        log(f"基线自检: 年化 {bm['cagr']:.2%} 回撤 {bm['mdd']:.2%} 卡玛 {bm['calmar']:.3f} "
            f"双边换手 {bm['turn_two_way']:.0%}")
        meta["pools"][pool]["baseline"] = {k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                                          for k, v in bm.items()}

        # 三段边界：与 backtest_composite 的训练/验证/测试一致（各占 50/25/25）
        n_live = T - start
        bounds = [(start, start + int(n_live * 0.5)),
                  (start + int(n_live * 0.5), start + int(n_live * 0.75)),
                  (start + int(n_live * 0.75), T)]

        rows, subs, eq_data = [], [], {"date": close.index[start:]}
        runs, ctrls = {}, {}
        factory_map = {k: fac for k, _, fac in variants()}
        for key, label, factory in variants():
            cfg = factory()
            cfg.cost = a.cost
            res = simulate(close, f, cfg)
            runs[key] = res
            m = slice_metrics(res, start, T)

            # 同平均仓位的固定仓位对照：只保留"总敞口"的择时价值
            c = float(np.clip(m["avg_expo"], 0.0, 1.0))
            ctl = simulate(close, f, Config(cost=a.cost, cap=cfg.cap, stop=cfg.stop,
                                            stop_mode=cfg.stop_mode, weight_mode=cfg.weight_mode,
                                            rule=Fixed(c)))
            ctrls[key] = ctl
            cm = slice_metrics(ctl, start, T)

            rows.append({
                "池": pool, "机制": label, "key": key,
                "年化": pct(m["cagr"]), "波动": pct(m["vol"]), "夏普": round(m["sharpe"], 2),
                "回撤": pct(m["mdd"]), "卡玛": round(m["calmar"], 2),
                "平均仓位": f"{m['avg_expo']:.0%}", "双边换手": f"{m['turn_two_way']:.0%}",
                "成本拖累": f"{m['cost_drag'] * 100:.2f}pp",
                "固定同仓位年化": pct(cm["cagr"]), "固定同仓位卡玛": round(cm["calmar"], 2),
                "卡玛-固定": round(m["calmar"] - cm["calmar"], 2),
                "夏普-固定": round(m["sharpe"] - cm["sharpe"], 2),
                "回撤-固定": pct(m["mdd"] - cm["mdd"]),
            })
            for name, (lo, hi) in zip(("训练", "验证", "测试"), bounds):
                sm = slice_metrics(res, lo, hi)
                subs.append({"池": pool, "机制": label, "段": name, "年化": pct(sm["cagr"]),
                             "回撤": pct(sm["mdd"]), "卡玛": round(sm["calmar"], 2),
                             "平均仓位": f"{sm['avg_expo']:.0%}"})
            eq_data[key] = res["ret"][start:]
            log(f"  {label:<24} 年化{rows[-1]['年化']:>8} 回撤{rows[-1]['回撤']:>8} "
                f"卡玛{rows[-1]['卡玛']:>5} 仓位{rows[-1]['平均仓位']:>4} "
                f"换手{rows[-1]['双边换手']:>5} 卡玛-固定{rows[-1]['卡玛-固定']:>6}")

        df = pd.DataFrame(rows)
        all_summary.append(df)
        all_sub.append(pd.DataFrame(subs))
        all_eq.append(pd.DataFrame(eq_data).assign(池=pool))

        # 关键对比的 bootstrap（月度收益，stationary bootstrap）
        boot_rows = []
        by_key = {r["key"]: r for r in rows}
        for key, ctl_key, ctl_label in [("VT12", "FIXED", "固定同仓位"), ("DD", "FIXED", "固定同仓位"),
                                        ("VT12DD", "FIXED", "固定同仓位"), ("COMBO", "S2", "S2"),
                                        ("CAP30", "S2", "S2"), ("STOP12", "S2", "S2")]:
            res = runs[key]
            ctl = ctrls[key] if ctl_key == "FIXED" else runs[ctl_key]
            ma = monthly(res["ret"], close.index, start)
            mb = monthly(ctl["ret"], close.index, start)
            ci = stationary_bootstrap_ci(ma, mb)
            boot_rows.append({"池": pool, "对比": f"{by_key[key]['机制']} vs {ctl_label}",
                              "Sharpe差_5%": round(ci["sharpe_lo"], 3),
                              "Sharpe差_50%": round(ci["sharpe_med"], 3),
                              "Sharpe差_95%": round(ci["sharpe_hi"], 3),
                              "CAGR差_5%": pct(ci["cagr_lo"]), "CAGR差_50%": pct(ci["cagr_med"]),
                              "CAGR差_95%": pct(ci["cagr_hi"])})
        all_boot.append(pd.DataFrame(boot_rows))

        # 逐年：基线 / VT12 / DD / COMBO
        yt = []
        for key in ("S2", "VT12", "DD", "COMBO"):
            for _, row in yearly_table(runs[key], close.index, start).iterrows():
                yt.append({"池": pool, "机制": by_key[key]["机制"], **row.to_dict()})
        pd.DataFrame(yt).to_csv(os.path.join(out_dir, f"yearly_{pool}.csv"), index=False, encoding="utf-8-sig")

        df.to_csv(os.path.join(out_dir, f"summary_{pool}.csv"), index=False, encoding="utf-8-sig")
        pd.DataFrame(subs).to_csv(os.path.join(out_dir, f"subperiod_{pool}.csv"), index=False, encoding="utf-8-sig")
        pd.DataFrame(boot_rows).to_csv(os.path.join(out_dir, f"bootstrap_{pool}.csv"), index=False, encoding="utf-8-sig")
        pd.DataFrame(eq_data).to_csv(os.path.join(out_dir, f"equity_{pool}.csv"), index=False, encoding="utf-8-sig")

    summary = pd.concat(all_summary, ignore_index=True)
    summary.to_csv(os.path.join(out_dir, "summary_all.csv"), index=False, encoding="utf-8-sig")
    pd.concat(all_sub, ignore_index=True).to_csv(os.path.join(out_dir, "subperiod_all.csv"), index=False, encoding="utf-8-sig")
    pd.concat(all_boot, ignore_index=True).to_csv(os.path.join(out_dir, "bootstrap_all.csv"), index=False, encoding="utf-8-sig")
    pd.concat(all_eq, ignore_index=True).to_csv(os.path.join(out_dir, "equity_all.csv"), index=False, encoding="utf-8-sig")

    def sha(path):
        return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]

    meta["code_sha256_16"] = {p: sha(os.path.join(ROOT, "position", p))
                              for p in ("engine.py", "run_grid.py")}
    meta["n_variants"] = len(variants())
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "console.log"), "w", encoding="utf-8") as fp:
        fp.write("\n".join(log_lines))
    print(f"\n结果已写入 {out_dir}")


if __name__ == "__main__":
    main()
