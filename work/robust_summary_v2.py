"""从 robust_meta.json 重生成规范 summary（c1..c5, passed）；原 robust_summary.csv 保留在 extra/。"""
import json
import os

import pandas as pd

RUN = os.path.join(r"E:\Code\quant\runs", "2026-11-02_P1-ROBUST-01")
meta = json.load(open(os.path.join(RUN, "robust_meta.json"), encoding="utf-8"))
rows = []
for pool, det in meta["detail"].items():
    v = det["verdicts"]
    keys = sorted(v.keys())
    assert len(keys) == 5, keys
    row = {"池": pool, "实盘起": det["live_start"], "实盘止": det["live_end"],
           "c1_9组合全为正": bool(v[keys[0]]), "c2_剔除后为正": bool(v[keys[1]]),
           "c3_逐年为正≥60%": bool(v[keys[2]]), "c4_去最大年为正": bool(v[keys[3]]),
           "c5_滚动3年为正≥70%": bool(v[keys[4]]),
           "滚动为正占比": det.get("roll_share"), "passed": bool(det["passed"]),
           "c3_detail": keys[2], "c4_detail": keys[3], "c5_detail": keys[4]}
    rows.append(row)
df = pd.DataFrame(rows)
out = os.path.join(RUN, "robust_summary_v2.csv")
df.to_csv(out, index=False, encoding="utf-8-sig")
print(df.to_string(index=False))
print("saved", out)
