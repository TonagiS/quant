"""把单支定稿策略套到 POS-01 那 6 个板块 ETF 上，并与旧 POS-01（VT15 纯敞口）对照。

6 只：芯片 sh512760 / 半导体 sh512480 / CPO-5G通信 sh515050 / PCB-电子50 sh515320 /
      传媒 sh512980 / 动漫游戏 sz159869
输出：pos1_sectors_final_20261116.csv（每只 train/test/全样本 + 当前状态与目标仓位）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_final import SPLIT, TRAIN_END, policy  # noqa: E402
from single_asset_harness import TD, factors, load_universe, run_pos, stats  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runs" / "2026-11-16_pos1_sectors"
OUT.mkdir(parents=True, exist_ok=True)

TARGETS = [
    ("sh512760", "芯片(国证芯片)"),
    ("sh512480", "半导体(全指半导)"),
    ("sh515050", "CPO(5G通信)"),
    ("sh515320", "PCB(电子50)"),
    ("sh512980", "传媒"),
    ("sz159869", "动漫游戏"),
]


def vt_only(F, target=0.15):
    return (target / F["vol60"]).clip(0.0, 1.0).fillna(1.0)


def seg_metrics(net, ret, mask):
    a, b = stats(net[mask]), stats(ret[mask].fillna(0.0))
    return dict(cagr=a["cagr"], mdd=a["mdd"], calmar=a["calmar"],
                d=a["calmar"] - b["calmar"])


def main():
    uni = load_universe()
    rows, live = [], []
    for code, name in TARGETS:
        grp, df = uni[code]
        df = df[df["date"] >= SPLIT["ETF"]].reset_index(drop=True)
        F = factors(df)
        pos, T = policy(F)
        net, p = run_pos(pos, F["ret"])
        te = (df["date"] > pd.Timestamp(TRAIN_END["ETF"])).to_numpy()
        tr = ~te
        m_all, m_tr, m_te = seg_metrics(net, F["ret"], np.ones(len(df), bool)), seg_metrics(net, F["ret"], tr), seg_metrics(net, F["ret"], te)
        # 同平均仓位的固定仓位对照
        avg = float(np.nanmean(p))
        fx, _ = run_pos(pd.Series(avg, index=pos.index), F["ret"])
        d_fixed = m_all["d"] - (stats(fx)["calmar"] - stats(F["ret"].fillna(0.0))["calmar"])
        vot = vt_only(F)
        net_v, _ = run_pos(vot, F["ret"])
        cm_v = stats(net_v)["calmar"]
        rows.append(dict(
            code=code, name=name, n=len(df),
            all_cagr=m_all["cagr"], all_mdd=m_all["mdd"], all_calmar=m_all["calmar"],
            all_d=m_all["d"], d_fixed=d_fixed, avg_pos=avg,
            tr_d=m_tr["d"], te_d=m_te["d"],
            te_cagr=m_te["cagr"], te_mdd=m_te["mdd"],
            vt15_calmar=cm_v, vt15_d=cm_v - stats(F["ret"].fillna(0.0))["calmar"],
            bare_calmar=stats(F["ret"].fillna(0.0))["calmar"],
        ))
        live.append(dict(code=code, name=name, date=str(df["date"].iloc[-1].date()),
                         close=float(F["close"].iloc[-1]), T=int(T.iloc[-1]),
                         mom20=float(F["mom20"].iloc[-1]), vol60=float(F["vol60"].iloc[-1]),
                         dd=float(F["dd"].iloc[-1]), target=float(pos.iloc[-1]),
                         vt15_old=float(vot.iloc[-1])))
    r = pd.DataFrame(rows)
    l = pd.DataFrame(live)
    r.round(4).to_csv(OUT / "pos1_sectors_final_20261116.csv", index=False, encoding="utf-8-sig")
    l.round(4).to_csv(OUT / "pos1_sectors_live_20261116.csv", index=False, encoding="utf-8-sig")

    print("== 全样本（2019-01 起）==")
    print(r.assign(
        all_cagr=lambda d: (d.all_cagr * 100).map("{:.2f}%".format),
        all_mdd=lambda d: (d.all_mdd * 100).map("{:.1f}%".format),
    )[["code", "name", "all_cagr", "all_mdd", "all_calmar", "all_d", "d_fixed", "avg_pos",
       "tr_d", "te_d", "bare_calmar", "vt15_calmar", "vt15_d"]].round(3).to_string(index=False))
    print("\n中位：all_d %+.3f  d_fixed %+.3f  tr_d %+.3f  te_d %+.3f  vt15_d %+.3f  均仓 %.2f" % (
        r.all_d.median(), r.d_fixed.median(), r.tr_d.median(), r.te_d.median(),
        r.vt15_d.median(), r.avg_pos.median()))
    print("\n== 当前信号（%s）==" % l.date.iloc[0])
    print(l.assign(mom20=lambda d: (d.mom20 * 100).map("{:+.1f}%".format),
                   vol60=lambda d: (d.vol60 * 100).map("{:.1f}%".format),
                   dd=lambda d: (d.dd * 100).map("{:.1f}%".format))
          .round(2).to_string(index=False))

    # 六只等权，各按自身目标仓位（总敞口不再二次 VT）
    rets, poss = {}, {}
    for code, _ in TARGETS:
        grp, dfx = uni[code]
        dfx = dfx[dfx["date"] >= SPLIT["ETF"]].reset_index(drop=True)
        Fx = factors(dfx)
        px, _ = policy(Fx)
        rets[code] = pd.Series(Fx["ret"].to_numpy(), index=dfx["date"])
        poss[code] = pd.Series(px.to_numpy(), index=dfx["date"])
    R = pd.DataFrame(rets).sort_index()
    P = pd.DataFrame(poss).sort_index().ffill()
    port = (P.shift(1) * R).mean(axis=1).fillna(0.0)          # 等权资金分配到 6 只
    turn = (P.shift(1).diff().abs().sum(axis=1) / 6).fillna(0.0)
    port = port - 0.0005 * turn
    eq = (1 + port).cumprod()
    pm = dict(cagr=eq.iloc[-1] ** (TD / len(port)) - 1,
              mdd=float((eq / eq.cummax() - 1).min()))
    pm["calmar"] = pm["cagr"] / abs(pm["mdd"])
    bench = R.mean(axis=1).fillna(0.0)
    be = (1 + bench).cumprod()
    bm = dict(cagr=be.iloc[-1] ** (TD / len(bench)) - 1, mdd=float((be / be.cummax() - 1).min()))
    bm["calmar"] = bm["cagr"] / abs(bm["mdd"])
    print("\n== 六只等权（各按自身目标仓位，资金等分）==")
    print(f"  定稿策略  cagr {pm['cagr']:.2%}  mdd {pm['mdd']:.2%}  calmar {pm['calmar']:.3f}")
    print(f"  等权满仓  cagr {bm['cagr']:.2%}  mdd {bm['mdd']:.2%}  calmar {bm['calmar']:.3f}")


if __name__ == "__main__":
    main()
