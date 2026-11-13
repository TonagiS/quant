# -*- coding: utf-8 -*-
"""双账本模拟盘（真实费率口径）。

两个账本，各 100,000 元，同一起跑日：
  s2     —— S2 满仓（动量+60日线+逆波动），敞口 100%
  vt15   —— S2 + 波动率目标 15%（60 日窗口，敞口 = min(1, 15%/vol60)）

成本：买入佣金 5bp；卖出佣金 5bp + 赎回费（按该笔实际持有自然日分档：
<7 天 1.5% / 7~30 天 0.5% / >=30 天 0.15%）。VENUE="场内" 时无赎回费。
调仓规则：调仓日只卖出"不在新目标"的名字；仍在目标内的超配名字不主动减持，
只补低配（need<=0 跳过）。这是为避免赎回费的刻意设计，权重会随行情漂移。

用法：
  python paper_account.py --init 2026-11-03   # 按该日 14:30 价建仓（两个账本）
  python paper_account.py --execute           # 14:30 影子跑完后调仓（两个账本）
  python paper_account.py --mark              # 收盘后计净值（两个账本）
  python paper_account.py --status

账本：position/live/paper/books/<book>/{account.json,trades.csv,ledger.csv,expo.json}
"""
import argparse
import json
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "shadow"))
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

from run_shadow import px_close  # noqa: E402

SHADOW = os.path.join(HERE, "shadow")
RAW = os.path.join(ROOT, "data", "daily")
PAPER = os.path.join(HERE, "paper")
BOOKS_DIR = os.path.join(PAPER, "books")
TRADES_COLS = ["date", "time", "code", "side", "shares", "price", "amount", "fee"]
LEDGER_COLS = ["date", "time", "phase", "book", "nav", "cash", "pos_value", "n_pos",
               "day_ret", "total_ret", "exposure", "notes"]

COST, LOT, INIT = 0.0005, 100, 100000.0
VENUE = "场外"
REDEEM_SHORT, REDEEM_MID, REDEEM_LONG = 0.015, 0.005, 0.0015
MIN_HOLD_DAYS, TIER_LONG_DAYS = 7, 30

BOOKS = [("s2", "S2满仓", None, False),
         ("vt15", "VT15", 0.15, False)]
# ADX 账本已于 2026-11-04 撤下（POS-07 修正 base_ret 后未通过）；以下函数保留给研究，不在 BOOKS 中
ADX_WIN, ADX_TH, ADX_MULT = 14, 20.0, 0.5


# ---------------------------------------------------------------- 基础工具
def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, indent=2, ensure_ascii=False)


def append_rows(path, cols, row):
    df = pd.DataFrame([row], columns=cols)
    df.to_csv(path, mode="a", header=not os.path.exists(path), index=False, encoding="utf-8-sig")


def redeem_fee(amount, held_days):
    if VENUE == "场内":
        return 0.0
    if held_days < MIN_HOLD_DAYS:
        return amount * REDEEM_SHORT
    if held_days < TIER_LONG_DAYS:
        return amount * REDEEM_MID
    return amount * REDEEM_LONG


def book_dir(name):
    return os.path.join(BOOKS_DIR, name)


def acc_path(name):
    return os.path.join(book_dir(name), "account.json")


def trades_path(name):
    return os.path.join(book_dir(name), "trades.csv")


def ledger_path(name):
    return os.path.join(book_dir(name), "ledger.csv")


def account(name, start=None):
    acc = load(acc_path(name), None)
    if acc is None:
        acc = {"initial": INIT, "cash": INIT, "start": str(start or date.today()),
               "positions": {}, "last_px": {}}
        save(acc_path(name), acc)
    return acc


def raw_for(ds=None):
    ds = ds or date.today()
    return load(os.path.join(RAW, f"{ds:%Y%m%d}.json"), {"codes": {}, "failed": {}})


# ---------------------------------------------------------------- 敞口（VT15）
def s2_returns():
    """回测重建 S2 日收益序列（ext 池、10 交易日调仓、5bp），供 vol60 使用。"""
    from position.engine import Config, Fixed, features, load_panel, simulate
    close = load_panel("ext")
    f = features(close)
    res = simulate(close, f, Config(reb=10, buy_cost=COST, sell_cost=COST, rule=Fixed(1.0)))
    lo = f["start"]
    return pd.Series(res["ret"][lo:], index=close.index[lo:]).dropna()


