"""仓位 policy v1 的纯函数（提案，未接线到 live/shadow、paper）。

用法（实盘/模拟盘）：
    from position.policy import build_target
    weights, info = build_target(targets, strategy_returns)
    # targets: live/shadow/engine.py: targets()["targets"]，sum=1
    # strategy_returns: S2 的日收益序列（paper/shadow 已实现或回测重建）
    # weights: 目标权重（含现金，sum=info["exposure"] <= 1）

规则：
  * 单票上限 name_cap（默认 40%），超出部分给未触顶的名字；全触顶留现金。
  * 总敞口 = clip(target_vol / 60 日已实现波动, 0, 1)；样本 <40 日时敞口=1（暖机）。
  * 不加杠杆，现金收益不计。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TD = 252
DEFAULTS = dict(target_vol=0.15, vol_window=60, min_obs=40, name_cap=0.40, expo_cap=1.0)


def realized_vol(rets, window: int = 60, min_obs: int = 40):
    """年化已实现波动；样本不足返回 None。"""
    r = pd.Series(np.asarray(rets, dtype=float)).replace([np.inf, -np.inf], np.nan).dropna().tail(window)
    if len(r) < min_obs:
        return None
    sd = float(r.std(ddof=1)) * np.sqrt(TD)
    return sd if np.isfinite(sd) and sd > 1e-8 else None


def target_exposure(rets, target_vol: float = 0.15, window: int = 60,
                    min_obs: int = 40, cap: float = 1.0) -> float:
    """按已实现波动给出总敞口；暖机期返回 1.0（与 shadow v1 当前满仓一致）。"""
    vol = realized_vol(rets, window, min_obs)
    if vol is None:
        return 1.0
    return float(np.clip(target_vol / vol, 0.0, cap))


def cap_weights(weights: dict, cap: float = 0.40) -> dict:
    """单票上限；water-filling，超额分给未触顶的名字；全触顶时留现金。"""
    w = {c: float(x) for c, x in weights.items() if x and x > 0}
    if not w:
        return {}
    total = sum(w.values())
    if total <= 0:
        return {}
    # 先归一到 1（targets 理论上已归一，防脏数据）
    w = {c: x / total for c, x in w.items()}
    if cap >= 1.0:
        return w
    arr = np.array(list(w.values()), dtype=float)
    names = list(w)
    for _ in range(50):
        over = arr > cap + 1e-12
        if not over.any():
            break
        excess = float((arr[over] - cap).sum())
        arr[over] = cap
        free = arr < cap - 1e-12
        if not free.any():
            break
        arr[free] += excess * arr[free] / arr[free].sum()
    return {c: float(x) for c, x in zip(names, arr) if x > 1e-12}


def build_target(targets: dict, rets, target_vol: float = DEFAULTS["target_vol"],
                 vol_window: int = DEFAULTS["vol_window"], min_obs: int = DEFAULTS["min_obs"],
                 name_cap: float = DEFAULTS["name_cap"], expo_cap: float = 1.0) -> tuple[dict, dict]:
    """返回 (含现金的目标权重, 诊断信息)。权重和 = exposure，其余为现金。"""
    vol = realized_vol(rets, vol_window, min_obs)
    expo = float(np.clip(target_vol / vol, 0.0, expo_cap)) if vol else 1.0
    base = cap_weights(targets, name_cap)
    weights = {c: x * expo for c, x in base.items()}
    info = {"exposure": expo, "realized_vol": vol, "target_vol": target_vol,
            "name_cap": name_cap, "cash": 1.0 - sum(weights.values()),
            "n_names": len(weights), "warmup": vol is None,
            "max_weight": max(weights.values()) if weights else 0.0}
    return weights, info


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    fake = rng.normal(0.0006, 0.018, 300)          # 年化波动约 28.6%
    tgt = {"a": 0.5, "b": 0.3, "c": 0.2}
    w, info = build_target(tgt, fake)
    print("vol", round(info["realized_vol"], 4), "expo", round(info["exposure"], 3),
          "weights", {k: round(v, 3) for k, v in w.items()}, "cash", round(info["cash"], 3))
    w2, info2 = build_target({"a": 1.0}, fake)
    print("single-name", round(info2["max_weight"], 3), "expo", round(info2["exposure"], 3))
