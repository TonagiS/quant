"""
MACD 钝化 / 结构 判定 —— 按公开描述还原，并非原指标源码。

底部规则（顶部：把价格和 DIF 取负后套用同一套逻辑）：
  前低     : 最近 lookback 根内、排除最近 recent 根之外的最低收盘价
  前低DIF  : 前低附近 ±trough 根内 DIF 的最小值
  钝化     : 最近 recent 根内出现 收盘价 < 前低，但 DIF > 前低DIF
  结构形成 : 钝化之后 DIF 首次拐头向上（DIF[j] > DIF[j-1] 且 DIF[j-1] <= DIF[j-2]）
  钝化消失 : 最近一次创新低那天 DIF 也 <= 前低DIF（动能同步新低），钝化失效

只用收盘价。盘中追加临时K线得出的信号属于“预判”，收盘后可能改变。
"""
import numpy as np
import pandas as pd

# 共享配置：ETF 池
ETF_POOL = {
    "sh510300": "沪深300", "sh588000": "科创50", "sz159915": "创业板",
    "sh515050": "通信CPO/PCB", "sh515320": "电子50(PCB)", "sh588810": "科创芯片",
    "sh159516": "半导体设备", "sh512480": "半导体", "sh512720": "计算机", "sh515230": "软件",
    "sh515070": "人工智能AI", "sh159819": "AI应用(159819)", "sh562500": "机器人",
    "sh512010": "医药", "sh512170": "医疗", "sh159992": "创新药", "sh515170": "食品饮料",
    "sh512690": "酒", "sz159928": "消费", "sh512400": "有色金属", "sh515220": "煤炭",
    "sh518880": "黄金", "sh159611": "电力", "sh159697": "油气", "sh512800": "银行",
    "sh515030": "新能源车", "sh515790": "光伏", "sh512660": "军工", "sh512880": "证券",
    "sh512200": "房地产",
}

DEFAULTS = dict(lookback=120, recent=5, trough=5)


def calc_macd(close, fast=12, slow=26, signal=9):
    """返回 (dif, dea) 两个 numpy 数组。EMA 是因果的，可直接用于回测。"""
    s = pd.Series(np.asarray(close, dtype=float))
    dif = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif.values, dea.values


def _bottom_state(close, dif, end, lookback, recent, trough):
    """只使用 close[:end+1]、dif[:end+1]，不看未来。返回 (状态, 距今几根K线)。"""
    start = max(end - lookback + 1, 0)
    stop = end - recent + 1                      # 前低窗口右端（不含）
    if stop - start < 20 or end < 2:
        return "数据不足", 0

    i0 = start + int(np.argmin(close[start:stop]))
    if i0 - start < trough:                      # 最低点贴着窗口左边界：不是可靠的前低
        return "无", 0
    prev_low = close[i0]
    lo, hi = i0 - trough, min(i0 + trough, stop - 1)
    prev_dif_low = dif[lo:hi + 1].min()

    below = [k for k in range(end - recent + 1, end + 1) if close[k] < prev_low]
    if not below:
        return "无", 0
    last = below[-1]

    if dif[last] <= prev_dif_low:                # DIF 同步创新低：没有钝化
        dull_before = any(dif[k] > prev_dif_low for k in below if k < last)
        return ("钝化消失", end - last) if dull_before else ("无", 0)

    # 钝化成立：看 last 之后（含 last）DIF 是否已经拐头向上
    for j in range(end, last - 1, -1):
        if j >= 2 and dif[j] > dif[j - 1] and dif[j - 1] <= dif[j - 2]:
            return "结构形成", end - j
    return "钝化", end - last


def analyze(close, dif, side="bottom", lookback=120, recent=5, trough=5):
    """判断最新一根K线的状态。side: 'bottom' 或 'top'。"""
    c = np.asarray(close, dtype=float)
    d = np.asarray(dif, dtype=float)
    if side == "top":
        c, d = -c, -d
    return _bottom_state(c, d, len(c) - 1, lookback, recent, trough)


def structure_series(close, dif, side="bottom", lookback=120, recent=5, trough=5, warmup=120):
    """逐日状态序列（回测用）。第 t 天只用到 t 及以前的数据。"""
    c = np.asarray(close, dtype=float)
    d = np.asarray(dif, dtype=float)
    if side == "top":
        c, d = -c, -d
    out = [("数据不足", 0)] * len(c)
    for t in range(warmup, len(c)):
        out[t] = _bottom_state(c, d, t, lookback, recent, trough)
    return out


def label(state, age, side):
    if state in ("无", "数据不足"):
        return state
    prefix = "底" if side == "bottom" else "顶"
    when = "当日" if age == 0 else f"{age}日前"
    return f"{prefix}{state}({when})"
