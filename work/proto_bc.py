import sys, numpy as np, pandas as pd
sys.path.insert(0, r"E:\Code\quant")
import backtest_composite as bc
from work.proto_s2 import load_close, features

close = load_close("ext")
amt = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
f = bc.build_features(close, amt, 120)
valid = f["valid"]
rk = {k: bc.xs_rank(f[k], valid, 8) for k in ("mom20", "mom60", "mom120")}
rm = (rk["mom20"] + rk["mom60"] + rk["mom120"]) / 3
enough = rm.notna().any(axis=1).to_numpy()
start = int(np.argmax(enough))
ret = f["ret"].to_numpy(); vol = f["vol20"].to_numpy()
lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
lim_up, lim_dn = ret >= lim * 0.98, ret <= -lim * 0.98
cand = (valid & rm.notna() & (close > f["ma60"])).to_numpy()
rm_fill = np.nan_to_num((rm - 0.5).to_numpy(), nan=0.0)
p, turn = bc.run_portfolio(ret, rm_fill, cand, vol, lim_up, lim_dn, 5, 5, 0.0005, 1, start, invvol=True)
m = bc.perf(p[start:])
print("bc engine", {k: round(v, 4) for k, v in m.items()}, "turn_mean", round(turn, 3),
      "annual two-way %", round(turn * 252 / 5 * 100, 0))
print("start", close.index[start].date())
