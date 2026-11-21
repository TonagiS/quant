"""第 3 个账本：VT15 · 5 交易日调仓（场内费率口径）。

和 s2 / vt15 的区别只有两点：
    1. 调仓节奏 5 交易日（≈7 自然日），不是 14 自然日；
    2. 费率按场内 ETF：只有 5bp 佣金，没有赎回费、没有 7 天最短持有（场外那条 1.5% 不存在）。
池子：52 只里去掉跨境 sh513500 标普500 / sh513100 纳指100（老爹 2026-11-20 要求）。
建仓日与另外两个账本一致：2026-11-03。成交价优先用当日 14:30 快照，缺失才退回收盘价；
收盘行一律用真收盘价记账（2026-11-20 修：原回填把 14:30 快照价写进了收盘行，且调仓日漏收盘行）。

用法：
    python paper_book5d.py --backfill 2026-11-03   # 补历史（已建过账本会拒绝重跑）
    python paper_book5d.py --execute              # 每个交易日 14:30
    python paper_book5d.py --mark                 # 收盘计价
    python paper_book5d.py --status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import paper_account as pa  # noqa: E402
from position.engine import Config, Fixed, features, load_panel, simulate  # noqa: E402

NAME, LABEL = "vt15_5d", "VT15·5日"
CROSS = ["sh513500", "sh513100"]
REB, VT = 5, 0.15


def panel(ds: str | None = None) -> pd.DataFrame:
    close = load_panel("ext")
    close = close.drop(columns=[c for c in CROSS if c in close.columns])
    if ds:
        snap = pa.raw_for(date.fromisoformat(ds)).get("codes") or {}
        row = pd.Series({c: snap.get(c) for c in close.columns})
        if snap and row.notna().any():
            close.loc[pd.Timestamp(ds)] = row
    return close


def series(ds: str | None = None):
    close = panel(ds)
    f = features(close)
    cost = pa.COST
    r5 = simulate(close, f, Config(k=5, reb=REB, reb_mode="rolling", weight_mode="invvol",
                                   buy_cost=cost, sell_cost=cost), keep_weights=True)
    base = simulate(close, f, Config(k=5, reb=10, reb_mode="rolling", weight_mode="invvol",
                                     buy_cost=cost, sell_cost=cost, rule=Fixed(1.0)))
    ret = pd.Series(base["ret"], index=close.index)
    vol = ret.rolling(60, min_periods=40).std() * np.sqrt(252)
    return close, r5, vol


def trade_days(r5: dict, start: str) -> list[int]:
    out, prev = [], {}
    for i, w in enumerate(r5["w_hist"]):
        if w != prev:
            out.append(i)
        prev = w
    return [i for i in out if i > 0 and str(r5["dates"][i].date()) >= start]


def apply_trade(acc: dict, ds: str, px: dict, targets: dict, expo: float, note: str = "") -> list[dict]:
    """场内口径：卖出只扣 5bp，不限 7 天。"""
    trades = []
    nav0 = acc["cash"] + sum(v["shares"] * px.get(c, acc["last_px"].get(c, v["cost"]))
                             for c, v in acc["positions"].items())
    for c in list(acc["positions"]):
        if c in targets:
            continue
        v = acc["positions"][c]
        p = px.get(c)
        if not p:
            continue
        amt = v["shares"] * p
        fee = amt * pa.COST
        acc["cash"] += amt - fee
        trades.append({"date": ds, "time": "14:30:00", "code": c, "side": "sell",
                       "shares": v["shares"], "price": round(p, 4),
                       "amount": round(amt, 2), "fee": round(fee, 2)})
        del acc["positions"][c]

    # 敞口回收（同 vt15 逻辑：超目标 5pp 才砍）
    if acc["positions"]:
        pos_now = sum(v["shares"] * px.get(c, acc["last_px"].get(c, v["cost"]))
                      for c, v in acc["positions"].items())
        expo_now = pos_now / nav0 if nav0 > 0 else 0.0
        if expo_now > expo + pa.TRIM_GAP:
            need = pos_now - expo * nav0
            over = []
            for c, w in targets.items():
                v, p = acc["positions"].get(c), px.get(c)
                if not v or not p:
                    continue
                diff = v["shares"] * p - w * nav0
                if diff > 0:
                    over.append((diff, c, p))
            over.sort(reverse=True)
            for diff, c, p in over:
                if need <= 0:
                    break
                v = acc["positions"][c]
                cut = int(min(diff, need) / p // pa.LOT) * pa.LOT
                if cut <= 0:
                    continue
                if cut >= v["shares"]:
                    cut = (v["shares"] // pa.LOT - 1) * pa.LOT
                if cut <= 0:
                    continue
                amt = cut * p
                fee = amt * pa.COST
                acc["cash"] += amt - fee
                v["shares"] -= cut
                need -= amt
                trades.append({"date": ds, "time": "14:30:00", "code": c, "side": "sell",
                               "shares": cut, "price": round(p, 4),
                               "amount": round(amt, 2), "fee": round(fee, 2)})

    for c, w in sorted(targets.items(), key=lambda kv: -kv[1]):
        p = px.get(c)
        if not p or p <= 0:
            continue
        cur = acc["positions"].get(c, {"shares": 0, "cost": 0.0})
        need = w * nav0 - cur["shares"] * p
        if need <= 0:
            continue
        lots = int(min(need, acc["cash"] / (1 + pa.COST)) / p // pa.LOT)
        if lots <= 0:
            continue
        sh = lots * pa.LOT
        amt = sh * p
        fee = amt * pa.COST
        acc["cash"] -= amt + fee
        if cur["shares"] == 0 or not cur.get("open_date"):
            cur["open_date"] = ds
        new_sh = cur["shares"] + sh
        cur["cost"] = round((cur["shares"] * cur["cost"] + amt) / new_sh, 4)
        cur["shares"] = new_sh
        acc["positions"][c] = cur
        trades.append({"date": ds, "time": "14:30:00", "code": c, "side": "buy",
                       "shares": sh, "price": round(p, 4),
                       "amount": round(amt, 2), "fee": round(fee, 2)})

    acc["last_px"] = {c: px[c] for c in px}
    pos_val = sum(v["shares"] * px.get(c, v["cost"]) for c, v in acc["positions"].items())
    nav = acc["cash"] + pos_val
    pa.append_rows(pa.ledger_path(NAME), pa.LEDGER_COLS,
                   {"date": ds, "time": "14:30:00", "phase": "1430", "book": LABEL,
                    "nav": round(nav, 2), "cash": round(acc["cash"], 2),
                    "pos_value": round(pos_val, 2), "n_pos": len(acc["positions"]),
                    "day_ret": "", "total_ret": round(nav / acc["initial"] - 1, 6),
                    "exposure": round(expo, 4), "notes": "14:30 成交" + note})
    return trades


def backfill(start: str) -> None:
    if os.path.exists(pa.acc_path(NAME)):
        raise SystemExit(f"{LABEL} 账本已存在（{pa.acc_path(NAME)}），不重跑；要重建请先归档")
    close, r5, vol = series()
    days = trade_days(r5, start)
    if not days:
        raise SystemExit("回放区间内没有调仓日")
    dates = r5["dates"]
    # 建仓日固定为 start（老爹要求与另两个账本一致）：取该日实际持仓作为初始仓位
    i_start = max(i for i in range(len(dates)) if str(dates[i].date()) <= start)
    if days[0] > i_start:
        days = [i_start] + days
    acc = {"initial": pa.INIT, "cash": pa.INIT, "start": start, "positions": {},
           "last_px": {}, "last_rebalance": ""}
    d0, d1 = dates[days[0]], dates[-1]
    all_days = list(range(days[0], len(dates)))

    for i in all_days:
        ds = str(dates[i].date())
        # 收盘价（记账用，绝不能混进 14:30 快照价）
        px_close = {c: float(close.iat[i, j]) for j, c in enumerate(close.columns)
                    if np.isfinite(close.iat[i, j])}
        # 成交价：有 14:30 快照就用快照价（更贴近实盘），没有就退回收盘价
        snap = pa.raw_for(date.fromisoformat(ds)).get("codes") or {}
        px_trade = dict(px_close)
        px_trade.update({c: v for c, v in snap.items() if v})
        if i in days:
            w = r5["w_hist"][i]
            tot = sum(w.values())
            base_w = {close.columns[c] if isinstance(c, (int, np.integer)) else c: v / tot
                      for c, v in w.items()}
            v = vol.iloc[i]
            expo = float(min(1.0, VT / v)) if np.isfinite(v) and v > 0 else 1.0
            targets = {c: ww * expo for c, ww in base_w.items()}
            trades = apply_trade(acc, ds, px_trade, targets, expo, "（回填）")
            pa.save(pa.acc_path(NAME), acc)
            for t in trades:
                pa.append_rows(pa.trades_path(NAME), pa.TRADES_COLS, t)
            print(f"  {ds} 换仓 {len(trades)} 笔，敞口 {expo:.0%}")
        # 每个交易日都补一行收盘计价：调仓日也要有，否则下午的涨跌会混进下一天 day_ret
        val = sum(v["shares"] * px_close.get(c, acc["last_px"].get(c, v["cost"]))
                  for c, v in acc["positions"].items())
        nav = acc["cash"] + val
        prev = nav
        led = pa.ledger_path(NAME)
        if os.path.exists(led):
            df = pd.read_csv(led, encoding="utf-8-sig")
            df = df[df["date"].astype(str) < ds]
            prev = float(df["nav"].iloc[-1]) if len(df) else pa.INIT
        pa.append_rows(led, pa.LEDGER_COLS,
                       {"date": ds, "time": "15:00:00", "phase": "close", "book": LABEL,
                        "nav": round(nav, 2), "cash": round(acc["cash"], 2),
                        "pos_value": round(val, 2), "n_pos": len(acc["positions"]),
                        "day_ret": round(nav / prev - 1, 6) if prev and i > all_days[0] else "",
                        "total_ret": round(nav / acc["initial"] - 1, 6), "exposure": "",
                        "notes": "回填（收盘价）"})
    acc["last_rebalance"] = str(dates[days[-1]].date())
    pa.save(pa.acc_path(NAME), acc)
    led = pd.read_csv(pa.ledger_path(NAME), encoding="utf-8-sig")
    last = led[led["phase"] == "close"].iloc[-1]
    print(f"[{LABEL}] 回填完成 {d0.date()}~{d1.date()}，收盘净值 {float(last['nav']):,.2f}"
          f"（{float(last['total_ret']):+.2%}），持仓 {len(acc['positions'])} 只")


def execute() -> None:
    ds = str(date.today())
    acc = pa.load(pa.acc_path(NAME), None)
    if not acc:
        raise SystemExit(f"{LABEL} 还没建仓，先 --backfill")
    close, r5, vol = series(ds)
    i = len(close.index) - 1
    if str(close.index[i].date()) != ds:
        print(f"[{LABEL}] 面板最后一根不是今天（{close.index[i].date()}），跳过")
        return
    reb_due = r5["dates"][i] in [d for d in r5["dates"] if d >= close.index[i]] and False
    # 5 交易日节奏由回放序列决定：取序列里最近一次目标作为今日目标，按"距上次调仓交易日数"判断到期
    last = acc.get("last_rebalance") or acc.get("start")
    idx = r5["dates"]
    since = int(np.sum((idx > pd.Timestamp(last)) & (idx <= idx[i])))
    if since < REB:
        print(f"[{LABEL}] 距上次调仓 {since} 个交易日 < {REB}，非调仓日")
        return
    w = r5["w_hist"][i]
    tot = sum(w.values())
    base_w = {close.columns[c] if isinstance(c, (int, np.integer)) else c: v / tot for c, v in w.items()}
    v = vol.iloc[i]
    expo = float(min(1.0, VT / v)) if np.isfinite(v) and v > 0 else 1.0
    targets = {c: ww * expo for c, ww in base_w.items()}
    px = {c: v for c, v in (pa.raw_for(date.fromisoformat(ds)).get("codes") or {}).items() if v}
    trades = apply_trade(acc, ds, px, targets, expo)
    acc["last_rebalance"] = ds
    pa.save(pa.acc_path(NAME), acc)
    for t in trades:
        pa.append_rows(pa.trades_path(NAME), pa.TRADES_COLS, t)
    nav = acc["cash"] + sum(x["shares"] * px.get(c, x["cost"]) for c, x in acc["positions"].items())
    print(f"[{LABEL}] 成交 {len(trades)} 笔，净值 {nav:,.2f}（{nav / pa.INIT - 1:+.2%}），敞口 {expo:.0%}")


def mark() -> None:
    ds = str(date.today())
    acc = pa.load(pa.acc_path(NAME), None)
    if not acc:
        print(f"[{LABEL}] 无账本")
        return
    px, failed = {}, []
    for c in acc["positions"]:
        try:
            px[c] = pa.last_price(c)[0]
        except Exception as e:  # noqa: BLE001
            failed.append(f"{c}:{type(e).__name__}")
    val = sum(v["shares"] * px.get(c, v["cost"]) for c, v in acc["positions"].items())
    nav = acc["cash"] + val
    acc["last_px"].update(px)
    pa.save(pa.acc_path(NAME), acc)
    prev = pa.prev_nav(NAME, ds) or acc["initial"]
    pa.append_rows(pa.ledger_path(NAME), pa.LEDGER_COLS,
                   {"date": ds, "time": datetime.now().strftime("%H:%M:%S"), "phase": "close",
                    "book": LABEL, "nav": round(nav, 2), "cash": round(acc["cash"], 2),
                    "pos_value": round(val, 2), "n_pos": len(acc["positions"]),
                    "day_ret": round(nav / prev - 1, 6) if prev else "",
                    "total_ret": round(nav / acc["initial"] - 1, 6), "exposure": "",
                    "notes": ("缺价: " + ",".join(failed)) if failed else "价源: px_close"})
    print(f"[{LABEL}] 收盘净值 {nav:,.2f}，当日 {nav / prev - 1:+.2%}，累计 {nav / acc['initial'] - 1:+.2%}")


def status() -> None:
    acc = pa.load(pa.acc_path(NAME), None)
    if not acc:
        print(f"[{LABEL}] 无账本")
        return
    px = acc.get("last_px", {})
    val = sum(v["shares"] * px.get(c, v["cost"]) for c, v in acc["positions"].items())
    nav = acc["cash"] + val
    print(f"[{LABEL}] 净值 {nav:,.2f}（{nav / acc['initial'] - 1:+.2%}） 现金 {acc['cash']:,.2f} "
          f"持仓 {len(acc['positions'])} 只 起始 {acc.get('start')} 上次调仓 {acc.get('last_rebalance')}")
    for c, v in sorted(acc["positions"].items(), key=lambda kv: -kv[1]["shares"] * kv[1]["cost"]):
        print(f"  {c} {v['shares']} 股 成本 {v['cost']:.4f} 市值 {v['shares'] * px.get(c, v['cost']):,.0f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", metavar="YYYY-MM-DD")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--mark", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.backfill:
        backfill(a.backfill)
    elif a.execute:
        execute()
    elif a.mark:
        mark()
    elif a.status:
        status()
    else:
        ap.print_help()
