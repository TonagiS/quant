"""Shadow 每日运行器（规则 v1，冻结）。

用法：
  14:30  python run_shadow.py --phase 1430   # 取 14:30 价，算目标权重与假设订单
  15:10  python run_shadow.py --phase eod    # 补收盘价，算两种成交假设的当日收益

不做的事情：不下单、不接券商、不调用任何 LLM 参与计算、不改 decision_1430.py。
数据缺失只记缺失（failed/notes），不补造。
"""
import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))
sys.path.insert(0, HERE)

import engine  # noqa: E402
from macd_structure import ETF_POOL  # noqa: E402

SHADOW = HERE
RAW = os.path.join(ROOT, "data", "daily")
LOG = os.path.join(SHADOW, "shadow_log.csv")
STATE = os.path.join(SHADOW, "state.json")
UNIVERSE = os.path.join(SHADOW, "universe_v1.csv")
RULES = os.path.join(SHADOW, "rules_v2.json")

COLS = ["date", "time", "phase", "universe_n", "enough", "cash_gate_above_ma", "n_cand",
        "targets_json", "orders_json", "px_1430_json", "px_close_json",
        "ret_fill1430", "ret_fillclose", "failed", "notes"]


def universe():
    df = pd.read_csv(UNIVERSE, dtype={"code": str})
    return [c for c in df.loc[df["status"] == "OK", "code"]], df.loc[df["status"] != "OK", "code"].tolist()


def hist_close(code, days=400):
    import akshare as ak
    start = (date.today() - timedelta(days=days * 2)).strftime("%Y%m%d")
    df = ak.stock_zh_a_hist_tx(symbol=code, start_date=start, end_date=date.today().strftime("%Y%m%d"),
                               adjust="qfq")
    if df is None or len(df) < 130:
        raise ValueError(f"history short: {0 if df is None else len(df)}")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["close"].astype(float)


def px_1430(code):
    import akshare as ak
    df = ak.stock_zh_a_minute(symbol=code, period="30", adjust="")
    if df is None or df.empty:
        raise ValueError("minute empty")
    df = df.copy()
    df["day"] = pd.to_datetime(df["day"])
    today = df[df["day"].dt.date == date.today()]
    if today.empty:
        raise ValueError("no bar today")
    bar = today[today["day"].dt.strftime("%H:%M") <= "14:30"]
    if bar.empty:
        raise ValueError("no 14:30 bar")
    return float(pd.to_numeric(bar.iloc[-1]["close"], errors="coerce"))


def _sina_close(code):
    """腾讯日线在收盘后一段时间内可能还没有当日 bar，用新浪 30 分钟线的 15:00 收盘兜底。"""
    import akshare as ak
    df = ak.stock_zh_a_minute(symbol=code, period="30", adjust="")
    df["day"] = pd.to_datetime(df["day"])
    t = df[df["day"].dt.date == date.today()]
    if t.empty:
        raise ValueError("no today bar")
    return float(pd.to_numeric(t.iloc[-1]["close"], errors="coerce"))


def px_close(code):
    import akshare as ak
    df = ak.stock_zh_a_hist_tx(symbol=code, start_date=(date.today() - timedelta(days=10)).strftime("%Y%m%d"),
                               end_date=date.today().strftime("%Y%m%d"), adjust="")
    if df is None or df.empty:
        raise ValueError("daily empty")
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df[df["date"].dt.date == date.today()]
    if df.empty:
        return _sina_close(code)   # 腾讯日线更新慢，兜底新浪 15:00 bar
    return float(pd.to_numeric(df.iloc[-1]["close"], errors="coerce"))


def load_json(path, default):
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, indent=2, ensure_ascii=False)


