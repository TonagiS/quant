#!/usr/bin/env python3
"""S4 影子盘（纸面）公共库：实时数据拉取、面板拼接、复用冻结研究代码。
提案：position/frozen_candidates/PROPOSAL_S4_SHADOW.md（2026-11-21 用户批准）。
原则：规则代码只 import 已冻结的研究模块（components_core / prereg_satellite / sat_vt15_control / engine），
本文件只负责"把数据延长到最新交易日"；运行时只改各模块的数据截止日常量 END 与缓存路径，不改任何规则。"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "work"))
sys.path.insert(0, str(ROOT / "position"))

LIVE = ROOT / "data" / "cache_live_s4"          # gitignored（data/cache*/）
BARS = LIVE / "bars"
CC_LIVE = LIVE / "cc"
RC_CACHE = LIVE / "rc"
RESEARCH_END = pd.Timestamp("2026-11-13")       # 研究面板 panel_tr2 的最后一天
PULL_START = "2026-09-01"                       # 拉取窗口起点：与研究面板重叠 ~2.5 个月，用于校验与估计拆分系数
UA = {"User-Agent": "Mozilla/5.0"}
BROAD = ["sh510300", "sh510500", "sz159915", "sh588000", "sh512100"]
SPLIT_JUMP, MAX_GAP_DAYS = 0.30, 30            # 与 long_history_backtest 相同


def log(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)


def sha256(p: Path) -> str:
    """LF 口径：先把 CRLF 换成 LF 再算（Windows 检出与仓库一致，避免 DSH 第 4 项那类换行误报）。"""
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# ------------------------------------------------------------------ 拉取（腾讯 newfqkline，与研究缓存同源）
def _get(u, tries=4):
    last = None
    for i in range(tries):
        try:
            r = requests.get(u, headers=UA, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0 * (i + 1))
    raise last


def tx_pull(code, fq, start=PULL_START, end="2099-12-31"):
    j = _get(f"https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?param={code},day,{start},{end},800,{fq}")
    b = (j.get("data") or {}).get(code) or {}
    k = b.get("qfqday") or b.get("day") or []
    recs = []
    for r in k:
        amt = None
        if len(r) >= 9 and r[8] not in ("", None, {}):
            try:
                amt = float(r[8]) * 1e4
            except (TypeError, ValueError):
                amt = None
        recs.append((r[0], r[2], r[5], amt))
    df = pd.DataFrame(recs, columns=["date", "close", "volume", "amount"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    for c in ("close", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["amount"] = df["amount"].fillna(df["volume"] * df["close"] * 100)
    return df.drop_duplicates("date").sort_values("date").set_index("date")


def tr_returns(q: pd.Series, raw: pd.Series) -> pd.Series:
    """与 long_history_backtest.tr_series 相同的总收益公式（拆分分段 k，r_t = Δqfq_t / (k·raw_{t-1})）。"""
    df = pd.concat({"q": q, "r": raw}, axis=1, sort=True).dropna()
    df = df[df.r > 0]
    rr = df.r.pct_change()
    jump = (rr.abs() > SPLIT_JUMP).fillna(False)
    seg = jump.cumsum()
    dq, dr = df.q.diff(), df.r.diff()
    x = (dq / dr).where((dr.abs() > 0) & ~jump)
    k = x.groupby(seg).transform("median")
    r = dq / (k * df.r.shift(1))
    r[jump] = df.q.pct_change()[jump]
    return r


def pull_one(code):
    try:
        q = tx_pull(code, "qfq")
        raw = tx_pull(code, "")
        if q.empty or raw.empty:
            return code, "empty"
        r = tr_returns(q.close, raw.close)
        out = pd.DataFrame({"raw": raw.close, "qfq": q.close, "amount": raw.amount, "r": r}).dropna(subset=["raw"])
        out.index.name = "date"
        out.to_csv(BARS / f"{code}.csv")
        return code, "ok"
    except Exception as e:  # noqa: BLE001
        return code, f"fail:{type(e).__name__}"


def pull(codes, threads=6):
    BARS.mkdir(parents=True, exist_ok=True)
    res = {}
    with ThreadPoolExecutor(threads) as ex:
        for c, s in ex.map(pull_one, sorted(set(codes))):
            res[c] = s
    bad = {c: s for c, s in res.items() if s != "ok"}
    log(f"pull {len(res)} codes, failed {len(bad)}")
    return res, bad


# ------------------------------------------------------------------ 指数 PE / 国债（C3 的 ERP 乘数）
def update_cc():
    """把 data/cache_components 复制到 live 目录，并尽量把 000300 PE、10 年国债延长到最新；失败则沿用旧值（日志标注）。"""
    src = ROOT / "data" / "cache_components"
    if CC_LIVE.exists():
        shutil.rmtree(CC_LIVE)
    shutil.copytree(src, CC_LIVE)
    notes = []
    try:
        j = _get("https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=000300"
                 f"&startDate={PULL_START.replace('-', '')}&endDate=20991231")
        d = pd.DataFrame(j.get("data") or [])
        new = pd.DataFrame({"date": pd.to_datetime(d.tradeDate), "close": pd.to_numeric(d.close, errors="coerce"),
                            "pe": pd.to_numeric(d.get("peg"), errors="coerce")}).dropna(subset=["close"])
        old = pd.read_csv(CC_LIVE / "000300.csv", parse_dates=["date"])
        m = pd.concat([old[old.date < new.date.min()], new]).drop_duplicates("date", keep="last").sort_values("date")
        m.to_csv(CC_LIVE / "000300.csv", index=False)
        notes.append(f"000300_pe_last={m.date.iloc[-1].date()}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"000300_pe_update_fail:{type(e).__name__}")
    try:
        u = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_WEB_TREASURYYIELD&columns=SOLAR_DATE,EMM00166466"
             "&sortColumns=SOLAR_DATE&sortTypes=-1&pageNumber=1&pageSize=200")
        rows = (_get(u).get("result") or {}).get("data") or []
        d = pd.DataFrame(rows)
        new = pd.DataFrame({"date": pd.to_datetime(d.SOLAR_DATE), "y10": pd.to_numeric(d.EMM00166466, errors="coerce")}).dropna()
        old = pd.read_csv(CC_LIVE / "cgb10y.csv", parse_dates=["date"])
        m = pd.concat([old[old.date < new.date.min()], new]).drop_duplicates("date", keep="last").sort_values("date")
        m.to_csv(CC_LIVE / "cgb10y.csv", index=False)
        notes.append(f"cgb10y_last={m.date.iloc[-1].date()}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"cgb10y_update_fail:{type(e).__name__}")
    return notes


def fred_dgs10():
    p = LIVE / "fred_DGS10.csv"
    try:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2025-01-01", headers=UA, timeout=20)
        r.raise_for_status()
        p.write_text(r.text)
    except Exception:  # noqa: BLE001
        if not p.exists():
            src = ROOT / "data" / "cache_pv2" / "fred_DGS10.csv"
            if src.exists():
                shutil.copy(src, p)
    d = pd.read_csv(p)
    d.columns = ["date", "DGS10"]
    s = pd.to_numeric(d.DGS10, errors="coerce")
    s.index = pd.to_datetime(d.date)
    return s.dropna().sort_index()


# ------------------------------------------------------------------ 面板拼接
def research_panel():
    import tr_ens_data as TD
    return TD.build()


def extend_panel(P, asof=None, cut=None):
    """研究面板（截到 cut，默认 RESEARCH_END=2026-11-13）+ 实时拉取的总收益日收益/成交额（cut 之后的交易日）。
    cut 早于 RESEARCH_END 仅供 replay_check_s4.py 做"截断后用实时数据延长"的复现检验。返回新 P 与重叠期校验表。"""
    cut = pd.Timestamp(cut or RESEARCH_END)
    C = P["close_tr"].loc[:cut]
    A = P["amount"].reindex(columns=P["close_tr"].columns).loc[:cut]
    live = {}
    for f in BARS.glob("*.csv"):
        d = pd.read_csv(f, parse_dates=["date"]).set_index("date")
        live[f.stem] = d
    dates = sorted({t for d in live.values() for t in d.index if t > cut})
    if asof is not None:
        dates = [t for t in dates if t <= pd.Timestamp(asof)]
    new_idx = pd.DatetimeIndex(dates)
    C2 = pd.concat([C, pd.DataFrame(np.nan, index=new_idx, columns=C.columns)])
    A2 = pd.concat([A, pd.DataFrame(np.nan, index=new_idx, columns=C.columns)])
    chk = []
    for code, d in live.items():
        if code not in C.columns:
            continue
        base = C[code].dropna()
        if base.empty or base.index[-1] != cut:
            continue
        r = d["r"].reindex(new_idx)
        C2.loc[new_idx, code] = base.iloc[-1] * (1 + r.fillna(0.0)).cumprod().where(d["raw"].reindex(new_idx).notna())
        A2.loc[new_idx, code] = d["amount"].reindex(new_idx)
        ov = P["close_tr"][code].loc[:RESEARCH_END].pct_change().loc[PULL_START:]
        rv = d["r"].reindex(ov.index)
        m = ov.notna() & rv.notna()
        if m.sum() > 5:
            diff = (ov[m] - rv[m]).abs()
            chk.append({"code": code, "n": int(m.sum()), "max_abs_diff_bp": float(diff.max() * 1e4),
                        "share_within_1bp": float((diff < 1e-4).mean())})
    P2 = dict(P)
    P2["close_tr"], P2["amount"] = C2, A2
    P2["close_naive"] = P["close_naive"].reindex(C2.index)
    return P2, pd.DataFrame(chk)


def patch_modules(P2):
    """让冻结研究模块读到延长后的面板：只改数据截止日与缓存位置。"""
    import components_core as K
    import long_history_backtest as LH
    import pit_pool_backtest as PB
    import pit_pool_data as PD
    import prereg_satellite as PS
    import recombine_core as RC
    end = P2["close_tr"].index[-1]
    for m in (PB, RC, K):
        m.END = end
    PD.END = str(end.date())
    LH.END = str(end.date())
    RC_CACHE.mkdir(parents=True, exist_ok=True)
    pk = RC_CACHE / "base.pkl"
    if pk.exists():
        pk.unlink()
    RC.CACHE = RC_CACHE
    RC.TD.build = lambda force=False: P2
    RC._D.clear()
    K._S.clear()
    K._S["P"] = P2
    K.CC = CC_LIVE
    K.EXTRA = [(c, (kind, CC_LIVE / Path(p).name if Path(p).parent.name == "cache_components" else p, col), e)
               for c, (kind, p, col), e in K.EXTRA]
    PS.G.clear()
    return end


def fingerprint_files():
    files = ["position/live/shadow_s4/run_s4_shadow.py", "position/live/shadow_s4/s4lib.py",
             "work/components_core.py", "work/components_bt.py", "work/prereg_satellite.py", "work/sat_vt15_control.py",
             "work/recombine_core.py", "work/pit_pool_backtest.py", "work/pit_pool_data.py", "work/tr_ens_data.py",
             "work/long_history_backtest.py", "work/fullmkt_single_asset.py", "work/s4_random_null.py",
             "work/tr_ens_portfolio.py", "position/engine.py"]
    return {f: sha256(ROOT / f) for f in files}


def check_fingerprints():
    fz = json.loads((HERE / "FROZEN_SHADOW_S4.json").read_text(encoding="utf-8"))
    now = fingerprint_files()
    bad = {f: (fz["files"].get(f), h) for f, h in now.items() if fz["files"].get(f) != h}
    return len(bad) == 0, bad
