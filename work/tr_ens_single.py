#!/usr/bin/env python3
"""单支 A-ENS（波动帽 vol20/vol60/vol120 取平均）+ 第 0 步单支 A vs BH 总收益重算。
基础设施：work/ablation_v3_backtest.py（长 ETF 190 / 指数 19 / 截面 950 / 跨市场，均为总收益或指数原口径）。
规则见 runs/2026-11-20_tr_ens_trendgate/PREREG.md（已冻结）。"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ablation_v3_backtest as AB  # noqa: E402

L = AB.L
RUN = AB.ROOT / "runs" / "2026-11-20_tr_ens_trendgate" / "single"
ARMS = ["A", "A-ENS"]
NEW_ARMS = ["A-ENS"]
VOLS = ((20, 14), (60, 42), (120, 84))


def arms_positions(F):
    g1, volcap, T = L.g1_volcap_T(F)
    taper = T.map(L.TAPER_BASE).astype(float)
    A = (g1 * volcap * taper).clip(0, 1)
    caps = []
    for n, mp in VOLS:
        v = F["ret"].rolling(n, min_periods=mp).std() * np.sqrt(L.TD)
        caps.append((L.K_VOL / v).clip(L.FLOOR, 1.0).fillna(1.0))
    ens = sum((g1 * c * taper).clip(0, 1) for c in caps) / len(caps)
    comps = dict(g1=g1, volcap=volcap, taper=taper)
    return {"A": A, "A-ENS": ens.clip(0, 1)}, comps


def main():
    RUN.mkdir(parents=True, exist_ok=True)
    src = AB.ROOT / "runs" / "2026-11-14_fastcap_v5" / "cross_market_meta.csv"
    shutil.copy2(src, RUN / "cross_market_meta.csv")
    AB.RUN = RUN
    AB.ARMS = ARMS
    AB.NEW_ARMS = NEW_ARMS
    AB.arms_positions = arms_positions
    AB.main()
    (RUN / "ablation_v3_backtest.py").unlink(missing_ok=True)
    sp = RUN / "snapshot.json"
    snap = json.loads(sp.read_text(encoding="utf-8"))
    snap.pop("lowvol", None)
    snap.update(arms=ARMS, vols=VOLS, prereg="runs/2026-11-20_tr_ens_trendgate/PREREG.md",
                script="work/tr_ens_single.py", infra="work/ablation_v3_backtest.py")
    sp.write_text(json.dumps(snap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    AB.log("A-ENS 完成")


if __name__ == "__main__":
    main()