def append_row(row):
    df = pd.DataFrame([row], columns=COLS)
    header = not os.path.exists(LOG)
    df.to_csv(LOG, mode="a", header=header, index=False, encoding="utf-8-sig")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["1430", "eod"], required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cadence-days", type=int, default=0,
                    help="覆盖 rules_v2.json 的调仓间隔（自然日）；5 交易日参考臂用 7")
    a = ap.parse_args()
    os.makedirs(RAW, exist_ok=True)
    codes, bad = universe()
    raw_path = os.path.join(RAW, f"{date.today():%Y%m%d}.json")
    raw = load_json(raw_path, {"codes": {}, "failed": {}})
    state = load_json(STATE, {"last_targets": {}, "last_date": None})

    if a.phase == "1430":
        px = {}
        for c in codes:
            try:
                px[c] = px_1430(c)
                raw["codes"][c] = px[c]
            except Exception as e:
                raw["failed"][c] = f"1430 {type(e).__name__}: {str(e)[:60]}"
            time.sleep(0.1)
        save_json(raw_path, raw)
        hist = {}
        ok_codes = [c for c in codes if c in px]
        for c in ok_codes:
            try:
                s = hist_close(c)
                s.loc[pd.Timestamp(date.today())] = px[c]  # 14:30 价代替当日收盘
                hist[c] = s
            except Exception as e:
                raw["failed"][c] = f"hist {type(e).__name__}: {str(e)[:60]}"
        close = pd.DataFrame(hist).sort_index()
        # v2：调仓周期从"每日"改成"每 reb_calendar_days 自然日"，非调仓日沿用上次目标
        rules = load_json(RULES, {})
        reb_days = int(a.cadence_days or rules.get("reb_calendar_days", 0) or 0)
        prev = state.get("last_targets", {})
        last_reb = state.get("last_rebalance")
        due = True
        if reb_days > 0 and prev and last_reb:
            try:
                due = (date.today() - date.fromisoformat(str(last_reb))).days >= reb_days
            except ValueError:
                due = True
        gate = engine.cash_gate(close) if not close.empty else {"above_ma": None}
        if close.empty:
            targets, n_cand, enough = prev, "", ""
            notes = f"无可用 14:30 价（失败 {len(raw['failed'])} 只），保持上次目标"
        elif due:
            t = engine.targets(close)
            targets, n_cand, enough = t["targets"], len(t["cand"]), t["enough"]
            notes = ""
        else:
            targets, n_cand, enough = prev, "", ""
            notes = f"非调仓日（v2 每 {reb_days} 自然日调仓，上次 {last_reb}）"
        orders = []
        for c, w in targets.items():
            orders.append({"code": c, "side": "buy" if c not in prev else "hold", "w": round(w, 6)})
        if due:
            for c in prev:
                if c not in targets:
                    orders.append({"code": c, "side": "sell", "w": 0.0})
        row = {"date": str(date.today()), "time": datetime.now().strftime("%H:%M:%S"), "phase": "1430",
               "universe_n": len(codes), "enough": enough, "cash_gate_above_ma": gate["above_ma"],
               "n_cand": n_cand, "targets_json": json.dumps(targets, ensure_ascii=False),
               "orders_json": json.dumps(orders, ensure_ascii=False),
               "px_1430_json": json.dumps(px, ensure_ascii=False), "px_close_json": "",
               "ret_fill1430": "", "ret_fillclose": "",
               "failed": json.dumps(raw["failed"], ensure_ascii=False), "notes": notes}
        if not a.dry_run:
            append_row(row)
            if due and targets and not close.empty:
                state["last_targets"] = targets
                state["last_date"] = str(date.today())
                state["last_rebalance"] = str(date.today())
                save_json(STATE, state)
        print(f"[1430] {'调仓' if due else '非调仓日'} 目标 {len(targets)} 只，失败 {len(raw['failed'])}，"
              f"闸门 {gate['above_ma']}")
        print(json.dumps(targets, ensure_ascii=False))
        return

    # eod：补收盘价与两种成交假设的当日收益
    px_c = {}
    for c in codes:
        try:
            px_c[c] = px_close(c)
        except Exception as e:
            raw["failed"][c] = f"close {type(e).__name__}: {str(e)[:60]}"
        time.sleep(0.1)
    summary = load_json(os.path.join(SHADOW, "last_1430.json"), {})
    tg = state.get("last_targets", {})
    p1430 = {c: raw["codes"][c] for c in raw["codes"]}
    r1 = r2 = np.nan
    if tg and p1430:
        num1 = num2 = den = 0.0
        for c, w in tg.items():
            if c in px_c and c in p1430:
                den += w
                num1 += w * (px_c[c] / p1430[c] - 1.0)   # 14:30 成交
                num2 += w * (px_c[c] / px_c[c] - 1.0)    # 收盘成交（当日无剩余收益）
        if den > 0:
            r1, r2 = num1 / den, num2 / den
    row = {"date": str(date.today()), "time": datetime.now().strftime("%H:%M:%S"), "phase": "eod",
           "universe_n": len(codes), "enough": "", "cash_gate_above_ma": "", "n_cand": "",
           "targets_json": "", "orders_json": "",
           "px_1430_json": json.dumps(p1430, ensure_ascii=False),
           "px_close_json": json.dumps(px_c, ensure_ascii=False),
           "ret_fill1430": "" if r1 != r1 else round(r1, 6),
           "ret_fillclose": "" if r2 != r2 else round(r2, 6),
           "failed": json.dumps(raw["failed"], ensure_ascii=False), "notes": "fill-to-close 口径"}
    save_json(raw_path, raw)
    if not a.dry_run:
        append_row(row)
    print(f"[eod] 收盘补齐 {len(px_c)} 只；fill-to-close 14:30 {r1:.4%} / 收盘 {r2:.4%}")


if __name__ == "__main__":
    main()
