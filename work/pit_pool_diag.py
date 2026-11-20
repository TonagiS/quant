#!/usr/bin/env python3
"""事后描述（不进判定）：W2013 逐年表、DIV-IV 分仓位收益贡献、VT15 对 EW/静态的月度 bootstrap 区间。"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np, pandas as pd
import pit_pool_backtest as B, pit_pool_data as D
E = B.E
RUN = B.RUN
nav = pd.read_csv(RUN / "nav_W2013.csv", index_col=0, parse_dates=True)
r = nav.pct_change().fillna(0.0)
yr = (1 + r).groupby(r.index.year).prod() - 1
mdd = r.groupby(r.index.year).apply(lambda g: ((1 + g).cumprod() / (1 + g).cumprod().cummax() - 1).min())
cols = ["PIT-S2", "PIT-VT15", "PIT-VT15-5d", "PIT-EW", "DIV-IV", "DIV-EW", "DIV-IV-A", "B92-EW"]
out = yr[cols].add_suffix("_ret").join(mdd[cols].add_suffix("_mdd"))
out.to_csv(RUN / "yearly_W2013.csv")
print(yr[cols].round(3).to_string())
# 分仓位贡献
P = D.build(); cal = P["close_tr"].index; cal = cal[(cal >= "2010-01-01") & (cal <= B.END)]
Rd, _ = B.div_returns(P, cal)
lo = cal.get_loc(pd.Timestamp("2013-01-04"))
tg, _ = B.div_targets(Rd, None, lo, "IV")
Rv = Rd.fillna(0.0)
W = pd.DataFrame(0.0, index=cal, columns=Rd.columns)
col = list(Rd.columns); w = np.zeros(len(col)); ex = {s + 1: v for s, v in tg.items()}
contrib = np.zeros((len(cal), len(col)))
for t in range(lo, len(cal)):
    rt = Rv.iloc[t].to_numpy(); g = float(w @ rt)
    contrib[t] = w * rt
    if w.sum() > 0: w = w * (1 + rt) / (1 + g)
    if t in ex:
        w = np.array([ex[t].get(c, 0.0) for c in col])
    W.iloc[t] = w
yrs = (len(cal) - lo) / 252
att = pd.DataFrame({"avg_weight": W.iloc[lo:].mean(), "sum_contrib_per_year": contrib[lo:].sum(0) / yrs}, index=col)
att.to_csv(RUN / "div_iv_attribution_W2013.csv")
print(att.round(4))
# bootstrap
res = {}
for w0, s in B.START_W.items():
    pass
m = lambda c: E.monthly(r[c].to_numpy(), r.index, 0)
k = float(pd.read_csv(RUN / "results_windows.csv").query("window=='W2013' and arm=='STATIC[PIT-VT15]'").k.iloc[0])
res["VT15_vs_EW"] = E.stationary_bootstrap_ci(m("PIT-VT15"), m("PIT-EW"))
res["VT15_vs_static"] = E.stationary_bootstrap_ci(m("PIT-VT15"), E.monthly((k * r["PIT-EW"]).to_numpy(), r.index, 0))
res["DIVIV_vs_VT15"] = E.stationary_bootstrap_ci(m("DIV-IV"), m("PIT-VT15"))
(RUN / "bootstrap_W2013.json").write_text(json.dumps(res, indent=2))
print(json.dumps(res, indent=1))
