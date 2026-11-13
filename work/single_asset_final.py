"""单支最终策略（定稿口径）+ 实盘信号表。

仓位 = 底仓G1 × 波动上限 × 趋势衰减
  底仓 G1   ：收盘跌破 20 日动量(0%) 时——年线上行(mom20>0 且 MA200 60日斜率>0)则减半，否则清仓；
              回升到 +1% 以上复原（hyst 缓冲，防抖）
  波动上限  ：clip(0.15 / vol60, 0.4, 1.0)，vol60 = 60日年化波动
  趋势衰减  ：T0/T1 (年线上/走平) 1.0，T2 (年线上但年线下弯) 0.85，T3 (年线下、年线走平) 0.6，
              T4 (年线下且年线下弯) 0.5
状态判定阈值（收盘即可算）
  T: close vs MA200 ；MA200 60日斜率  ±2%
  V: vol60 <18% / 18~30% / >30%
  D: 回撤 -8% / -15% / -25% 四档
输出：final_summary.csv（每标的 train/test/全样本）、live_signal.csv、REPORT 用数字。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import (SPLIT, TD, TRAIN_END, build_rules, factors, hyst,  # noqa: E402
                                  load_universe, run_pos, stats)

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("SA_OUT", str(ROOT / "runs" / "2026-11-15_single_asset_state")))
K_VOL, FLOOR = 0.15, 0.4
TAPER = {0: 1.0, 1: 1.0, 2: 0.85, 3: 0.6, 4: 0.5}

HOLDING = ["sh510230", "sh512800", "sh510880", "sz159928", "sh512690"]


def policy(F):
    c = F["close"]
    bull = ((c > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)
    g1 = hyst(F["mom20"] < 0.0, F["mom20"] > 0.01, 0.0)   # level=0：跌破清仓，回 +1% 复仓
    g1 = pd.Series(np.where(g1.to_numpy() > 0, 1.0, np.where(bull.to_numpy(), 0.5, 0.0)),
                   index=c.index)
    # 跌破 20 日动量即触发：年线上行时只减半，否则清仓；回到 +1% 复原
    volcap = (K_VOL / F["vol60"]).clip(FLOOR, 1.0).fillna(1.0)
    slope = F["ma200"].pct_change(60)
    above = c > F["ma200"]
    T = np.select([above & (slope > 0.02), above & (slope >= -0.02), above,
                   (~above) & (slope >= -0.02)], [0, 1, 2, 3], default=4)
    T = pd.Series(T, index=c.index).where(F["ma200"].notna(), 1)
    taper = T.map(TAPER).astype(float)
    return (g1 * volcap * taper).clip(0.0, 1.0), T


def main():
    uni = load_universe()
    rows, live = [], []
    for code, (grp, df) in uni.items():
        key = "ETF" if grp == "ETF" else "长样本"
        df = df[df["date"] >= SPLIT[key]].reset_index(drop=True)
        if len(df) < 500:
            continue
        F = factors(df)
        pos, T = policy(F)
        ret = F["ret"]
        net, p = run_pos(pos, ret)
        tr = (df["date"] <= pd.Timestamp(TRAIN_END[key])).to_numpy()
        rec = dict(code=code, group=key, n=len(df))
        for split, mask in (("train", tr), ("test", ~tr), ("all", np.ones(len(df), bool))):
            s = stats(net[mask])
            b = stats(ret[mask].fillna(0.0))
            fx = stats(p[mask].mean() * ret[mask].fillna(0.0))
            rec[f"{split}_calmar"] = s["calmar"]
            rec[f"{split}_d"] = s["calmar"] - b["calmar"]
            rec[f"{split}_dfix"] = s["calmar"] - fx["calmar"]
            rec[f"{split}_base"] = b["calmar"]
        rec["cagr"] = stats(net)["cagr"]
        rec["mdd"] = stats(net)["mdd"]
        rec["avg_pos"] = float(p.mean())
        rec["turn"] = float(p.diff().abs().sum() / (len(p) / TD))
        rows.append(rec)

        i = len(df) - 1
        live.append(dict(code=code, group=key, date=str(df["date"].iloc[i].date()),
                         T=int(T.iloc[i]), state=f"T{int(T.iloc[i])}",
                         close=float(F["close"].iloc[i]), mom20=float(F["mom20"].iloc[i]),
                         vol60=float(F["vol60"].iloc[i]), dd=float(F["dd"].iloc[i]),
                         target=float(pos.iloc[i]), held=code in HOLDING))
    r = pd.DataFrame(rows)
    r.round(4).to_csv(OUT / "final_summary.csv", index=False, encoding="utf-8-sig")
    lv = pd.DataFrame(live).sort_values(["held", "group", "code"], ascending=[False, True, True])
    lv.round(4).to_csv(OUT / "live_signal.csv", index=False, encoding="utf-8-sig")
    (OUT / "policy_final.json").write_text(json.dumps(
        dict(k_vol=K_VOL, floor=FLOOR, taper=TAPER,
             base="跌破20日动量: 年线上行则半仓, 否则清仓; 恢复到+1%复原",
             trend="T=close vs MA200 + MA200 60日斜率±2%",
             vol="V=vol60 <18/18-30/>30%", dd="D=回撤 -8/-15/-25% 四档"),
        ensure_ascii=False, indent=2), encoding="utf-8")

    print("== 定稿策略：中位 d_calmar（vs满仓）/ d_fixed ==")
    for split in ("train", "test", "all"):
        for g in ("ETF", "长样本"):
            s = r[r.group == g]
            print(f"{split:<5} {g:<4} n={len(s):2d}  d {s[f'{split}_d'].median():+.3f} "
                  f" 为正 {(s[f'{split}_d']>0).mean():.0%}  d_fixed {s[f'{split}_dfix'].median():+.3f} "
                  f" 绝对卡玛 {s[f'{split}_calmar'].median():.3f}  仓位 {s.avg_pos.median():.2f}")
    s = r
    print(f"全部       n={len(s):2d}  d(all) {s.all_d.median():+.3f}  d_fixed(all) "
          f"{s.all_dfix.median():+.3f}  为正 {(s.all_d>0).mean():.0%}  "
          f"cagr {s.cagr.median():.2%}  mdd {s.mdd.median():.2%}  换手 {s.turn.median():.1f}/年")

    print("\n== 当前持仓状态与目标仓位 ==")
    print(lv[lv.held].round(3).to_string(index=False))
    print("\n== 全样本最差 8 只（诚实区）==")
    print(r.nsmallest(8, "all_d")[["code", "group", "all_d", "all_dfix", "avg_pos"]].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
