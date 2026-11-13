"""按 PREREG 的三关（跨池/跨段/同仓位对照）+ bootstrap，检验
「n 日内累计减持 m%，l 天后减仓 k 天」这族规则能不能过。

网格
----
n ∈ {10,14,20,28,40}     累计窗口（交易日）
m ∈ {-3,-4,-5,-6,-8,-10} 累计变动阈值（%）
l ∈ {0,1,2,3,5}          信号后延迟几天才减
k ∈ {10,20,40}           减仓持续天数
仓位水平 L ∈ {0.0, 0.5}  空仓 / 半仓

判据（照 PREREG 的 H1/H2 口径）
-------------------------------
G1 跨池：ext 与 base 卡玛都 ≥ 基线 + 0.05
G2 跨段：训练/验证/测试 ≥2 段卡玛 ≥ 基线
G3 对照：卡玛 ≥ 同平均仓位固定暴露 + 0.05（证明不是靠降仓）
G4 bootstrap：月度收益差（variant - 基线）CAGR 90% 区间下沿 > 0

输出 runs/20261108_nt_sell_signal/grid_gates.csv / grid_gates.md
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "work"))

from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, features, load_panel, monthly, perf, simulate,
    stationary_bootstrap_ci,
)
from nt_policy_overlay import VolTargetNT, nt_series  # noqa: E402

RUN = ROOT / "runs" / "20261108_nt_sell_signal"
CACHE: dict[str, tuple[pd.DataFrame, dict, np.ndarray, np.ndarray]] = {}


def pool_data(pool: str):
    if pool not in CACHE:
        close = load_panel(pool)
        f = features(close)
        _, arr = nt_series(close.index)
        c1 = pd.Series(arr["c1"], index=close.index)
        CACHE[pool] = (close, f, c1.to_numpy(), np.array(close.index))
    return CACHE[pool]


def mult_rule(sig: np.ndarray, l: int, k: int, level: float) -> np.ndarray:
    mult = np.ones(len(sig))
    idx = np.flatnonzero(sig)
    for i in idx:
        lo = i + l
        hi = min(len(mult), lo + k)
        if lo < len(mult):
            mult[lo:hi] = np.minimum(mult[lo:hi], level)
    return mult


def metrics(res: dict, start: int) -> dict:
    m = perf(res["ret"][start:])
    return {
        "cagr": m["cagr"] * 100, "mdd": m["mdd"] * 100, "calmar": m["calmar"],
        "sharpe": m["sharpe"], "expo": float(np.mean(res["expo"][start:])),
    }


def segments(n: int, start: int) -> list[tuple[str, int, int]]:
    live = n - start
    return [
        ("训练", start, start + int(live * 0.5)),
        ("验证", start + int(live * 0.5), start + int(live * 0.75)),
        ("测试", start + int(live * 0.75), n),
    ]


def main() -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    pool_info = {}
    for pool in ("ext", "base"):
        close, f, c1, dates = pool_data(pool)
        start = int(f["start"])
        base_res = simulate(close, f, Config(k=5, reb=5, cap=0.40, rule=VolTarget(0.15, every=1)))
        base_m = metrics(base_res, start)
        segs = segments(len(close), start)
        seg_cal = {name: perf(base_res["ret"][lo:hi])["calmar"] for name, lo, hi in segs}
        pool_info[pool] = dict(close=close, f=f, c1=c1, start=start, base=base_m, seg=seg_cal, segs=segs)
        print(f"[{pool}] 基线 年化 {base_m['cagr']:.2f}% 回撤 {base_m['mdd']:.2f}% 卡玛 {base_m['calmar']:.2f}", flush=True)

    grid = list(itertools.product((10, 14, 20, 28, 40), (-3, -4, -5, -6, -8, -10), (0, 1, 2, 3, 5), (10, 20, 40), (0.0, 0.5)))
    print(f"[grid] {len(grid)} 组", flush=True)

    rows = []
    for n, m, l, k, level in grid:
        cum = pd.Series(pool_info["ext"]["c1"]).rolling(n, min_periods=max(5, n // 2)).sum().to_numpy()
        sig = np.nan_to_num(cum, nan=9.0) <= m
        if sig.sum() < 10:
            continue
        mult = mult_rule(sig, l, k, level)
        rec = {"n": n, "m": m, "l": l, "k": k, "level": level}

        ok_pool = {}
        for pool in ("ext", "base"):
            p = pool_info[pool]
            cfg = Config(k=5, reb=5, cap=0.40, rule=VolTargetNT(VolTarget(0.15, every=1), mult, f"n{n}m{m}l{l}k{k}L{level}"))
            res = simulate(p["close"], p["f"], cfg)
            mm = metrics(res, p["start"])
            ok_pool[pool] = mm
            if pool == "ext":
                seg_ok = sum(
                    1 for name, lo, hi in p["segs"]
                    if perf(res["ret"][lo:hi])["calmar"] >= p["seg"][name]
                )
                rec["seg_pass"] = seg_ok
                rec["res"] = res
        e, b = ok_pool["ext"], ok_pool["base"]
        rec.update({
            "ext_cagr": e["cagr"], "ext_mdd": e["mdd"], "ext_calmar": e["calmar"], "ext_expo": e["expo"],
            "base_cagr": b["cagr"], "base_mdd": b["mdd"], "base_calmar": b["calmar"],
        })
        # G3：同平均仓位固定对照（ext）
        p = pool_info["ext"]
        fx = simulate(p["close"], p["f"], Config(k=5, reb=5, cap=0.40, rule=Fixed(e["expo"])))
        rec["fixed_calmar"] = perf(fx["ret"][p["start"]:])["calmar"]
        rows.append(rec)

    df = pd.DataFrame([{k: v for k, v in r.items() if k != "res"} for r in rows])
    base_cal = pool_info["ext"]["base"]["calmar"]
    base_cal_b = pool_info["base"]["base"]["calmar"]
    df["g1_pool"] = (df["ext_calmar"] >= base_cal + 0.05) & (df["base_calmar"] >= base_cal_b + 0.05)
    df["g2_seg"] = df["seg_pass"] >= 2
    df["g3_fixed"] = df["ext_calmar"] >= df["fixed_calmar"] + 0.05
    df["g4_pending"] = True

    # bootstrap 只对 G1&G2&G3 都过的做
    cand = df[df.g1_pool & df.g2_seg & df.g3_fixed].sort_values("ext_calmar", ascending=False)
    print(f"[gate] 前三关通过 {len(cand)} 组", flush=True)
    boot_rows = []
    for _, r in cand.head(20).iterrows():
        mult = None
        n, m, l, k, level = int(r.n), float(r.m), int(r.l), int(r.k), float(r.level)
        p = pool_info["ext"]
        cum = pd.Series(p["c1"]).rolling(n, min_periods=max(5, n // 2)).sum().to_numpy()
        sig = np.nan_to_num(cum, nan=9.0) <= m
        mult = mult_rule(sig, l, k, level)
        res = simulate(p["close"], p["f"], Config(k=5, reb=5, cap=0.40, rule=VolTargetNT(VolTarget(0.15, every=1), mult, "x")))
        base_res = simulate(p["close"], p["f"], Config(k=5, reb=5, cap=0.40, rule=VolTarget(0.15, every=1)))
        ra = monthly(res["ret"][p["start"]:], p["close"].index[p["start"]:], p["start"])
        rb = monthly(base_res["ret"][p["start"]:], p["close"].index[p["start"]:], p["start"])
        ci = stationary_bootstrap_ci(ra, rb)
        boot_rows.append({"n": n, "m": m, "l": l, "k": k, "level": level,
                          "calmar": r.ext_calmar, "cagr": r.ext_cagr, "fixed_calmar": r.fixed_calmar,
                          "cagr_lo": ci["cagr_lo"] * 100, "cagr_med": ci["cagr_med"] * 100,
                          "sharpe_lo": ci["sharpe_lo"], "sharpe_med": ci["sharpe_med"]})
    boot = pd.DataFrame(boot_rows)
    if len(boot):
        boot["g4_boot"] = boot["cagr_lo"] > 0
        boot["g4_sharpe"] = boot["sharpe_lo"] > -0.05  # PREREG H1 的口径：不显著变差
        for col in ("n", "m", "l", "k"):
            boot[col] = boot[col].astype(int)
            df[col] = df[col].astype(int)
        boot["level"] = boot["level"].astype(float)
        df["level"] = df["level"].astype(float)
        df = df.merge(
            boot[["n", "m", "l", "k", "level", "g4_boot", "g4_sharpe", "cagr_lo", "sharpe_lo", "sharpe_med"]],
            on=["n", "m", "l", "k", "level"], how="left",
        )

    df.to_csv(RUN / "grid_gates.csv", index=False, encoding="utf-8-sig")

    lines = ["# 「n 日累计减持 m%，l 天后减仓 k 天」网格 × PREREG 三关", ""]
    lines.append(f"- 网格 {len(df)} 组（ext 池全跑，base 池同步验证）")
    lines.append(f"- 基线 ext 卡玛 {base_cal:.2f} / base 池 {base_cal_b:.2f}")
    lines.append("")
    lines.append("## 关通过率")
    lines.append("")
    lines.append(f"- G1 跨池（两池都 ≥ 基线卡玛+0.05）：{int(df.g1_pool.sum())} / {len(df)}")
    lines.append(f"- G2 跨段（≥2 段卡玛不差于基线）：{int(df.g2_seg.sum())} / {len(df)}")
    lines.append(f"- G3 对同仓位固定对照（≥ +0.05）：{int(df.g3_fixed.sum())} / {len(df)}")
    lines.append(f"- 前三关全过：{len(cand)} / {len(df)}")
    lines.append("")
    lines.append("## 全过 + 最好的 10 组")
    lines.append("")
    lines.append("| n | m | l | k | 仓位 | ext 年化 | ext 回撤 | ext 卡玛 | 固定同仓位卡玛 | bootstrap CAGR下沿 | Sharpe下沿 | G4(CAGR>0) | G4(Sharpe>-0.05) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    def num(v):
        return v is not None and pd.notna(v)

    for _, r in cand.head(10).iterrows():
        cl = r.get("cagr_lo")
        sl = r.get("sharpe_lo")
        lines.append(
            "| {} | {}% | {} | {} | {} | {:.2f}% | {:.2f}% | {:.2f} | {:.2f} | {} | {} | {} | {} |".format(
                int(r.n), r.m, int(r.l), int(r.k), r.level, r.ext_cagr, r.ext_mdd, r.ext_calmar,
                r.fixed_calmar,
                ("%.2f%%" % float(cl)) if num(cl) else "-",
                ("%.3f" % float(sl)) if num(sl) else "-",
                ("是" if r.get("g4_boot") is True else ("否" if r.get("g4_boot") is False else "未测")),
                ("是" if r.get("g4_sharpe") is True else ("否" if r.get("g4_sharpe") is False else "未测")),
            )
        )
    lines.append("")
    lines.append("## 参考：上一版手挑的 28日≤-6%/空仓20日 在这一族里的位置")
    ref = df[(df.n == 28) & (df.m == -6) & (df.l == 0) & (df.k == 20) & (df.level == 0.0)]
    if len(ref):
        r = ref.iloc[0]
        lines.append(
            f"- 卡玛 {r.ext_calmar:.2f}（基线 {base_cal:.2f}），ext 年化 {r.ext_cagr:.2f}%，回撤 {r.ext_mdd:.2f}%；"
            f"base 池卡玛 {r.base_calmar:.2f}；固定同仓位 {r.fixed_calmar:.2f}；"
            f"G1 {'过' if r.g1_pool else '不过'} / G2 {'过' if r.g2_seg else '不过'} / G3 {'过' if r.g3_fixed else '不过'}"
        )
    (RUN / "grid_gates.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
