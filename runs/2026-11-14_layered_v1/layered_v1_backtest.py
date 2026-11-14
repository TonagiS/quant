#!/usr/bin/env python3
"""分层仓位规则 v1 回测（预登记冻结值，不开网格）。

对照臂：
  A. 旧定稿 G1 × clip(0.15/vol60, 0.4, 1.0) × taper
  B. 分层规则 v1（慢速组 / 主动组）
同时计算满仓 BH。口径与 runs/2026-11-14_fullmkt_single_asset 相同。
权威规则：runs/2026-11-14_layered_v1/PREREG.md
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PRIOR = ROOT / "runs" / "2026-11-14_fullmkt_single_asset"
RUN = ROOT / "runs" / "2026-11-14_layered_v1"
CACHE = ROOT / "data" / "cache_ohlc_full"
RUN.mkdir(parents=True, exist_ok=True)

EVAL_START = "2019-01-01"
TRAIN_END = "2022-12-31"
TEST_START = "2023-01-01"
TEST_END = "2026-11-13"
TD = 252
COST = 0.0005
MIN_BARS_CROSS = 250
MIN_BARS_STATS = 60
MIN_MA200_VALID = 60
MIN_AVG_AMOUNT = 5_000_000
K_VOL, FLOOR = 0.15, 0.4
TAPER_BASE = {0: 1.0, 1: 1.0, 2: 0.85, 3: 0.6, 4: 0.5}

# Layered v1 frozen numbers (do not change)
CONFIRM_DAYS = 3
SLOW_POS_T4 = 0.5
SLOW_POS_ELSE = 1.0
STATE_COEF = {"UP": 1.00, "SIDE": 0.70, "DOWN": 0.50}
VOL_TARGET = 0.20
VOL_FLOOR = 0.50
G1_SIDE_LEVEL = 0.50
G1_DOWN_LEVEL = 0.00
MOM_REDUCE = 0.0
MOM_RECOVER = 0.01
SLOPE_TH = -0.02

sys.path.insert(0, str(Path(__file__).resolve().parent))
from single_asset_harness import hyst, run_pos, stats  # noqa: E402

FOCUS_THEMES = ["科创芯片", "半导体设备", "机器人", "银行", "猪肉", "传媒"]
KEY_SECTORS = [
    "芯片/半导体", "半导体设备", "机器人", "传媒", "银行",
    "风格-红利/低波", "商品-黄金", "宽基", "债券", "养殖/畜牧",
]


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:  # noqa: BLE001
        return None


def slim_factors(df: pd.DataFrame) -> dict:
    if "date" in df.columns:
        df = df.set_index("date")
    c = df["close"].astype(float)
    F = {"close": c, "ret": c.pct_change()}
    F["ma200"] = c.rolling(200, min_periods=200).mean()
    F["slope60"] = F["ma200"].pct_change(60)
    F["mom20"] = c / c.shift(20) - 1
    F["vol60"] = F["ret"].rolling(60, min_periods=42).std() * np.sqrt(TD)
    if "amount" in df.columns:
        F["amount"] = df["amount"].astype(float)
    else:
        F["amount"] = df["volume"].astype(float) * c * 100
    return F


def g1_volcap_T(F: dict):
    """定稿 G1 / volcap / T，与 fullmkt_single_asset / single_asset_final 一致。"""
    c = F["close"]
    bull = ((c > F["ma200"]) & (F["ma200"] > F["ma200"].shift(60))).fillna(True)
    g1 = hyst(F["mom20"] < 0.0, F["mom20"] > 0.01, 0.0)
    g1 = pd.Series(np.where(g1.to_numpy() > 0, 1.0, np.where(bull.to_numpy(), 0.5, 0.0)),
                   index=c.index)
    volcap = (K_VOL / F["vol60"]).clip(FLOOR, 1.0).fillna(1.0)
    slope = F["slope60"] if "slope60" in F else F["ma200"].pct_change(60)
    above = c > F["ma200"]
    T = np.select([above & (slope > 0.02), above & (slope >= -0.02), above,
                   (~above) & (slope >= -0.02)], [0, 1, 2, 3], default=4)
    T = pd.Series(T, index=c.index).where(F["ma200"].notna(), 1)
    return g1, volcap, T


def policy_baseline(F):
    g1, volcap, T = g1_volcap_T(F)
    taper = T.map(TAPER_BASE).astype(float)
    return (g1 * volcap * taper).clip(0.0, 1.0), T


def is_slow_group(category: str, sector: str) -> bool:
    return category == "债券" or sector == "银行" or sector == "风格-红利/低波"


def candidate_state(F: dict) -> pd.Series:
    """按收盘计算候选状态。MA200 或 slope60 不足时视为震荡（无法确认上/下行条件）。"""
    c = F["close"]
    ma = F["ma200"]
    sl = F["slope60"]
    ma_ok = ma.notna()
    sl_ok = sl.notna()
    above = c > ma
    up = ma_ok & sl_ok & above & (sl >= SLOPE_TH)
    down = ma_ok & sl_ok & (c <= ma) & (sl < SLOPE_TH)
    side = ma_ok & sl_ok & ((above & (sl < SLOPE_TH)) | ((c <= ma) & (sl >= SLOPE_TH)))
    cand = np.select([up, down, side], ["UP", "DOWN", "SIDE"], default="SIDE")
    return pd.Series(cand, index=c.index)


def confirm_state(candidate: pd.Series, confirm: int = CONFIRM_DAYS) -> pd.Series:
    """连续 confirm 个交易日为同一候选才切换；首日直接取当日候选。"""
    n = len(candidate)
    out = np.empty(n, dtype=object)
    vals = candidate.to_numpy()
    if n == 0:
        return pd.Series(out, index=candidate.index)
    out[0] = vals[0]
    streak = 1
    for i in range(1, n):
        if vals[i] == vals[i - 1]:
            streak += 1
        else:
            streak = 1
        out[i] = vals[i] if streak >= confirm else out[i - 1]
    return pd.Series(out, index=candidate.index)


def g1_active(state: pd.Series, mom20: pd.Series) -> pd.Series:
    """按状态的动量开关。状态刚切换时按 mom20 相对 0% / 1% 初始化，之后保持缓冲。"""
    n = len(state)
    out = np.ones(n, dtype=float)
    st = state.to_numpy()
    m = mom20.to_numpy()
    prev = None
    cur = 1.0
    for i in range(n):
        s = st[i]
        mi = m[i]
        if s == "UP":
            cur = 1.0
        else:
            level = G1_SIDE_LEVEL if s == "SIDE" else G1_DOWN_LEVEL
            if s != prev:
                if pd.notna(mi) and mi < MOM_REDUCE:
                    cur = level
                else:
                    # mi > 1% 或落在缓冲带或缺失：未触发，记为已恢复
                    cur = 1.0
            else:
                if pd.notna(mi):
                    if mi < MOM_REDUCE:
                        cur = level
                    elif mi > MOM_RECOVER:
                        cur = 1.0
        out[i] = cur
        prev = s
    return pd.Series(out, index=state.index)


def policy_layered(F: dict, slow: bool):
    c = F["close"]
    ma = F["ma200"]
    sl = F["slope60"]
    if slow:
        t4 = ma.notna() & (c < ma) & sl.notna() & (sl < SLOPE_TH)
        pos = pd.Series(np.where(t4.to_numpy(), SLOW_POS_T4, SLOW_POS_ELSE), index=c.index)
        cand = candidate_state(F)
        st = confirm_state(cand)
        info = dict(candidate=cand, state=st, state_coef=pd.Series(1.0, index=c.index),
                    vol_scale=pd.Series(1.0, index=c.index), g1=pd.Series(1.0, index=c.index),
                    t4=t4.astype(int))
        return pos.clip(0.0, 1.0), info

    cand = candidate_state(F)
    st = confirm_state(cand)
    coef = st.map(STATE_COEF).astype(float)
    vol = F["vol60"]
    raw = (VOL_TARGET / vol).clip(VOL_FLOOR, 1.0)
    vol_scale = pd.Series(np.where(st.eq("UP"), 1.0, raw.fillna(1.0).to_numpy()), index=c.index)
    # UP already 1; SIDE/DOWN use clip, missing vol -> 1
    vol_scale = vol_scale.where(~(st.ne("UP") & vol.isna()), 1.0)
    g1 = g1_active(st, F["mom20"])
    pos = (coef * vol_scale * g1).clip(0.0, 1.0)
    info = dict(candidate=cand, state=st, state_coef=coef, vol_scale=vol_scale, g1=g1,
                t4=(st == "DOWN").astype(int))
    return pos, info


def split_masks(dates: pd.Series) -> dict:
    d = pd.to_datetime(dates)
    ev = (d >= pd.Timestamp(EVAL_START)) & (d <= pd.Timestamp(TEST_END))
    return {
        "train": ev & (d <= pd.Timestamp(TRAIN_END)),
        "test": ev & (d >= pd.Timestamp(TEST_START)),
        "all": ev,
    }


def metrics_block(net: pd.Series, ret: pd.Series, pos_shifted: pd.Series, mask: np.ndarray) -> dict:
    net_s = net[mask]
    ret_s = ret[mask].fillna(0.0)
    p_s = pos_shifted[mask]
    s = stats(net_s)
    b = stats(ret_s)
    avg = float(p_s.mean()) if len(p_s) else np.nan
    fx = stats(avg * ret_s) if len(p_s) >= MIN_BARS_STATS else dict(
        cagr=np.nan, mdd=np.nan, calmar=np.nan, sharpe=np.nan
    )
    n = int(mask.sum())
    out = {"n": n}
    out.update(s)
    out["bh_cagr"] = b["cagr"]
    out["bh_mdd"] = b["mdd"]
    out["bh_calmar"] = b["calmar"]
    out["d_calmar"] = (s["calmar"] - b["calmar"]) if pd.notna(s["calmar"]) and pd.notna(b["calmar"]) else np.nan
    out["d_cagr"] = (s["cagr"] - b["cagr"]) if pd.notna(s["cagr"]) and pd.notna(b["cagr"]) else np.nan
    out["d_mdd"] = (s["mdd"] - b["mdd"]) if pd.notna(s["mdd"]) and pd.notna(b["mdd"]) else np.nan
    out["d_calmar_fixed"] = (s["calmar"] - fx["calmar"]) if pd.notna(s["calmar"]) and pd.notna(fx["calmar"]) else np.nan
    out["avg_pos"] = avg
    out["turn"] = float(p_s.diff().abs().sum() / (n / TD)) if n >= 2 else np.nan
    return out


def load_bars(code: str) -> pd.DataFrame | None:
    p = CACHE / f"{code}.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"]).sort_values("date").drop_duplicates("date")
    df = df[df["close"].notna() & (df["close"] > 0)].reset_index(drop=True)
    if len(df):
        df = df[df["date"] <= pd.Timestamp(TEST_END)].reset_index(drop=True)
    return df if len(df) else None


def classify_exclude(row, df, ev_n, ma200_valid, avg_amt) -> str:
    if row["category"] == "货币":
        return "货币ETF"
    if df is None or len(df) < MIN_BARS_STATS:
        return "拉数失败或有效日线不足60"
    if ev_n < MIN_BARS_CROSS or ma200_valid < MIN_MA200_VALID:
        return "短历史(评估窗<250日或MA200有效日<60)"
    if pd.isna(avg_amt) or avg_amt < MIN_AVG_AMOUNT:
        return "流动性不足(近60日均额<500万元)"
    return ""


def _agg(df: pd.DataFrame, split: str) -> pd.Series:
    dcol, mcol, ccol = f"{split}_d_calmar", f"{split}_d_mdd", f"{split}_d_cagr"
    sub = df[df[f"{split}_n"] >= MIN_BARS_STATS]
    if sub.empty:
        return pd.Series(dict(
            n=0, med_d_calmar=np.nan, frac_d_calmar_pos=np.nan,
            med_d_mdd=np.nan, frac_mdd_improved=np.nan,
            med_d_cagr=np.nan, med_calmar=np.nan, med_bh_calmar=np.nan,
            med_cagr=np.nan, med_bh_cagr=np.nan, med_mdd=np.nan,
            med_bh_mdd=np.nan, med_avg_pos=np.nan, med_turn=np.nan,
            med_d_calmar_fixed=np.nan, med_sharpe=np.nan,
        ))
    return pd.Series(dict(
        n=len(sub),
        med_d_calmar=sub[dcol].median(),
        frac_d_calmar_pos=float((sub[dcol] > 0).mean()),
        med_d_mdd=sub[mcol].median(),
        frac_mdd_improved=float((sub[mcol] > 0).mean()),
        med_d_cagr=sub[ccol].median(),
        med_calmar=sub[f"{split}_calmar"].median(),
        med_bh_calmar=sub[f"{split}_bh_calmar"].median(),
        med_cagr=sub[f"{split}_cagr"].median(),
        med_bh_cagr=sub[f"{split}_bh_cagr"].median(),
        med_mdd=sub[f"{split}_mdd"].median(),
        med_bh_mdd=sub[f"{split}_bh_mdd"].median(),
        med_avg_pos=sub[f"{split}_avg_pos"].median(),
        med_turn=sub[f"{split}_turn"].median(),
        med_d_calmar_fixed=sub[f"{split}_d_calmar_fixed"].median(),
        med_sharpe=sub[f"{split}_sharpe"].median(),
    ))


def focus_hit(focus: str, theme: str) -> bool:
    parts = [x.strip() for x in str(focus or "").split(",") if x.strip()]
    return theme in parts


def comparison_block(a: pd.DataFrame, b: pd.DataFrame, split: str) -> dict:
    """a, b 已按 tx_code 对齐的截面子集（同一批名字）。取有足够样本的名字。"""
    ok = (a[f"{split}_n"] >= MIN_BARS_STATS) & (b[f"{split}_n"] >= MIN_BARS_STATS)
    aa, bb = a.loc[ok], b.loc[ok]
    n = int(ok.sum())
    if n == 0:
        return dict(n=0)

    def med(s):
        return float(s.median()) if len(s) else np.nan

    def frac(s):
        return float((s > 0).mean()) if len(s) else np.nan

    dcagr_ba = bb[f"{split}_cagr"] - aa[f"{split}_cagr"]
    dmdd_ba = bb[f"{split}_mdd"] - aa[f"{split}_mdd"]
    dcal_ba = bb[f"{split}_calmar"] - aa[f"{split}_calmar"]
    dpos_ba = bb[f"{split}_avg_pos"] - aa[f"{split}_avg_pos"]
    return dict(
        n=n,
        med_cagr_bh=med(aa[f"{split}_bh_cagr"]),
        med_cagr_a=med(aa[f"{split}_cagr"]),
        med_cagr_b=med(bb[f"{split}_cagr"]),
        med_mdd_bh=med(aa[f"{split}_bh_mdd"]),
        med_mdd_a=med(aa[f"{split}_mdd"]),
        med_mdd_b=med(bb[f"{split}_mdd"]),
        med_calmar_bh=med(aa[f"{split}_bh_calmar"]),
        med_calmar_a=med(aa[f"{split}_calmar"]),
        med_calmar_b=med(bb[f"{split}_calmar"]),
        med_avg_pos_a=med(aa[f"{split}_avg_pos"]),
        med_avg_pos_b=med(bb[f"{split}_avg_pos"]),
        med_turn_a=med(aa[f"{split}_turn"]),
        med_turn_b=med(bb[f"{split}_turn"]),
        med_d_cagr_a_vs_bh=med(aa[f"{split}_d_cagr"]),
        med_d_cagr_b_vs_bh=med(bb[f"{split}_d_cagr"]),
        med_d_mdd_a_vs_bh=med(aa[f"{split}_d_mdd"]),
        med_d_mdd_b_vs_bh=med(bb[f"{split}_d_mdd"]),
        med_d_calmar_a_vs_bh=med(aa[f"{split}_d_calmar"]),
        med_d_calmar_b_vs_bh=med(bb[f"{split}_d_calmar"]),
        frac_d_calmar_a_pos=frac(aa[f"{split}_d_calmar"]),
        frac_d_calmar_b_pos=frac(bb[f"{split}_d_calmar"]),
        frac_mdd_impr_a=frac(aa[f"{split}_d_mdd"]),
        frac_mdd_impr_b=frac(bb[f"{split}_d_mdd"]),
        med_d_cagr_b_vs_a=med(dcagr_ba),
        med_d_mdd_b_vs_a=med(dmdd_ba),
        med_d_calmar_b_vs_a=med(dcal_ba),
        med_d_avg_pos_b_vs_a=med(dpos_ba),
        frac_cagr_b_gt_a=frac(dcagr_ba),
        frac_mdd_b_gt_a=frac(dmdd_ba),  # B 回撤幅度小于 A（d_mdd>0）
        med_d_calmar_fixed_a=med(aa[f"{split}_d_calmar_fixed"]),
        med_d_calmar_fixed_b=med(bb[f"{split}_d_calmar_fixed"]),
    )


def build_comparisons(res: pd.DataFrame) -> pd.DataFrame:
    cs = res[res["in_cross_section"] == True]  # noqa: E712
    a_all = cs[cs.rule == "baseline"].set_index("tx_code")
    b_all = cs[cs.rule == "layered"].set_index("tx_code")
    # align
    common = a_all.index.intersection(b_all.index)
    a_all, b_all = a_all.loc[common], b_all.loc[common]
    rows = []
    for split in ("test", "train", "all"):
        rows.append(dict(split=split, group="ALL", kind="截面", **comparison_block(a_all, b_all, split)))
        for cat, ga in a_all.groupby("category"):
            gb = b_all.loc[ga.index]
            rows.append(dict(split=split, group=f"大类:{cat}", kind="大类", **comparison_block(ga, gb, split)))
        for sec, ga in a_all.groupby("sector"):
            gb = b_all.loc[ga.index]
            rows.append(dict(split=split, group=f"板块:{sec}", kind="板块", **comparison_block(ga, gb, split)))
        for lane, ga in a_all.groupby("lane"):
            gb = b_all.loc[ga.index]
            rows.append(dict(split=split, group=f"分组:{lane}", kind="快慢组", **comparison_block(ga, gb, split)))
        # focus themes: cross-section only
        for theme in FOCUS_THEMES:
            mask = a_all["focus"].fillna("").map(lambda s, t=theme: focus_hit(s, t))
            if not mask.any():
                rows.append(dict(split=split, group=f"焦点:{theme}", kind="焦点", n=0))
                continue
            ga, gb = a_all.loc[mask], b_all.loc[mask]
            rows.append(dict(split=split, group=f"焦点:{theme}", kind="焦点", **comparison_block(ga, gb, split)))
    return pd.DataFrame(rows)


def self_check_rules() -> None:
    """合成路径上核对确认窗与开关初始化，避免实现偏离预登记。"""
    idx = pd.date_range("2020-01-01", periods=10, freq="B")
    cand = pd.Series(["UP", "UP", "SIDE", "SIDE", "SIDE", "DOWN", "DOWN", "DOWN", "DOWN", "UP"], index=idx)
    st = confirm_state(cand, 3)
    expect = ["UP", "UP", "UP", "UP", "SIDE", "SIDE", "SIDE", "DOWN", "DOWN", "DOWN"]
    got = list(st)
    assert got == expect, f"confirm_state 不符: {got}"

    # switch to SIDE on day 5 (0-index 4); mom20 = -0.02 should init to 0.5
    mom = pd.Series([-0.02] * 10, index=idx)
    mom.iloc[8] = 0.005  # still in buffer, keep
    mom.iloc[9] = 0.02
    # states as above; day 5-7 SIDE then DOWN
    g = g1_active(st, mom)
    # UP days: 1.0; SIDE starts at day 5 with mom<0 -> 0.5; DOWN starts day 8 mom<0 -> 0.0
    # day 9 (index 8) DOWN mom in buffer keep 0.0; last day state still DOWN (UP 未满 3 日)
    # 但 mom>1%，下行开关恢复为 1.0
    assert list(np.round(g, 4)) == [1, 1, 1, 1, 0.5, 0.5, 0.5, 0.0, 0.0, 1.0], list(g)

    # buffer init on switch: mom in (0, 1%] should start at 1.0
    st2 = pd.Series(["UP", "SIDE", "SIDE", "SIDE"], index=idx[:4])
    mom2 = pd.Series([0.0, 0.005, -0.01, 0.02], index=idx[:4])
    g2 = g1_active(st2, mom2)
    # day1 UP 1; day2 switch SIDE mom=0.005 -> init 1; day3 mom<0 -> 0.5; day4 mom>1% -> 1
    assert list(np.round(g2, 4)) == [1.0, 1.0, 0.5, 1.0], list(g2)
    log("规则自检通过")


def fmt_pct(x, sgn=False, nd=2):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "NA"
    return f"{x*100:+.{nd}f}%" if sgn else f"{x*100:.{nd}f}%"


def fmt_num(x, sgn=False, nd=3):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "NA"
    return f"{x:+.{nd}f}" if sgn else f"{x:.{nd}f}"


def write_report(res: pd.DataFrame, cmp: pd.DataFrame, excl: pd.DataFrame, snap: dict) -> None:
    lines = []
    lines.append("# 单支 ETF 分层仓位规则 v1 回测")
    lines.append("")
    lines.append(f"- 快照日：{snap.get('snapshot_date')}（Asia/Shanghai 日历下的行情末日）")
    lines.append("- 成本：单边 5bp；执行：信号日收盘决策、次日收盘成交（delay=1）；只做多、不加杠杆")
    lines.append(f"- 窗口：指标从 {EVAL_START}（或上市后）起；训练段至 {TRAIN_END} 只作背景；测试段 {TEST_START} 至 {TEST_END}。2025 及以后在本仓库已用过，不称为新鲜留出。")
    lines.append(f"- 全集 {snap['universe_n']} 只；货币 {snap['money_market_n']} 只不回测；截面主评估 {snap['cross_section_n']} 只。")
    lines.append(f"- 慢速组（债券 / 银行 / 风格-红利/低波）{snap.get('slow_n')} 只；主动组 {snap.get('active_n')} 只。分类用已有 category/sector 标签，不按历史收益挑选。")
    lines.append("- 对照臂：A = 旧定稿 G1×volcap(15%,下限0.4)×taper；B = 本分层规则 v1。满仓为 BH。不开网格，不把仓位乘回 S2+VT15。")
    lines.append("- 预登记：本目录 `PREREG.md`。本报告不是投资建议。")
    if snap.get("git_commit"):
        lines.append(f"- git commit：`{snap['git_commit']}`")
    lines.append("")

    lines.append("## 过滤")
    if len(excl):
        vc = excl["exclude"].value_counts()
        lines.append("剔除原因（只数，含仅不进截面但仍可能附录评估的标的）：")
        for k, v in vc.items():
            lines.append(f"- {k}: {int(v)}")
        miss = int((excl["exclude"].astype(str).str.contains("拉数失败")).sum()) if len(excl) else 0
        if snap.get("missing_cache_n"):
            lines.append(f"- 其中缓存文件缺失（本次未重拉、记入剔除）：{snap['missing_cache_n']} 只，详见 exclusions.csv 中 pull_status=missing。")
        elif miss:
            lines.append(f"- 「拉数失败或有效日线不足60」{miss} 只（上次评估已说明非货币全部拉到行情，此处同一口径，多为上市不足 60 个交易日）。")
    lines.append("")

    def pick(split, group):
        sub = cmp[(cmp["split"] == split) & (cmp["group"] == group)]
        return sub.iloc[0].to_dict() if len(sub) else None

    def block(split, title):
        lines.append(f"### {title}")
        r = pick(split, "ALL")
        if not r or not r.get("n"):
            lines.append("无数据")
            lines.append("")
            return
        lines.append(
            f"截面 n={int(r['n'])}。"
            f"中位年化 BH {fmt_pct(r['med_cagr_bh'])} / A {fmt_pct(r['med_cagr_a'])} / B {fmt_pct(r['med_cagr_b'])}；"
            f"中位最大回撤 BH {fmt_pct(r['med_mdd_bh'])} / A {fmt_pct(r['med_mdd_a'])} / B {fmt_pct(r['med_mdd_b'])}；"
            f"中位卡玛 BH {fmt_num(r['med_calmar_bh'])} / A {fmt_num(r['med_calmar_a'])} / B {fmt_num(r['med_calmar_b'])}；"
            f"中位平均仓位 A {fmt_num(r['med_avg_pos_a'], nd=2)} / B {fmt_num(r['med_avg_pos_b'], nd=2)}；"
            f"中位年换手 A {fmt_num(r['med_turn_a'], nd=1)} / B {fmt_num(r['med_turn_b'], nd=1)}。"
        )
        lines.append(
            f"相对满仓：A 中位 d_cagr={fmt_pct(r['med_d_cagr_a_vs_bh'], sgn=True)}，"
            f"d_mdd={fmt_num(r['med_d_mdd_a_vs_bh'], sgn=True)}（回撤改善 {r['frac_mdd_impr_a']:.0%}），"
            f"d_calmar={fmt_num(r['med_d_calmar_a_vs_bh'], sgn=True)}（为正 {r['frac_d_calmar_a_pos']:.0%}）；"
            f"B 中位 d_cagr={fmt_pct(r['med_d_cagr_b_vs_bh'], sgn=True)}，"
            f"d_mdd={fmt_num(r['med_d_mdd_b_vs_bh'], sgn=True)}（回撤改善 {r['frac_mdd_impr_b']:.0%}），"
            f"d_calmar={fmt_num(r['med_d_calmar_b_vs_bh'], sgn=True)}（为正 {r['frac_d_calmar_b_pos']:.0%}）。"
        )
        lines.append(
            f"B 相对 A（先按只计算差值再取中位）："
            f"d_cagr={fmt_pct(r['med_d_cagr_b_vs_a'], sgn=True)}（B 年化更高 {r['frac_cagr_b_gt_a']:.0%}），"
            f"d_mdd={fmt_num(r['med_d_mdd_b_vs_a'], sgn=True)}（B 回撤更浅 {r['frac_mdd_b_gt_a']:.0%}），"
            f"d_calmar={fmt_num(r['med_d_calmar_b_vs_a'], sgn=True)}，"
            f"平均仓位差 {fmt_num(r['med_d_avg_pos_b_vs_a'], nd=2, sgn=True)}。"
        )
        lines.append("")
        lines.append(
            "| 分组 | n | 中位年化 BH/A/B | 中位MDD BH/A/B | 中位卡玛 BH/A/B | "
            "B vs BH d_cagr | B vs BH d_mdd | B vs A d_cagr | B vs A d_mdd | 仓位 A/B |"
        )
        lines.append("|---|---:|---|---|---|---:|---:|---:|---:|---|")

        def row_line(lab, rr):
            if not rr or not rr.get("n"):
                lines.append(f"| {lab} | 0 |  |  |  |  |  |  |  |  |")
                return
            lines.append(
                f"| {lab} | {int(rr['n'])} | "
                f"{fmt_pct(rr['med_cagr_bh'])} / {fmt_pct(rr['med_cagr_a'])} / {fmt_pct(rr['med_cagr_b'])} | "
                f"{fmt_pct(rr['med_mdd_bh'])} / {fmt_pct(rr['med_mdd_a'])} / {fmt_pct(rr['med_mdd_b'])} | "
                f"{fmt_num(rr['med_calmar_bh'])} / {fmt_num(rr['med_calmar_a'])} / {fmt_num(rr['med_calmar_b'])} | "
                f"{fmt_pct(rr['med_d_cagr_b_vs_bh'], sgn=True)} | {fmt_num(rr['med_d_mdd_b_vs_bh'], sgn=True)} | "
                f"{fmt_pct(rr['med_d_cagr_b_vs_a'], sgn=True)} | {fmt_num(rr['med_d_mdd_b_vs_a'], sgn=True)} | "
                f"{fmt_num(rr['med_avg_pos_a'], nd=2)} / {fmt_num(rr['med_avg_pos_b'], nd=2)} |"
            )

        row_line("截面ALL", r)
        # groups
        want = []
        for _, row in cmp[cmp["split"] == split].iterrows():
            if row["group"] == "ALL":
                continue
            if row["kind"] == "大类":
                want.append(row)
            elif row["kind"] == "快慢组":
                want.append(row)
            elif row["kind"] == "板块" and (row["group"].replace("板块:", "") in KEY_SECTORS or (row.get("n") or 0) >= 8):
                want.append(row)
            elif row["kind"] == "焦点":
                want.append(row)
        # order: 快慢组, 大类, 指定板块, 焦点, other sectors
        order = {"快慢组": 0, "大类": 1, "板块": 2, "焦点": 3}
        want = sorted(want, key=lambda x: (order.get(x["kind"], 9), -x.get("n", 0) if pd.notna(x.get("n")) else 0, x["group"]))
        seen = set()
        for row in want:
            if row["group"] in seen:
                continue
            seen.add(row["group"])
            row_line(row["group"], row.to_dict())
        lines.append("")

    lines.append("## 测试段（主看）")
    block("test", f"测试段 {TEST_START} 至 {TEST_END}")
    lines.append("## 训练段（背景）")
    block("train", f"训练段至 {TRAIN_END}")
    lines.append("## 全样本")
    block("all", f"全样本（{EVAL_START} 起或上市后，至 {TEST_END}）")

    # focus ticker table
    foc = res[res["focus"].fillna("").astype(str) != ""].copy()
    lines.append("## 焦点主题个股（测试段）")
    if len(foc):
        for theme in FOCUS_THEMES + ["传媒-相关游戏"]:
            hit = foc[foc["focus"].fillna("").map(lambda s, t=theme: focus_hit(s, t))]
            lines.append(f"### {theme}")
            if hit.empty:
                lines.append("无匹配。")
                lines.append("")
                continue
            one = hit[hit.rule == "baseline"].sort_values(
                ["in_cross_section", "avg_amt_60"], ascending=[False, False]
            )
            lay = hit[hit.rule == "layered"].set_index("tx_code")
            n_cs = int(one["in_cross_section"].sum())
            lines.append(f"匹配 {one['tx_code'].nunique()} 只；进入截面 {n_cs} 只。")
            lines.append(
                "| 代码 | 名称 | 截面 | 分组 | test年化 BH/A/B | test MDD BH/A/B | "
                "A d_cagr | B d_cagr | A d_mdd | B d_mdd | 仓位 A/B |"
            )
            lines.append("|---|---|---|---|---|---|---:|---:|---:|---:|---|")
            for r in one.itertuples():
                b = lay.loc[r.tx_code] if r.tx_code in lay.index else None
                if b is None:
                    continue
                lines.append(
                    f"| {r.tx_code} | {r.name} | {'是' if r.in_cross_section else '否'} | {r.lane} | "
                    f"{fmt_pct(r.test_bh_cagr)} / {fmt_pct(r.test_cagr)} / {fmt_pct(b['test_cagr'])} | "
                    f"{fmt_pct(r.test_bh_mdd)} / {fmt_pct(r.test_mdd)} / {fmt_pct(b['test_mdd'])} | "
                    f"{fmt_pct(r.test_d_cagr, sgn=True)} | {fmt_pct(b['test_d_cagr'], sgn=True)} | "
                    f"{fmt_num(r.test_d_mdd, sgn=True)} | {fmt_num(b['test_d_mdd'], sgn=True)} | "
                    f"{fmt_num(r.test_avg_pos, nd=2)} / {fmt_num(b['test_avg_pos'], nd=2)} |"
                )
            lines.append("")
    else:
        lines.append("无焦点结果。")
        lines.append("")

    # pre-registered judgment
    lines.append("## 对照预登记成功标准（只看测试段截面）")
    lines.append("")

    def judge_focus(theme_label, group_name, kind_note=""):
        r = pick("test", group_name)
        lines.append(f"### {theme_label}")
        if kind_note:
            lines.append(kind_note)
        if not r or not r.get("n"):
            lines.append("截面无足够样本，无法按预登记标准判定。")
            lines.append("")
            return r
        lines.append(
            f"截面 n={int(r['n'])}。"
            f"中位年化 BH {fmt_pct(r['med_cagr_bh'])}、A {fmt_pct(r['med_cagr_a'])}、B {fmt_pct(r['med_cagr_b'])}；"
            f"中位 d_cagr vs BH：A {fmt_pct(r['med_d_cagr_a_vs_bh'], sgn=True)}、B {fmt_pct(r['med_d_cagr_b_vs_bh'], sgn=True)}；"
            f"B 相对 A 中位年化差 {fmt_pct(r['med_d_cagr_b_vs_a'], sgn=True)}。"
            f"中位 MDD BH {fmt_pct(r['med_mdd_bh'])}、A {fmt_pct(r['med_mdd_a'])}、B {fmt_pct(r['med_mdd_b'])}；"
            f"B vs BH 中位 d_mdd={fmt_num(r['med_d_mdd_b_vs_bh'], sgn=True)}（回撤改善 {r['frac_mdd_impr_b']:.0%}）；"
            f"平均仓位 A {fmt_num(r['med_avg_pos_a'], nd=2)}、B {fmt_num(r['med_avg_pos_b'], nd=2)}。"
        )
        return r

    # 1 chips / equipment
    lines.append("预登记：科创芯片、半导体设备相对臂 A，测试段中位年化损失应明显收窄；相对满仓，回撤仍应改善（允许改善幅度小于臂 A）。")
    lines.append("")
    r_chip = judge_focus("科创芯片（焦点主题，截面）", "焦点:科创芯片")
    r_eq = judge_focus("半导体设备（焦点主题，截面）", "焦点:半导体设备")
    r_sec = judge_focus("芯片/半导体（板块，截面）", "板块:芯片/半导体")
    r_eqs = judge_focus("半导体设备（板块，截面）", "板块:半导体设备")

    def cagr_narrow(r):
        if not r or not r.get("n"):
            return "无法判定"
        # drag vs BH: A is typically more negative. "明显收窄" if B's d_cagr vs BH is higher than A's by a clear margin.
        # use per-name median (B-A). Also require B d_cagr vs BH less severe if A was a drag.
        d = r["med_d_cagr_b_vs_a"]
        a_drag = r["med_d_cagr_a_vs_bh"]
        b_drag = r["med_d_cagr_b_vs_bh"]
        # "明显" : at least about 3pp recovery in median per-name CAGR vs A, or drag cut by roughly half if A drag was large
        clear = (pd.notna(d) and d >= 0.03) or (
            pd.notna(a_drag) and pd.notna(b_drag) and a_drag < -0.03 and (b_drag - a_drag) >= 0.03
        )
        mild = pd.notna(d) and d > 0.005
        if clear:
            return "通过（年化损失相对 A 明显收窄）"
        if mild:
            return "部分通过（年化相对 A 有所回升，但幅度算不上明显）"
        if pd.notna(d) and d <= 0:
            return "未通过（年化相对 A 未收窄）"
        return "无法判定"

    def dd_still_better(r):
        if not r or not r.get("n"):
            return "无法判定"
        # vs BH, drawdowns should still improve: median d_mdd > 0, and not almost back to BH
        dm = r["med_d_mdd_b_vs_bh"]
        frac = r["frac_mdd_impr_b"]
        mdd_b, mdd_bh = r["med_mdd_b"], r["med_mdd_bh"]
        if pd.isna(dm) or pd.isna(mdd_b) or pd.isna(mdd_bh):
            return "无法判定"
        # almost back to BH: improvement < 2pp or B mdd within 2pp of BH
        almost = (dm < 0.02) or (abs(mdd_b - mdd_bh) < 0.02)
        if dm > 0 and frac >= 0.5 and not almost:
            return "通过（相对满仓回撤仍改善）"
        if dm > 0 and almost:
            return "未通过（回撤改善过小，接近满仓）"
        if dm <= 0:
            return "未通过（相对满仓回撤未改善）"
        return "无法判定"

    j1_parts = []
    for lab, r in (("科创芯片焦点", r_chip), ("半导体设备焦点", r_eq)):
        cn, dd = cagr_narrow(r), dd_still_better(r)
        j1_parts.append(f"{lab}：年化 {cn}；回撤 {dd}")
    lines.append("判定：" + "；".join(j1_parts) + "。板块口径数字见上，不另改结论规则。")
    lines.append("")

    # 2 banks / dividend
    lines.append("预登记：银行、红利/低波应接近满仓，不再出现大约 10 个百分点量级的年化拖累。")
    lines.append("")
    r_bank_f = judge_focus("银行（焦点主题，截面）", "焦点:银行")
    r_bank_s = judge_focus("银行（板块，截面）", "板块:银行")
    r_div = judge_focus("风格-红利/低波（板块，截面）", "板块:风格-红利/低波")

    def close_to_bh(r):
        if not r or not r.get("n"):
            return "无法判定"
        drag = r["med_d_cagr_b_vs_bh"]
        pos = r["med_avg_pos_b"]
        # no ~10pp drag: |drag| clearly below ~10pp, ideally within a few pp; pos close to 1
        if pd.isna(drag):
            return "无法判定"
        no_10pp = drag > -0.05  # clearly not ~10pp; allow small drag
        mild_10 = -0.08 <= drag <= -0.05
        still_10 = drag <= -0.08
        pos_close = pd.notna(pos) and pos >= 0.90
        pos_mid = pd.notna(pos) and pos >= 0.80
        if no_10pp and pos_close:
            return "通过（接近满仓，年化拖累不再是约 10 个百分点量级）"
        if (no_10pp and pos_mid) or (mild_10 and pos_close):
            return "部分通过（拖累已明显小于约 10 个百分点，但与满仓仍有距离）"
        if still_10:
            return "未通过（仍有约 10 个百分点量级年化拖累）"
        return "部分通过或未通过（见具体数字）"

    j2 = []
    for lab, r in (("银行焦点", r_bank_f), ("银行板块", r_bank_s), ("红利/低波板块", r_div)):
        j2.append(f"{lab}：{close_to_bh(r)}")
    lines.append("判定：" + "；".join(j2) + "。")
    lines.append("")

    # 3 market-wide risk control
    lines.append("预登记：全市场截面若年化明显回升，但中位回撤回到接近满仓，视为没有达到「分层风控」的目标。")
    r_all = pick("test", "ALL")
    if r_all and r_all.get("n"):
        lines.append(
            f"截面 n={int(r_all['n'])}。"
            f"中位年化 BH {fmt_pct(r_all['med_cagr_bh'])}、A {fmt_pct(r_all['med_cagr_a'])}、B {fmt_pct(r_all['med_cagr_b'])}；"
            f"B vs A 中位 d_cagr={fmt_pct(r_all['med_d_cagr_b_vs_a'], sgn=True)}；"
            f"B vs BH 中位 d_cagr={fmt_pct(r_all['med_d_cagr_b_vs_bh'], sgn=True)}。"
            f"中位 MDD BH {fmt_pct(r_all['med_mdd_bh'])}、A {fmt_pct(r_all['med_mdd_a'])}、B {fmt_pct(r_all['med_mdd_b'])}；"
            f"B vs BH 中位 d_mdd={fmt_num(r_all['med_d_mdd_b_vs_bh'], sgn=True)}（回撤改善 {r_all['frac_mdd_impr_b']:.0%}）。"
            f"中位仓位 A {fmt_num(r_all['med_avg_pos_a'], nd=2)}、B {fmt_num(r_all['med_avg_pos_b'], nd=2)}。"
        )
        cagr_up = pd.notna(r_all["med_d_cagr_b_vs_a"]) and r_all["med_d_cagr_b_vs_a"] >= 0.01
        dm = r_all["med_d_mdd_b_vs_bh"]
        mdd_b, mdd_bh, mdd_a = r_all["med_mdd_b"], r_all["med_mdd_bh"], r_all["med_mdd_a"]
        almost_bh = pd.notna(dm) and (dm < 0.02 or (pd.notna(mdd_b) and pd.notna(mdd_bh) and abs(mdd_b - mdd_bh) < 0.02))
        still_better = pd.notna(dm) and dm >= 0.05 and not almost_bh
        # also compare how much of A's DD improvement remains
        if pd.notna(mdd_a) and pd.notna(mdd_bh) and pd.notna(mdd_b) and (mdd_a - mdd_bh) != 0:
            kept = (mdd_b - mdd_bh) / (mdd_a - mdd_bh) if (mdd_a - mdd_bh) > 0 else np.nan
        else:
            kept = np.nan
        if pd.notna(kept):
            lines.append(f"B 相对满仓的中位回撤改善，约为臂 A 改善幅度的 {kept:.0%}。")
        if cagr_up and almost_bh:
            j3 = "未通过（年化相对 A 回升，但中位回撤接近满仓，未达到分层风控目标）"
        elif almost_bh:
            j3 = "未通过（中位回撤已接近满仓）"
        else:
            j3 = "未触发该失败条件（中位回撤未回到接近满仓）。若年化也未相对 A 回升，并不表示全市场分层优于旧定稿，需同时看卡玛与回撤改善幅度"
        lines.append(f"判定：{j3}。")
    else:
        lines.append("截面无数据，无法判定。")
    lines.append("")
    lines.append("以上判定只对照预登记文字，回测后不改规则、不加网格。本结果不是投资建议。")
    lines.append("")

    if snap.get("baseline_reconcile"):
        lines.append("## 实现核对")
        br = snap["baseline_reconcile"]
        lines.append(
            f"臂 A（旧定稿）与上次全市场评估 `results_long.csv` 中 baseline 对照："
            f"截面重叠 {br.get('n_overlap')} 只；"
            f"测试段年化最大绝对差 {br.get('max_abs_test_cagr')}，"
            f"测试段 MDD 最大绝对差 {br.get('max_abs_test_mdd')}，"
            f"测试段平均仓位最大绝对差 {br.get('max_abs_test_avg_pos')}。"
            f"{'一致' if br.get('ok') else '存在偏差，见 snapshot.json'}。"
        )
        lines.append("")

    lines.append("## 实现说明")
    lines.append("- 主动组候选状态在 MA200 或 slope60 缺失时记为震荡，因为预登记的上/下行都要求斜率可计算；这主要影响年线刚满 200 日、斜率尚未满 60 日的一段，测试段绝大多数标的不受影响。")
    lines.append("- 状态确认：同一候选连续 3 个交易日后切换，不足则保持原状态；序列首日直接取当日候选。")
    lines.append("- 动量开关在状态切换当日按当时 mom20 相对 0% / 1% 初始化（跌破 0 为触发档，其余含缓冲带为已恢复），之后保持滞回，不每日重判。")
    lines.append("- 本次未重拉全市场行情，复用 `data/cache_ohlc_full/`。")
    lines.append("")

    lines.append("## 文件")
    for fn in [
        "PREREG.md", "layered_v1_backtest.py", "results_long.csv",
        "summary_by_group.csv", "comparison_test.csv", "comparison_all_splits.csv",
        "focus_results.csv", "exclusions.csv", "live_signal.csv",
        "snapshot.json", "REPORT.md",
    ]:
        lines.append(f"- `runs/2026-11-14_layered_v1/{fn}`")
    (RUN / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    log(f"写报告 {RUN / 'REPORT.md'}")


def cmd_eval() -> None:
    self_check_rules()
    uni = pd.read_csv(PRIOR / "universe_all.csv")
    meta = pd.read_csv(PRIOR / "pull_meta.csv") if (PRIOR / "pull_meta.csv").exists() else pd.DataFrame()
    pull_status = dict(zip(meta["tx_code"], meta["status"])) if len(meta) else {}

    # copy reference maps into this run for completeness
    for fn in ("universe_all.csv", "focus_map.csv"):
        src = PRIOR / fn
        if src.exists() and not (RUN / fn).exists():
            shutil.copy2(src, RUN / fn)

    rows, excl, live = [], [], []
    missing_cache = 0
    n_uni = len(uni)
    for i, row in uni.iterrows():
        code = row["tx_code"]
        if (i + 1) % 100 == 0:
            log(f"评估 {i + 1}/{n_uni}")
        if row["category"] == "货币":
            excl.append({**row.to_dict(), "exclude": "货币ETF", "ev_n": 0, "avg_amt_60": np.nan,
                         "first": "", "last": "", "ma200_valid": 0, "lane": ""})
            continue
        df = load_bars(code)
        if df is None:
            st = pull_status.get(code, "missing")
            if not (CACHE / f"{code}.csv").exists():
                missing_cache += 1
                st = "missing"
            excl.append({**row.to_dict(), "exclude": "拉数失败或有效日线不足60",
                         "ev_n": 0, "avg_amt_60": np.nan, "first": "", "last": "",
                         "ma200_valid": 0, "pull_status": st, "lane": ""})
            continue
        first, last = str(df["date"].iloc[0].date()), str(df["date"].iloc[-1].date())
        ev = (df["date"] >= pd.Timestamp(EVAL_START)) & (df["date"] <= pd.Timestamp(TEST_END))
        ev_n = int(ev.sum())
        ev_df = df[ev] if ev.any() else df
        tail = ev_df.tail(60)
        avg_amt = float(tail["amount"].mean()) if "amount" in tail.columns and len(tail) else np.nan

        F = slim_factors(df)
        ev_dates = set(df.loc[ev, "date"])
        ma200_valid = int(F["ma200"].loc[F["close"].index.isin(ev_dates)].notna().sum())

        reason = classify_exclude(row, df, ev_n, ma200_valid, avg_amt)
        lane = "slow" if is_slow_group(row["category"], row["sector"]) else "active"
        rec_common = dict(
            tx_code=code, name=row["name"], index_name=row["index_name"],
            category=row["category"], sector=row["sector"], focus=row["focus"],
            first=first, last=last, ev_n=ev_n, ma200_valid=ma200_valid,
            avg_amt_60=avg_amt, listed_after_train=first > TRAIN_END,
            short_flag=bool(reason), exclude=reason or "", lane=lane,
        )
        if reason:
            do_eval = (bool(row["focus"]) or reason.startswith("短历史") or reason.startswith("流动性")) and ev_n >= MIN_BARS_STATS
            if not do_eval:
                excl.append(rec_common)
                continue
            rec_common["in_cross_section"] = False
        else:
            rec_common["in_cross_section"] = True

        pos_a, T = policy_baseline(F)
        pos_b, info = policy_layered(F, slow=(lane == "slow"))
        ret = F["ret"]
        dates = ret.index
        masks = split_masks(pd.Series(dates, index=dates))
        last_i = dates[-1]
        mom = F["mom20"].loc[last_i]
        vol = F["vol60"].loc[last_i]
        sl = F["slope60"].loc[last_i]
        ma = F["ma200"].loc[last_i]
        live.append(dict(
            tx_code=code, name=row["name"], category=row["category"], sector=row["sector"],
            focus=row["focus"], lane=lane, date=str(pd.Timestamp(last_i).date()),
            T=int(T.loc[last_i]),
            candidate=str(info["candidate"].loc[last_i]),
            state=str(info["state"].loc[last_i]),
            close=float(F["close"].loc[last_i]),
            ma200=float(ma) if pd.notna(ma) else np.nan,
            slope60=float(sl) if pd.notna(sl) else np.nan,
            mom20=float(mom) if pd.notna(mom) else np.nan,
            vol60=float(vol) if pd.notna(vol) else np.nan,
            state_coef=float(info["state_coef"].loc[last_i]),
            vol_scale=float(info["vol_scale"].loc[last_i]),
            g1=float(info["g1"].loc[last_i]),
            pos_baseline=float(pos_a.loc[last_i]),
            pos_layered=float(pos_b.loc[last_i]),
            in_cross_section=rec_common["in_cross_section"],
        ))

        for rule, pos in (("baseline", pos_a), ("layered", pos_b)):
            net, p = run_pos(pos, ret, cost=COST)
            rec = dict(rec_common, rule=rule)
            for split, mask in masks.items():
                m = mask.to_numpy() if hasattr(mask, "to_numpy") else np.asarray(mask)
                blk = metrics_block(net, ret, p, m)
                for k, v in blk.items():
                    rec[f"{split}_{k}"] = v
            rows.append(rec)
        if reason:
            excl.append(rec_common)

    res = pd.DataFrame(rows)
    res.to_csv(RUN / "results_long.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(excl).to_csv(RUN / "exclusions.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(live).to_csv(RUN / "live_signal.csv", index=False, encoding="utf-8-sig")

    # summaries
    cs = res[res["in_cross_section"] == True]  # noqa: E712
    sum_rows = []
    for rule in ("baseline", "layered"):
        sub = cs[cs.rule == rule]
        for split in ("train", "test", "all"):
            a = _agg(sub, split)
            sum_rows.append(dict(rule=rule, group="ALL", split=split, **a))
            for cat, g in sub.groupby("category"):
                sum_rows.append(dict(rule=rule, group=f"大类:{cat}", split=split, **_agg(g, split)))
            for sec, g in sub.groupby("sector"):
                sum_rows.append(dict(rule=rule, group=f"板块:{sec}", split=split, **_agg(g, split)))
            for lane, g in sub.groupby("lane"):
                sum_rows.append(dict(rule=rule, group=f"分组:{lane}", split=split, **_agg(g, split)))
    pd.DataFrame(sum_rows).to_csv(RUN / "summary_by_group.csv", index=False, encoding="utf-8-sig")

    foc = res[res["focus"].fillna("").astype(str) != ""].copy()
    foc.to_csv(RUN / "focus_results.csv", index=False, encoding="utf-8-sig")

    cmp = build_comparisons(res)
    cmp.to_csv(RUN / "comparison_all_splits.csv", index=False, encoding="utf-8-sig")
    cmp[cmp.split == "test"].to_csv(RUN / "comparison_test.csv", index=False, encoding="utf-8-sig")

    # reconcile arm A vs prior baseline
    prior = pd.read_csv(PRIOR / "results_long.csv")
    pb = prior[(prior.rule == "baseline") & (prior.in_cross_section == True)].set_index("tx_code")  # noqa: E712
    nb = cs[cs.rule == "baseline"].set_index("tx_code")
    overlap = pb.index.intersection(nb.index)
    rec = dict(n_prior=int(pb.index.nunique()), n_new=int(nb.index.nunique()), n_overlap=int(len(overlap)))
    if len(overlap):
        rec["max_abs_test_cagr"] = float((nb.loc[overlap, "test_cagr"] - pb.loc[overlap, "test_cagr"]).abs().max())
        rec["max_abs_test_mdd"] = float((nb.loc[overlap, "test_mdd"] - pb.loc[overlap, "test_mdd"]).abs().max())
        rec["max_abs_test_avg_pos"] = float((nb.loc[overlap, "test_avg_pos"] - pb.loc[overlap, "test_avg_pos"]).abs().max())
        rec["ok"] = (
            rec["max_abs_test_cagr"] < 1e-12
            and rec["max_abs_test_mdd"] < 1e-12
            and rec["max_abs_test_avg_pos"] < 1e-12
            and rec["n_prior"] == rec["n_new"] == rec["n_overlap"]
        )
    else:
        rec["ok"] = False
    log(f"臂 A 与上次 baseline 核对: {rec}")

    snap = {
        "snapshot_date": (res["last"].max() if len(res) else None),
        "eval_start": EVAL_START, "train_end": TRAIN_END,
        "test_start": TEST_START, "test_end": TEST_END,
        "cost_one_way": COST, "delay": 1,
        "min_bars_cross": MIN_BARS_CROSS, "min_avg_amount": MIN_AVG_AMOUNT,
        "universe_n": int(len(uni)),
        "money_market_n": int((uni.category == "货币").sum()),
        "evaluated_n": int(res["tx_code"].nunique()) if len(res) else 0,
        "cross_section_n": int(cs["tx_code"].nunique()) if len(cs) else 0,
        "slow_n": int(cs.loc[cs.rule == "layered", "lane"].eq("slow").sum()) if len(cs) else 0,
        "active_n": int(cs.loc[cs.rule == "layered", "lane"].eq("active").sum()) if len(cs) else 0,
        "missing_cache_n": int(missing_cache),
        "prereg": "runs/2026-11-14_layered_v1/PREREG.md",
        "cache_path": str(CACHE),
        "script": "work/layered_v1_backtest.py",
        "git_commit": git_commit(),
        "baseline_reconcile": rec,
        "frozen": {
            "confirm_days": CONFIRM_DAYS,
            "slow_t4_pos": SLOW_POS_T4,
            "state_coef": STATE_COEF,
            "vol_target": VOL_TARGET,
            "vol_floor": VOL_FLOOR,
            "g1_side": G1_SIDE_LEVEL,
            "g1_down": G1_DOWN_LEVEL,
            "mom_reduce": MOM_REDUCE,
            "mom_recover": MOM_RECOVER,
            "slope_th": SLOPE_TH,
        },
    }
    (RUN / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(res, cmp, pd.DataFrame(excl), snap)
    log("评估完成")


def main():
    cmd_eval()


if __name__ == "__main__":
    main()