def vol60_and_expo(target=0.15, window=60, min_obs=40):
    rets = s2_returns().tail(window)
    info = {"date": str(date.today()), "n": int(len(rets)), "target": target}
    if len(rets) < min_obs:
        info.update({"vol60": None, "expo": 1.0, "warmup": True})
        return info
    vol = float(rets.std(ddof=1)) * (252 ** 0.5)
    info.update({"vol60": round(vol, 4), "expo": round(min(1.0, target / vol), 4), "warmup": False})
    return info


def adx_series(window=ADX_WIN):
    """池等权指数上的 ADX（趋势强度），用于震荡市降档。"""
    from position.engine import load_panel
    close = load_panel("ext")
    snap = raw_for().get("codes") or {}
    if snap:
        row = {c: snap.get(c) for c in close.columns}
        if any(v is not None for v in row.values()):
            close.loc[pd.Timestamp(date.today())] = [row.get(c) for c in close.columns]
    level = close.mean(axis=1).dropna()
    h = level.rolling(2).max()
    l = level.rolling(2).min()
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=level.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=level.index)
    tr = pd.concat([h - l, (h - level.shift()).abs(), (l - level.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / window, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1 / window, adjust=False).mean() / atr
    mdi = 100 * minus_dm.ewm(alpha=1 / window, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / window, adjust=False).mean()


def adx_factor():
    """震荡市（ADX < 阈值）返回 0.5，否则 1.0。"""
    try:
        a = adx_series().dropna()
        if len(a) < ADX_WIN + 2:
            return 1.0, None
        v = float(a.iloc[-1])
        return (ADX_MULT if v < ADX_TH else 1.0), v
    except Exception as e:
        print(f"  ADX 计算失败，按 1.0 处理: {e}")
        return 1.0, None


def book_targets(name, vt, use_adx=False):
    st = load(os.path.join(SHADOW, "state.json"), {"last_targets": {}})
    targets = st.get("last_targets", {}) or {}
    reb = str(st.get("last_rebalance") or "")
    if not targets:
        return {}, 1.0, {"note": "影子没有目标"}, reb
    if vt is None:
        return dict(targets), 1.0, {"note": "满仓"}, reb
    cache_path = os.path.join(book_dir(name), "expo.json")
    info = load(cache_path, {}) or {}
    if info.get("rebalance_date") != reb or not info.get("expo"):
        info = vol60_and_expo(vt)
        info["rebalance_date"] = reb
        save(cache_path, info)
    expo = float(info["expo"])
    if use_adx:
        f_adx, adx_val = adx_factor()
        info["adx"] = adx_val
        info["adx_factor"] = f_adx
        expo = expo * f_adx
    return {c: w * expo for c, w in targets.items()}, expo, info, reb


# ---------------------------------------------------------------- 成交
def trade_book(name, label, vt, use_adx=False, ds=None, quiet=False):
    ds = ds or date.today()
    dstr = str(ds)
    acc = account(name, start=dstr)
    targets, expo, info, reb = book_targets(name, vt, use_adx)
    if not targets:
        print(f"[{label}] 没有目标权重（影子未跑），跳过")
        return None
    if acc.get("positions") and acc.get("last_rebalance") == reb:
        print(f"[{label}] 非调仓日（影子上次调仓 {reb}），不动")
        return None
    px = raw_for(ds)["codes"]
    if not px:
        print(f"[{label}] 没有 {dstr} 的价格快照，跳过")
        return None

    last = acc.get("last_px", {})
    nav0 = acc["cash"] + sum(v["shares"] * px.get(c, last.get(c, v["cost"]))
                             for c, v in acc["positions"].items())
    if not quiet:
        print(f"[{label}] 成交前净值 {nav0:,.2f}（现金 {acc['cash']:,.2f}），"
              f"目标 {len(targets)} 只，敞口 {expo:.0%}")

    trades = []
    for c in list(acc["positions"]):
        if c in targets:
            continue
        v = acc["positions"][c]
        p = px.get(c)
        if p is None:
            print(f"  跳过卖出 {c}: 无价格")
            continue
        od = v.get("open_date") or acc.get("start")
        held = (ds - date.fromisoformat(str(od))).days if od else MIN_HOLD_DAYS
        if VENUE != "场内" and held < MIN_HOLD_DAYS:
            print(f"  顺延卖出 {c}: 持有 {held} 天 < {MIN_HOLD_DAYS}（避开 1.5%）")
            continue
        amt = v["shares"] * p
        redeem = redeem_fee(amt, held)
        comm = amt * COST
        acc["cash"] += amt - comm - redeem
        trades.append({"date": dstr, "time": datetime.now().strftime("%H:%M:%S"), "code": c,
                       "side": "sell", "shares": v["shares"], "price": round(p, 4),
                       "amount": round(amt, 2), "fee": round(comm + redeem, 2)})
        del acc["positions"][c]

    for c, w in sorted(targets.items(), key=lambda kv: -kv[1]):
        p = px.get(c)
        if p is None or p <= 0:
            print(f"  跳过买入 {c}: 无价格")
            continue
        cur = acc["positions"].get(c, {"shares": 0, "cost": 0.0})
        need = w * nav0 - cur["shares"] * p
        if need <= 0:
            continue
        lots = int(min(need, acc["cash"] / (1 + COST)) / p // LOT)
        if lots <= 0:
            continue
        sh = lots * LOT
        amt = sh * p
        fee = amt * COST
        acc["cash"] -= amt + fee
        if cur["shares"] == 0 or not cur.get("open_date"):
            cur["open_date"] = dstr
        new_sh = cur["shares"] + sh
        cur["cost"] = round((cur["shares"] * cur["cost"] + amt) / new_sh, 4)
        cur["shares"] = new_sh
        acc["positions"][c] = cur
        trades.append({"date": dstr, "time": datetime.now().strftime("%H:%M:%S"), "code": c,
                       "side": "buy", "shares": sh, "price": round(p, 4),
                       "amount": round(amt, 2), "fee": round(fee, 2)})

    acc["last_px"] = {c: px[c] for c in px}
    acc["last_rebalance"] = reb
    save(acc_path(name), acc)
    for t in trades:
        append_rows(trades_path(name), TRADES_COLS, t)

    pos_val = sum(v["shares"] * px.get(c, v["cost"]) for c, v in acc["positions"].items())
    nav = acc["cash"] + pos_val
    append_rows(ledger_path(name), LEDGER_COLS,
                {"date": dstr, "time": datetime.now().strftime("%H:%M:%S"), "phase": "1430",
                 "book": label, "nav": round(nav, 2), "cash": round(acc["cash"], 2),
                 "pos_value": round(pos_val, 2), "n_pos": len(acc["positions"]),
                 "day_ret": "", "total_ret": round(nav / acc["initial"] - 1, 6),
                 "exposure": round(expo, 4), "notes": "14:30 成交"})
    print(f"[{label}] 成交 {len(trades)} 笔，净值 {nav:,.2f}（{nav/acc['initial']-1:+.2%}），"
          f"持仓 {len(acc['positions'])} 只，现金 {acc['cash']/nav:.1%}")
    return nav


def execute():
    for name, label, vt, use_adx in BOOKS:
        trade_book(name, label, vt, use_adx)


# ---------------------------------------------------------------- 收盘计价
def sina_close(code):
    import akshare as ak
    df = ak.stock_zh_a_minute(symbol=code, period="30", adjust="")
    df["day"] = pd.to_datetime(df["day"])
    t = df[df["day"].dt.date == date.today()]
    if t.empty:
        raise ValueError("no today bar")
    return float(pd.to_numeric(t.iloc[-1]["close"], errors="coerce"))


def last_price(code):
    for fn in (px_close, sina_close):
        try:
            return float(fn(code)), fn.__name__
        except Exception:
            continue
    raw = raw_for()
    if code in raw["codes"]:
        return float(raw["codes"][code]), "px1430"
    raise ValueError("no price")


def prev_nav(name, ds):
    led = ledger_path(name)
    if not os.path.exists(led):
        return None
    df = pd.read_csv(led, encoding="utf-8-sig")
    df = df[df["date"].astype(str) < ds]
    return float(df["nav"].iloc[-1]) if len(df) else None


def mark():
    ds = str(date.today())
    for name, label, vt, use_adx in BOOKS:
        acc = load(acc_path(name), None)
        if not acc:
            continue
        px, src, failed = {}, {}, []
        for c in acc["positions"]:
            try:
                p, s = last_price(c)
                px[c] = p
                src[c] = s
            except Exception as e:
                failed.append(f"{c}:{type(e).__name__}")
        val = sum(v["shares"] * px.get(c, v["cost"]) for c, v in acc["positions"].items())
        nav = acc["cash"] + val
        acc["last_px"].update(px)
        save(acc_path(name), acc)
        prev = prev_nav(name, ds) or acc["initial"]
        append_rows(ledger_path(name), LEDGER_COLS,
                    {"date": ds, "time": datetime.now().strftime("%H:%M:%S"), "phase": "close",
                     "book": label, "nav": round(nav, 2), "cash": round(acc["cash"], 2),
                     "pos_value": round(val, 2), "n_pos": len(acc["positions"]),
                     "day_ret": round(nav / prev - 1, 6) if prev else "",
                     "total_ret": round(nav / acc["initial"] - 1, 6), "exposure": "",
                     "notes": ("缺价: " + ",".join(failed)) if failed else
                              ("价源: " + ",".join(sorted(set(src.values()))))})
        print(f"[{label}] 收盘净值 {nav:,.2f}，当日 {nav/prev-1:+.2%}，累计 {nav/acc['initial']-1:+.2%}")


# ---------------------------------------------------------------- 建仓（补历史）
def init_from(dstr):
    ds = date.fromisoformat(dstr)
    px = (raw_for(ds).get("codes") or {})
    if not px:
        raise SystemExit(f"{dstr} 没有 14:30 快照，无法建仓")
    sl = pd.read_csv(os.path.join(SHADOW, "shadow_log.csv"), encoding="utf-8-sig")
    rows = sl[(sl["date"].astype(str) == dstr) & (sl["phase"] == "1430")
              & (sl["targets_json"].astype(str) != "")]
    if not len(rows):
        raise SystemExit(f"{dstr} 影子日志里没有目标")
    targets = json.loads(rows.iloc[-1]["targets_json"])

    for name, label, vt, use_adx in BOOKS:
        if os.path.exists(acc_path(name)):
            print(f"[{label}] 账本已存在，跳过建仓")
            continue
        acc = {"initial": INIT, "cash": INIT, "start": dstr, "positions": {}, "last_px": {}}
        save(acc_path(name), acc)
        expo = 1.0
        if vt is not None:
            info = vol60_and_expo(vt)
            expo = float(info["expo"])
            if use_adx:
                f_adx, adx_val = adx_factor()
                info["adx"] = adx_val
                info["adx_factor"] = f_adx
                expo = expo * f_adx
            save(os.path.join(book_dir(name), "expo.json"), info)
        weights = {c: w * expo for c, w in targets.items()}
        nav0 = acc["cash"]
        for c, w in sorted(weights.items(), key=lambda kv: -kv[1]):
            p = px.get(c)
            if p is None or p <= 0:
                print(f"  跳过 {c}: 无 {dstr} 价格")
                continue
            lots = int(min(w * nav0, acc["cash"] / (1 + COST)) / p // LOT)
            if lots <= 0:
                continue
            sh = lots * LOT
            amt = sh * p
            fee = amt * COST
            acc["cash"] -= amt + fee
            acc["positions"][c] = {"shares": sh, "cost": round(p, 4), "open_date": dstr}
            append_rows(trades_path(name), TRADES_COLS,
                        {"date": dstr, "time": "14:30:00", "code": c, "side": "buy",
                         "shares": sh, "price": round(p, 4),
                         "amount": round(amt, 2), "fee": round(fee, 2)})
        acc["last_px"] = px
        save(acc_path(name), acc)
        pos_val = sum(v["shares"] * px.get(c, v["cost"]) for c, v in acc["positions"].items())
        nav = acc["cash"] + pos_val
        append_rows(ledger_path(name), LEDGER_COLS,
                    {"date": dstr, "time": "14:30:00", "phase": "1430", "book": label,
                     "nav": round(nav, 2), "cash": round(acc["cash"], 2),
                     "pos_value": round(pos_val, 2), "n_pos": len(acc["positions"]),
                     "day_ret": "", "total_ret": round(nav / INIT - 1, 6),
                     "exposure": round(expo, 4), "notes": "建仓（补历史）"})
        print(f"[{label}] 建仓完成：净值 {nav:,.2f}，持仓 {len(acc['positions'])} 只，"
              f"敞口 {expo:.0%}，现金 {acc['cash']:,.2f}")


def status():
    for name, label, vt, use_adx in BOOKS:
        acc = load(acc_path(name), None)
        if not acc:
            print(f"[{label}] 无账本")
            continue
        px = acc.get("last_px", {})
        val = sum(v["shares"] * px.get(c, v["cost"]) for c, v in acc["positions"].items())
        nav = acc["cash"] + val
        print(f"[{label}] 净值 {nav:,.2f}（{nav/acc['initial']-1:+.2%}） 现金 {acc['cash']:,.2f} "
              f"持仓 {len(acc['positions'])} 只 起始 {acc.get('start')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--mark", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--init", metavar="YYYY-MM-DD")
    a = ap.parse_args()
    if a.init:
        init_from(a.init)
    elif a.execute:
        execute()
    elif a.mark:
        mark()
    elif a.status:
        status()
    else:
        print("需要 --init YYYY-MM-DD / --execute / --mark / --status")
