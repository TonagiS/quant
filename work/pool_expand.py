# -*- coding: utf-8 -*-
"""扩池实验：52 只现有 ETF + 一批补充 ETF（跨境/债/商品/细分行业），看是否值得扩。

产物：E:\\Code\\quant\\work\\pool_expand.txt + 缓存 data/cache2019_exp/
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\Code\quant")

from position.engine import Config, VolTarget, Fixed, features, load_panel, simulate, slice_metrics  # noqa: E402

OUT = r"E:\Code\quant\work\pool_expand.txt"
EXP_CACHE = r"E:\Code\quant\data\cache2019_exp"
os.makedirs(EXP_CACHE, exist_ok=True)
lines = []

CAND = {
    "sh513520": "日经225", "sh513030": "德国30", "sh511010": "国债ETF", "sh511260": "十年国债",
    "sz159985": "豆粕", "sz159981": "能源化工", "sh513120": "港股创新药", "sz159792": "港股互联网",
    "sh515210": "钢铁", "sz159825": "农业", "sz159766": "旅游", "sz159996": "家电",
    "sh516110": "汽车", "sh516960": "机械", "sh516950": "基建", "sz159870": "化工",
    "sh512890": "红利低波",
}
BUY = 0.0005


def log(s=""):
    print(s)
    lines.append(str(s))


def fetch(code):
    path = os.path.join(EXP_CACHE, f"{code}.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["date"])
        return df if len(df) >= 150 else None
    import akshare as ak
    try:
        df = ak.stock_zh_a_hist_tx(symbol=code, start_date="20190101", end_date="20261102", adjust="qfq")
        if df is None or len(df) < 150:
            return None
        df = df[["date", "close"]].copy()
        df["date"] = pd.to_datetime(df["date"])
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return df
    except Exception as e:
        log(f"  取数失败 {code} {CAND.get(code)}: {type(e).__name__} {str(e)[:50]}")
        return None


def panel_with_extra(extra_codes):
    close = load_panel("ext")
    add = {}
    for c in extra_codes:
        df = fetch(c)
        if df is None:
            continue
        add[c] = df.set_index("date")["close"].astype(float)
    if not add:
        return close, []
    wide = pd.concat([close, pd.DataFrame(add)], axis=1).sort_index()
    return wide, list(add)


def stats(close, label):
    f = features(close)
    lo = f["start"]
    n = f["cand"].iloc[lo:].sum(axis=1)
    log(f"[{label}] 标的 {close.shape[1]} 只，有效天数 {len(n)}")
    log(f"   候选数 中位 {int(n.median())}、均值 {n.mean():.1f}；<=5 天占比 {100*(n<=5).mean():.1f}%、"
        f"<=8 占比 {100*(n<=8).mean():.1f}%")
    return f, lo


def perf(close, f, label):
    out = []
    for rname, mk in (("满仓", lambda reb: Fixed(1.0)), ("VT15", lambda reb: VolTarget(target=0.15, window=60, every=reb))):
        cfg = Config(reb=10, k=5, buy_cost=BUY, fee_tier=True, rule=mk(10))
        m = slice_metrics(simulate(close, f, cfg), f["start"], len(close))
        out.append((rname, m))
        log(f"   {label} {rname}: 年化 {m['cagr']:>7.2%} 回撤 {m['mdd']:>8.2%} "
            f"卡玛 {m['calmar']:.2f} 换手 {m['turn_two_way']:.1f}x 成本 {m['cost_drag']:.2%}")
    return out


log("=== 扩池实验（场外·真实持有天数分档·reb=10）===")
close52 = load_panel("ext")
f52, _ = stats(close52, "现有 52 只")
perf(close52, f52, "52只")

close_exp, added = panel_with_extra(list(CAND))
if added:
    log("")
    log("补充成功: " + "、".join(f"{CAND.get(c, c)}({c[2:]})" for c in added))
    f_exp, _ = stats(close_exp, "扩池后")
    perf(close_exp, f_exp, "扩池")

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("\n写出:", OUT)
