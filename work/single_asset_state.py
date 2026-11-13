"""单支状态机：用数值阈值给"这只标的此刻处在什么状态"分类，再按状态换策略。

状态定义（全部可数值化，收盘即可判定）
  趋势 T（价格 vs 年线 MA200，年线 60 日斜率）
    T0 强多：close>MA200 且 MA200 60日斜率 > +2%
    T1 弱多：close>MA200 且 斜率 ∈ [-2%, +2%]
    T2 转弱：close>MA200 且 斜率 < -2%（价格还在上面，年线已下弯）
    T3 弱空：close<MA200 且 斜率 ≥ -2%
    T4 强空：close<MA200 且 斜率 < -2%
  回撤 D（相对历史最高收盘）
    D0 >-8%   D1 -8~-15%   D2 -15~-25%   D3 <-25%
  波动 V（60日年化，长样本固定阈值，跨标的可比）
    V0 <18%   V1 18~30%   V2 >30%
  策略态 = T×D（20 个），辅助看 T×V

流程
  1) train 段（ETF 2019-2022 / 长样本 2007-2015）里，对每个状态挑"相对满仓的日均超额"最大的候选规则
  2) test 段（2023+ / 2016+）端到端回测，不参与选择
  3) 输出状态→策略表、状态统计表、test 结果表、今日实盘信号
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import (SPLIT, TD, TRAIN_END, build_rules, factors,  # noqa: E402
                                  load_universe, run_pos, stats)

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("SA_OUT", str(ROOT / "runs" / "2026-11-15_single_asset_state")))
OUT.mkdir(parents=True, exist_ok=True)

MIN_TRAIN_DAYS = 400

CANDS = ["满仓", "U14 牛慢熊快", "B_mom20清仓", "B_mom20半仓", "A_MA30清仓",
         "A_MA60清仓", "F_周MA20清仓", "C_VT12", "X_投票四件套"]


def state_series(F):
    c, ma200 = F["close"], F["ma200"]
    slope = ma200.pct_change(60)
    above = c > ma200
    T = np.select(
        [above & (slope > 0.02), above & (slope >= -0.02), above,
         (~above) & (slope >= -0.02)],
        [0, 1, 2, 3], default=4)
    vol = F["vol60"]
    V = np.select([vol < 0.18, vol < 0.30], [0, 1], default=2)
    dd = F["dd"]
    D = np.select([dd > -0.08, dd >= -0.15, dd >= -0.25], [0, 1, 2], default=3)
    T = pd.Series(T, index=c.index).where(ma200.notna(), 1)
    V = pd.Series(V, index=c.index)
    D = pd.Series(D, index=c.index)
    return T, V, D


def labeled(T, V, D, mode="TD"):
    if mode == "TD":
        return T.astype(int).astype(str) + D.astype(int).astype(str)
    return T.astype(int).astype(str) + V.astype(int).astype(str)


def build(uni):
    """返回 {code: dict(pos_cands, st, V, D, ret, df)}"""
    R = build_rules()
    out = {}
    for code, (grp, df) in uni.items():
        key = "ETF" if grp == "ETF" else "长样本"
        df = df[df["date"] >= SPLIT[key]].reset_index(drop=True)
        if len(df) < 500:
            continue
        F = factors(df)
        T, V, D = state_series(F)
        pos = {}
        vote = pd.concat([R["A_MA60清仓"](F), R["B_mom60清仓"](F), R["F_周MA20清仓"](F),
                          R["D_dd15半仓"](F)], axis=1).mean(axis=1).clip(0, 1)
        bull = ((F["close"] > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)
        u14 = R["B_mom20清仓"](F).where(~bull, R["B_mom20半仓"](F))
        for nm in CANDS:
            if nm == "满仓":
                pos[nm] = pd.Series(1.0, index=F["close"].index)
            elif nm == "X_投票四件套":
                pos[nm] = vote
            elif nm == "U14 牛慢熊快":
                pos[nm] = u14.clip(0, 1)
            else:
                pos[nm] = R[nm](F).clip(0, 1)
        part = pd.concat({nm: pos[nm].shift(1) for nm in CANDS}, axis=1)
        part.columns = CANDS
        out[code] = dict(key=key, dates=df["date"], ret=F["ret"], F=F,
                         st=labeled(T, V, D), st_v=labeled(T, V, D, "TV"),
                         T=T, V=V, D=D, pos=part)
    return out


def train_stats(data):
    frames = []
    for code, d in data.items():
        tr = (d["dates"] <= pd.Timestamp(TRAIN_END[d["key"]])).to_numpy()
        x = d["pos"][tr].copy()
        x["ret"] = d["ret"].to_numpy()[tr]
        x["st"] = d["st"].to_numpy()[tr]
        x["code"] = code
        x["group"] = d["key"]
        frames.append(x)
    return pd.concat(frames, ignore_index=True)


def pick_policy(alltr):
    """每个状态选出相对满仓日均超额最大的候选规则；样本不足回落投票四件套。"""
    rows, pol = [], {}
    for st, g in alltr.groupby("st"):
        base = g["ret"].mean()
        n = len(g)
        rec = dict(state=st, n_days=n, n_assets=g["code"].nunique(),
                   base_mean_bp=base * 1e4, base_pos_rate=float((g["ret"] > 0).mean()),
                   fwd_vol=float(g["ret"].std() * np.sqrt(252)))
        best, best_v = None, -9e9
        for nm in CANDS:
            alpha = (g[nm] * g["ret"] - g["ret"]).mean() * 1e4
            rec[f"a_{nm}"] = alpha
            if alpha > best_v:
                best, best_v = nm, alpha
        if n < MIN_TRAIN_DAYS:
            best = "X_投票四件套"
        rec["best"] = best
        rec["best_alpha_bp"] = best_v
        rec["best_avg_pos"] = float(g[best].mean())
        pol[st] = best
        rows.append(rec)
    return pol, pd.DataFrame(rows)


def run_policy(d, pol, split):
    tr = (d["dates"] <= pd.Timestamp(TRAIN_END[d["key"]])).to_numpy()
    mask = tr if split == "train" else ~tr
    pos = pd.Series(np.nan, index=d["pos"].index)
    for st, rule in pol.items():
        m = (d["st"] == st).to_numpy()
        pos[m] = d["pos"][rule].to_numpy()[m]
    pos = pos.ffill().fillna(1.0)
    net, p = run_pos(pos, d["ret"])
    st_ = stats(net[mask])
    b = stats(d["ret"][mask].fillna(0.0))
    # 参照：全局最优单规则（mom20清仓）、投票四件套、U14 牛慢熊快
    ref = {}
    for nm in ("B_mom20清仓", "X_投票四件套", "U14 牛慢熊快"):
        n2, p2 = run_pos(d["pos"][nm].fillna(1.0), d["ret"])
        ref[nm] = stats(n2[mask])
    return dict(calmar=st_["calmar"], cagr=st_["cagr"], mdd=st_["mdd"], sharpe=st_["sharpe"],
                base_calmar=b["calmar"], d_calmar=st_["calmar"] - b["calmar"],
                avg_pos=float(p[mask].mean()),
                turn=float(p.diff().abs().sum() / (len(p) / TD)),
                ref_mom20=ref["B_mom20清仓"]["calmar"] - b["calmar"],
                ref_vote=ref["X_投票四件套"]["calmar"] - b["calmar"],
                ref_u14=ref["U14 牛慢熊快"]["calmar"] - b["calmar"])


def main():
    uni = load_universe()
    data = build(uni)
    print(f"标的 {len(data)}（ETF {sum(1 for d in data.values() if d['key']=='ETF')}）")

    alltr = train_stats(data)
    pol, tab = pick_policy(alltr)
    tab.round(3).to_csv(OUT / "state_train.csv", index=False, encoding="utf-8-sig")
    (OUT / "policy.json").write_text(json.dumps(pol, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n== 训练段状态统计（bp=日均，正=偏多）==")
    show = tab[["state", "n_days", "n_assets", "base_mean_bp", "base_pos_rate", "fwd_vol",
                "best", "best_alpha_bp", "best_avg_pos"]]
    print(show.round(2).to_string(index=False))

    recs = []
    for code, d in data.items():
        for split in ("train", "test"):
            r = run_policy(d, pol, split)
            recs.append(dict(code=code, group=d["key"], split=split, **r))
    res = pd.DataFrame(recs)
    res.round(3).to_csv(OUT / "state_results.csv", index=False, encoding="utf-8-sig")

    print("\n== 策略效果（中位）==")
    for split in ("train", "test"):
        s = res[res.split == split]
        print(f"[{split}] d_calmar 中位 {s.d_calmar.median():+.3f}  为正 {(s.d_calmar>0).mean():.0%}  "
              f"vs mom20 {s.ref_mom20.median():+.3f}  vs 投票 {s.ref_vote.median():+.3f}  "
              f"平均仓位 {s.avg_pos.median():.2f}  换手 {s.turn.median():.1f}/年")
        for g in ("ETF", "长样本"):
            sg = s[s.group == g]
            if len(sg):
                print(f"    {g:<4} n={len(sg):2d}  d_calmar {sg.d_calmar.median():+.3f}  "
                      f"为正 {(sg.d_calmar>0).mean():.0%}  绝对卡玛 {sg.calmar.median():.3f}")

    # 今日信号
    live = []
    for code, d in data.items():
        i = len(d["dates"]) - 1
        st = d["st"].iloc[i]
        rule = pol.get(st, "X_投票四件套")
        live.append(dict(code=code, group=d["key"], date=str(d["dates"].iloc[-1].date()),
                         state=st, rule=rule, target_pos=float(d["pos"][rule].iloc[i]),
                         mom20=float(d["F"]["mom20"].iloc[i]), dd=float(d["F"]["dd"].iloc[i]),
                         vol60=float(d["F"]["vol60"].iloc[i])))
    live = pd.DataFrame(live).sort_values(["group", "code"])
    live.round(4).to_csv(OUT / "live_signal.csv", index=False, encoding="utf-8-sig")
    print("\n== 今日信号（前 8 行）==")
    print(live.head(8).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
