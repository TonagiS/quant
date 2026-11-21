#!/usr/bin/env python3
"""S4 影子盘复现检验（回填）。三种场景各在独立子进程里跑冻结研究代码，比较近期调仓的目标持仓：
  research : 原研究面板（截至 2026-11-13），不打补丁 = 回测本身；
  live     : 影子盘实际流水线（研究面板 + 实时数据延长到最新交易日），只比较 ≤2026-11-13 的日子（应逐位相同）；
  ext      : 把研究面板截到 CUT=2026-09-30，再用实时拉取的数据延长到 2026-11-13（检验"实时延长"这一步本身）。
比较：S4 执行日持仓（w_hist）、宽基+MA60+VT15 / C3 / C3_60_S4 的日收益、C3 月末目标、PIT 池。
用法：python position/live/shadow_s4/replay_check_s4.py   （先跑过一次 run_s4_shadow.py 拉好数据）"""
from __future__ import annotations

import json
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import s4lib as L  # noqa: E402

CUT = "2026-09-30"
COST = 0.0005
OUT = L.HERE / "replay"


def worker(scn, path):
    P = L.research_panel()
    if scn == "live":
        P2, _ = L.extend_panel(P)
        L.patch_modules(P2)
    elif scn == "ext":
        P2, _ = L.extend_panel(P, asof=L.RESEARCH_END, cut=CUT)
        L.patch_modules(P2)
    import components_core as K
    import prereg_satellite as PS
    import recombine_core as RC
    import sat_vt15_control as SV
    D = RC.data()
    cal, cols = D["cal"], list(D["closeT"].columns)
    sd = RC.START_W["W2013"]
    s4 = K.sat_run(sd, COST, sector=True, keep=True)
    lo = int(s4["lo"])
    PS.setup()
    broad = SV.broad_vt_run(COST)
    m = K.erp_mult()[0]
    core = K.core_run(lo, COST, mult=m)
    tg, _ = K.core_targets(K.core_returns(), lo, mult=m)
    combo = RC.mix(core, s4, 0.6, lo, COST)
    wh = {str(cal[i].date()): {cols[c]: float(w) for c, w in s4["w_hist"][i].items() if w > 1e-12}
          for i in range(lo, len(cal)) if (i - lo) % 10 == 1}
    res = dict(cal=[str(d.date()) for d in cal], lo=lo, wh=wh,
               rets={k: pd.Series(np.asarray(v["ret"]), index=cal) for k, v in
                     {"S4": s4, "BROAD_VT15": broad, "C3": core, "C3_60_S4": combo}.items()},
               c3={str(cal[s].date()): w.round(10).to_dict() for s, w in tg.items()},
               members={str(k.date()): list(v) for k, v in D["lists"].items()})
    with open(path, "wb") as fh:
        pickle.dump(res, fh)


def cmp_w(a, b):
    ks = set(a) | set(b)
    return max((abs(a.get(k, 0) - b.get(k, 0)) for k in ks), default=0.0)


def main():
    if len(sys.argv) == 3:
        return worker(sys.argv[1], sys.argv[2])
    OUT.mkdir(exist_ok=True)
    R = {}
    with tempfile.TemporaryDirectory() as td:
        for scn in ("research", "live", "ext"):
            p = Path(td) / f"{scn}.pkl"
            L.log(f"scenario {scn}")
            subprocess.run([sys.executable, __file__, scn, str(p)], check=True)
            with open(p, "rb") as fh:
                R[scn] = pickle.load(fh)
    base = R["research"]
    end = str(L.RESEARCH_END.date())
    lines = ["# S4 影子盘复现检验（replay_check_s4.py 输出）", "",
             f"成本 {COST*1e4:.0f}bp；research = 回测原面板（到 {end}）。", ""]
    summary = {}
    for scn, lo_date in (("live", "2025-11-13"), ("ext", "2026-10-08")):
        o = R[scn]
        assert o["lo"] == base["lo"], (scn, o["lo"], base["lo"])
        dates = [d for d in base["wh"] if lo_date <= d <= end]
        rows = []
        for d in dates:
            md = cmp_w(base["wh"][d], o["wh"].get(d, {}))
            rows.append({"exec_date": d, "max_abs_w_diff": md,
                         "same_names": set(base["wh"][d]) == set(o["wh"].get(d, {})),
                         "research": json.dumps({k: round(v, 4) for k, v in base["wh"][d].items()}),
                         scn: json.dumps({k: round(v, 4) for k, v in o["wh"].get(d, {}).items()})})
        df = pd.DataFrame(rows)
        df.to_csv(OUT / f"s4_positions_{scn}.csv", index=False)
        rd = {}
        for arm in base["rets"]:
            a = base["rets"][arm].loc[lo_date:end]
            b = o["rets"][arm].reindex(a.index)
            dd = (a - b).abs() * 1e4
            rd[arm] = {"n": int(len(a)), "max_bp": round(float(dd.max()), 3), "median_bp": round(float(dd.median()), 4),
                       "share_le_5bp": round(float((dd <= 5).mean()), 4)}
        # 研究面板最后一天（2026-11-13，月中）会被 month_end_pos/pit_members 当成"月末"产生一条伪记录（次日生效，不影响任何已算日）；
        # 实时面板里 11-13 不是最后一天，所以没有这条。比较时排除它。
        c3d = [d for d in base["c3"] if lo_date <= d < end]
        c3max = max((cmp_w(base["c3"][d], o["c3"].get(d, {})) for d in c3d), default=0.0)
        memd = [d for d in base["members"] if lo_date <= d < end]
        mem_same = all(set(base["members"][d]) == set(o["members"].get(d, [])) for d in memd)
        summary[scn] = {"window": f"{lo_date}..{end}", "s4_exec_dates": len(df),
                        "s4_same_names_share": float(df.same_names.mean()) if len(df) else None,
                        "s4_max_abs_w_diff": float(df.max_abs_w_diff.max()) if len(df) else None,
                        "s4_share_w_within_1e-4": float((df.max_abs_w_diff <= 1e-4).mean()) if len(df) else None,
                        "ret_diff": rd, "c3_target_max_abs_diff": c3max, "pit_pools_identical": mem_same}
        lines += [f"## 场景 {scn}（{lo_date} ~ {end}）", "",
                  "```json", json.dumps(summary[scn], ensure_ascii=False, indent=1), "```", "",
                  "S4 执行日持仓对比：", "", "```", df.to_csv(index=False), "```", ""]
    (OUT / "REPLAY_CHECK.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
