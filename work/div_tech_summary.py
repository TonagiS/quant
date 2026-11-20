#!/usr/bin/env python3
"""红利-科技跷跷板：判定、汇总表、图。"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "2026-11-15_div_tech_seesaw"
for f in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
    if Path(f).exists():
        fm.fontManager.addfont(f); plt.rcParams["font.family"] = fm.FontProperties(fname=f).get_name()
plt.rcParams["axes.unicode_minus"] = False


def verdict(r, label="main"):
    f = r[(r.label == label) & (r.window == "full")].set_index(["code", "arm"])
    codes = r[(r.label == label)].code.unique()
    out = {}
    for arm in ["SEESAW", "SEESAW-BOND", "REVERSE"]:
        per = []
        for c in codes:
            a, b = f.loc[(c, arm)], f.loc[(c, "50/50")]
            per.append(dict(code=c, d_calmar_5050=a.calmar - b.calmar, d_calmar_static=a.calmar - a.static_calmar,
                            d_mdd_5050=a.mdd - b.mdd, win_both=bool(a.calmar > b.calmar and a.calmar > a.static_calmar)))
        p = pd.DataFrame(per).set_index("code")
        cy = p.loc["399606"]
        c1 = bool(cy.d_calmar_5050 > 0 and cy.d_calmar_static > 0 and cy.d_mdd_5050 >= -0.03)
        c2 = bool(p.d_calmar_5050.median() > 0 and p.d_calmar_static.median() > 0 and p.d_mdd_5050.median() >= -0.03)
        c3 = bool(p.win_both.mean() > 0.5)
        out[arm] = dict(c1_chinext=c1, c2_median=c2, c3_majority=c3, adopted=bool(c1 and c2 and c3),
                        chinext={k: (round(float(x), 4) if k != "win_both" else bool(x)) for k, x in cy.items()},
                        med_d_calmar_5050=round(float(p.d_calmar_5050.median()), 4),
                        med_d_calmar_static=round(float(p.d_calmar_static.median()), 4),
                        med_d_mdd_5050=round(float(p.d_mdd_5050.median()), 4),
                        share_win_both=round(float(p.win_both.mean()), 3), n_targets=len(p),
                        per_target=p.reset_index().round({"d_calmar_5050": 4, "d_calmar_static": 4, "d_mdd_5050": 4}).to_dict("records"))
    return out


def main():
    r = pd.read_csv(RUN / "results_long.csv")
    v = {"main": verdict(r), "chk_510880": verdict(r, "chk_510880"), "chk_sig000922": verdict(r, "chk_sig000922")}
    v["rule_frozen_commit"] = "c2306eb"
    (RUN / "verdict.json").write_text(json.dumps(v, ensure_ascii=False, indent=1, default=str))
    arms = ["SEESAW", "SEESAW-BOND", "REVERSE", "50/50", "BH-tech", "BH-div", "A", "RATIO"]
    full = r[(r.label == "main") & (r.window == "full") & r.arm.isin(arms)]
    full.to_csv(RUN / "summary_full.csv", index=False, encoding="utf-8-sig", float_format="%.4g")
    sub = r[(r.label == "main") & r.arm.isin(arms[:7])].pivot_table(index=["code", "window"], columns="arm", values=["cagr", "mdd"])
    sub.to_csv(RUN / "summary_subwindows.csv", encoding="utf-8-sig", float_format="%.4g")
    chk = r[(r.window == "full") & r.arm.isin(["SEESAW", "50/50", "BH-tech", "BH-div"])].pivot_table(
        index=["code", "arm"], columns="label", values="calmar")
    chk.to_csv(RUN / "summary_checks_calmar.csv", encoding="utf-8-sig", float_format="%.4g")
    print(json.dumps({k: {a: {kk: vv for kk, vv in d.items() if kk != "per_target"} for a, d in x.items()} for k, x in v.items() if isinstance(x, dict)}, ensure_ascii=False, indent=1, default=str))

    # 图 1：红利 + 状态底色 + 创业板
    P = pd.read_pickle(RUN / "_navs.pkl")
    div, st, pos = P["div"], P["dstate"], P["dpos"]
    p000922 = pd.read_csv(ROOT / "data/cache_div_tech/000922.csv", parse_dates=["date"]).set_index("date").close
    cy = pd.read_csv(ROOT / "data/cache_div_tech/sz399006.csv", parse_dates=["date"]).set_index("date").close
    fig, ax = plt.subplots(3, 1, figsize=(13, 10), sharex=True, gridspec_kw=dict(height_ratios=[3, 1.2, 2.2]))
    col = {"HIGH": "#f4a6a6", "LOW": "#9ec9f0", "NEUTRAL": "#e6e6e6"}
    s = st.dropna()
    seg = (s != s.shift()).cumsum()
    for _, g in s.groupby(seg):
        for a in ax: a.axvspan(g.index[0], g.index[-1], color=col[g.iloc[0]], alpha=.45, lw=0)
    ax[0].semilogy(div.index, div, label="中证红利全收益 H00922（信号）", color="#b22222")
    ax[0].semilogy(p000922.index, p000922, label="中证红利价格 000922", color="#e9967a", lw=.9)
    ax[0].set_title("红利状态（红=HIGH 持红利，蓝=LOW 持科技，灰=NEUTRAL 50/50）")
    ax[0].legend(loc="upper left")
    ax[1].plot(pos.index, pos, color="k", lw=.8); ax[1].axhline(.8, ls="--", c="r"); ax[1].axhline(.2, ls="--", c="b")
    ax[1].set_ylabel("750日 min-max 位置")
    ax[2].semilogy(cy.index, cy, color="#1f5fa8", label="创业板指 399006（价格）"); ax[2].legend(loc="upper left")
    fig.tight_layout(); fig.savefig(RUN / "chart_dividend_state_chinext.png", dpi=120); plt.close(fig)

    # 图 2：创业板全收益上各臂净值
    nv = P["navs"]["399606"]
    fig, ax = plt.subplots(figsize=(13, 6))
    for arm, c in [("SEESAW", "#b22222"), ("SEESAW-BOND", "#8b4513"), ("REVERSE", "#6a5acd"), ("50/50", "#2e8b57"),
                   ("BH-tech", "#1f5fa8"), ("BH-div", "#ff8c00"), ("A", "#555555")]:
        ax.semilogy(nv[arm].index, nv[arm], label=arm, color=c, lw=1.1)
    ax.set_title("创业板指全收益 399606：各臂净值（2011-06-15 起，对数坐标）"); ax.legend()
    fig.tight_layout(); fig.savefig(RUN / "chart_nav_399606.png", dpi=120); plt.close(fig)


if __name__ == "__main__":
    main()
