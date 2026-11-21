#!/usr/bin/env python3
"""总收益面板 v1→v2 修正（2026-11-21 trfix）对主结论的影响。用法：python3 work/trfix_impact.py v1|v2 <out.json>
v1 = 修正前面板快照 data/cache_pit/panel_tr2_v1_prefix.pkl；v2 = 修正后 data/cache_pit/panel_tr2.pkl。
派生缓存（recombine base.pkl）各自写到 data/cache_trfix/<ver>/，互不污染。样本内。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
ver, outp = sys.argv[1], sys.argv[2]
import tr_ens_data as TD  # noqa: E402
import recombine_core as RC0  # noqa: E402

if ver == "v1":
    TD.PKL2 = TD.ROOT / "data" / "cache_pit" / "panel_tr2_v1_prefix.pkl"
RC0.CACHE = TD.ROOT / "data" / "cache_trfix" / ver
RC0.CACHE.mkdir(parents=True, exist_ok=True)

import pv2_core as V  # noqa: E402
import prereg_satellite as PS  # noqa: E402
import sat_vt15_control as SVC  # noqa: E402

K, RC = PS.K, PS.RC
V.setup()
PS.setup()
assert PS.G["lo0"] == V.G["base_f"][V.WIN]["start"] if hasattr(V, "G") and "base_f" in V.G else True
out = {"ver": ver, "panel": str(TD.PKL2)}
for cost in (0.0005, 0.0020):
    bp = int(round(cost * 1e4))
    lo = PS.G["lo0"]
    c3 = V.core_run(cost)
    div = K.core_run(lo, cost)
    s4 = V.sat_run(cost)
    s4c = V.sat_run(cost, credit=True)
    s2v = K.sat_run(RC.START_W["W2013"], cost)
    bvt = SVC.broad_vt_run(cost)
    arms = {"C3": c3["ret"], "DIV-IV": div["ret"], "S4": s4["ret"], "S4+CREDIT": s4c["ret"], "S2+VT15": s2v["ret"],
            "BROAD_MA60_VT15": bvt["ret"]}
    for nm, sat in (("S4", s4), ("S4+CREDIT", s4c), ("S2+VT15", s2v), ("BROAD_MA60_VT15", bvt)):
        arms[f"C3 60%+{nm}"] = V.combo(c3, sat, cost)["ret"]
    res = {nm: V.full(r) for nm, r in arms.items()}
    g = {}
    for a, b in (("S4", "BROAD_MA60_VT15"), ("S4", "S2+VT15"), ("C3 60%+S4", "C3 60%+BROAD_MA60_VT15"),
                 ("C3 60%+S4", "C3"), ("S4+CREDIT", "S4")):
        x = V.gate1(arms[a], arms[b])
        g[f"{a} vs {b}"] = {k: x[k] for k in ("share_up", "med_d", "n_seg_pos")}
    out[f"{bp}bp"] = {"full_from_2014": res, "gate1": g}
    np.savez_compressed(RC0.CACHE / f"arms_{bp}bp.npz", **{k.replace("/", "_"): np.asarray(v) for k, v in arms.items()})
json.dump(out, open(outp, "w"), ensure_ascii=False, indent=1, default=float)
print("done", ver)
