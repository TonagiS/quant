"""仓位管理研究引擎（2026-11-03）。

在既有 S2（动量 + 60 日线 + 逆波动）之上，只改"仓位层"：
  * 总敞口：固定比例 / 波动率目标 / 回撤节流 / 净值均线 / 现金闸门 / Kelly
  * 个券上限、止损
信号、调仓频率、成本口径与 shadow v1 / backtest_composite 对齐；基线可复现
ext 2019-08-13~2026-11-02 的年化 18.8%、回撤 -33.6%、卡玛 0.561、双边换手 3988%。

约定：
  * 收盘出信号，delay=1 个交易日后收盘成交，次日起计收益（与 backtest_composite 相同）。
  * 仓位规则在成交日收盘用"截至前一交易日"的数据计算，成交后次日起生效（无前视）。
  * 现金收益 0；不加杠杆；成本按单边 cost 计；双边换手 = 权重 L1 变化之和。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDICATORS_CODE = os.path.join(ROOT, "indicators", "code")
sys.path.insert(0, INDICATORS_CODE)
CACHE = os.path.join(ROOT, "data", "cache2019")
TD = 252


# ---------------------------------------------------------------- 数据与特征
def load_panel(pool_name: str = "ext", cache_dir: str = CACHE) -> pd.DataFrame:
    from pools import get_pool
    from macd_structure import ETF_POOL

    pool = get_pool(pool_name, ETF_POOL)
    closes = {}
    for code in pool:
        path = os.path.join(cache_dir, f"{code}.csv")
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, parse_dates=["date"])
        closes[code] = df.set_index("date")["close"].astype(float)
    close = pd.DataFrame(closes).sort_index()
    return close.loc[:, close.notna().sum() >= 300]


def features(close: pd.DataFrame, min_names: int = 8) -> dict:
    ret = close.pct_change(fill_method=None)
    vol20 = ret.rolling(20, min_periods=15).std()
    mom = {n: close / close.shift(n) - 1.0 for n in (20, 60, 120)}
    ma60 = close.rolling(60, min_periods=60).mean()
    bars = close.notna().cumsum()
    valid = (bars >= 150) & close.notna() & mom[120].notna() & vol20.notna()
    rk = {n: mom[n].where(valid).rank(axis=1, pct=True) for n in (20, 60, 120)}
    rm = (rk[20] + rk[60] + rk[120]) / 3.0
    cand = valid & rm.notna() & (close > ma60)
    enough = (valid.sum(axis=1) >= min_names) & rm.notna().any(axis=1)
    start = int(np.argmax(enough.to_numpy())) if enough.any() else len(close) - 1
    return {"ret": ret, "vol20": vol20, "mom": mom, "ma60": ma60, "valid": valid,
            "rm": rm, "cand": cand, "start": start,
            "bench": (1.0 + ret.mean(axis=1).fillna(0.0)).cumprod()}


def _l1(a: dict, b: dict) -> float:
    return float(sum(abs(b.get(c, 0.0) - a.get(c, 0.0)) for c in set(a) | set(b)))


def _update_entries(entry_px: dict, entry_i: dict, new_w: dict, i: int,
                    close: pd.DataFrame) -> tuple[dict, dict]:
    """只保留当前持仓的建仓信息；继续持有的保留原建仓日，新买入的重置为 i。

    卖出后从 dict 中消失，之后再次买入会拿到新的 entry_i（不会沿用旧持有天数）。
    """
    new_px, new_i = {}, {}
    for c in new_w:
        if new_w[c] <= 0:
            continue
        if c in entry_px and c in entry_i:
            new_px[c] = entry_px[c]
            new_i[c] = entry_i[c]
            continue
        px = float(close.iat[i, c])
        if np.isfinite(px):
            new_px[c] = px
            new_i[c] = i
    return new_px, new_i


def _weights(names, vrow, cap=1.0) -> dict:
    if not names:
        return {}
    iv = np.array([1.0 / max(float(vrow[c]), 1e-4) if np.isfinite(vrow[c]) else np.nan for c in names])
    if np.isnan(iv).all():
        iv = np.ones(len(names))
    iv = np.where(np.isnan(iv), np.nanmean(iv), iv)
    w = iv / iv.sum()
    if cap < 1.0:
        for _ in range(50):
            over = w > cap + 1e-12
            if not over.any():
                break
            excess = float((w[over] - cap).sum())
            w[over] = cap
            free = w < cap - 1e-12          # 只把超额分给"还没到上限"的名字
            if not free.any():
                break                        # k*cap < 1 时留现金，不再来回倒
            w[free] += excess * w[free] / w[free].sum()
    return {c: float(x) for c, x in zip(names, w) if x > 1e-12}


def _apply_cap(w: np.ndarray, cap: float) -> np.ndarray:
    w = np.asarray(w, dtype=float)
    if w.sum() <= 0:
        return w
    w = w / w.sum()
    if cap >= 1.0:
        return w
    for _ in range(50):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        free = w < cap - 1e-12
        if not free.any():
            break
        w[free] += excess * w[free] / w[free].sum()
    return w


def _scheme_weights(mode: str, names, i: int, close: pd.DataFrame, f: dict,
                    cap: float, vrow=None) -> dict:
    """个券权重方案：invvol / equal / erc(126日协方差) / mdp(最大分散化)。"""
    if not names:
        return {}
    if mode == "invvol" or vrow is None:
        return _weights(names, vrow, cap) if vrow is not None else _equal_w(names, cap)
    if mode == "equal":
        return _equal_w(names, cap)

    ret_df = f["ret"]
    codes = [close.columns[c] for c in names]
    lo = max(0, i - 126)
    r = ret_df.iloc[lo:i][codes].dropna(how="all")
    if len(r) < 20:
        return _equal_w(names, cap)
    cov = r.cov().to_numpy()
    cov = np.nan_to_num(cov, nan=0.0)
    cov = cov + np.eye(len(names)) * 1e-8
    n = len(names)

    if mode == "erc":
        w = np.full(n, 1.0 / n)
        for _ in range(300):
            mrc = cov @ w
            rc = w * mrc
            if not np.isfinite(rc).all() or rc.mean() <= 0:
                break
            w = w * np.sqrt(rc.mean() / np.maximum(rc, 1e-15))
            w = np.clip(w, 1e-8, None)
            w = w / w.sum()
        return {c: float(x) for c, x in zip(names, _apply_cap(w, cap)) if x > 1e-12}

    if mode == "mdp":
        from scipy.optimize import minimize
        sigma = np.sqrt(np.maximum(np.diag(cov), 1e-12))

        def neg_div(w):
            denom = np.sqrt(max(float(w @ cov @ w), 1e-18))
            return -float(w @ sigma) / denom

        cons = ({"type": "eq", "fun": lambda w: float(w.sum() - 1.0)},)
        res = minimize(neg_div, np.full(n, 1.0 / n), method="SLSQP",
                       bounds=[(0.0, 1.0)] * n, constraints=cons,
                       options={"maxiter": 80, "ftol": 1e-10})
        w = res.x if res.success and np.isfinite(res.x).all() else np.full(n, 1.0 / n)
        return {c: float(x) for c, x in zip(names, _apply_cap(w, cap)) if x > 1e-12}

    return _equal_w(names, cap)


def _equal_w(names, cap=1.0) -> dict:
    w = _apply_cap(np.full(len(names), 1.0 / len(names)), cap)
    return {c: float(x) for c, x in zip(names, w) if x > 1e-12}


# ---------------------------------------------------------------- 仓位规则
class Rule:
    """仓位规则接口。scale(j, ctx) 用截至第 j 日（含）的数据给出目标总敞口。"""
    name = "base"
    every = 1          # 每几天更新一次敞口；1=每日，5=调仓日
    warmup = 0
    requires_base = False   # 是否需要未加仓控的 base_rets（VolTarget 用）

    def reset(self):
        pass

    def scale(self, j: int, ctx: dict) -> float:
        return 1.0


class Fixed(Rule):
    def __init__(self, s: float = 1.0, name: str | None = None):
        self.s = float(s)
        self.name = name or f"固定{self.s:.0%}"

    def scale(self, j, ctx):
        return self.s


class VolTarget(Rule):
    requires_base = True

    def __init__(self, target=0.12, window=60, min_periods=40, cap=1.0, every=1, name=None):
        self.target, self.window, self.min_periods = target, window, min_periods
        self.cap, self.every = cap, every
        self.name = name or f"波动目标{target:.0%}({window}日/每{every}日)"

    def scale(self, j, ctx):
        # 用"未加仓控的 S2 收益"估波动，避免用已缩放收益造成 sqrt 反馈
        src = ctx.get("base_rets")
        if src is None:
            raise ValueError("VolTarget.scale 需要 ctx['base_rets']；请用 run_with_base() "
                             "或确保 f['base_ret'] 已按同一 reb/cost 重建")
        lo = max(int(ctx.get("start", 0)), j - self.window + 1)
        r = src[lo: j + 1]
        if len(r) < max(self.min_periods, 20):
            return 1.0
        sd = float(np.nanstd(r, ddof=1)) * np.sqrt(TD)
        if not np.isfinite(sd) or sd <= 1e-8:
            return 1.0
        return float(np.clip(self.target / sd, 0.0, self.cap))


class DrawdownThrottle(Rule):
    """回撤分档减仓 + 回撤修复后恢复（滞回，避免来回打脸）。"""
    def __init__(self, cuts=((-0.10, 0.70), (-0.15, 0.40), (-0.20, 0.0)),
                 recover=(-0.05, 1.0), name=None):
        self.cuts = sorted(cuts, key=lambda x: -x[0])   # 由浅到深：-10%、-15%、-20%
        self.recover = recover
        self.level = 1.0
        self.name = name or "回撤节流(10/15/20%)"

    def reset(self):
        self.level = 1.0

    def scale(self, j, ctx):
        eq = ctx["equity"][: j + 1]
        if len(eq) < 2:
            return 1.0
        dd = float(eq[-1] / np.max(eq) - 1.0)
        target = 1.0
        for thr, sc in self.cuts:
            if dd <= thr:
                target = sc
        if dd <= self.cuts[0][0]:
            self.level = min(self.level, target)
        elif dd > self.recover[0]:
            self.level = self.recover[1]
        return self.level


class EquityMA(Rule):
    def __init__(self, window=200, below=0.5, name=None):
        self.window, self.below = window, below
        self.name = name or f"净值{window}日线(线下{below:.0%})"

    def scale(self, j, ctx):
        lo = max(int(ctx.get("start", 0)), j - self.window + 1)
        eq = ctx["equity"][lo: j + 1]
        if len(eq) < self.window:
            return 1.0
        return 1.0 if eq[-1] >= float(np.mean(eq)) else self.below


class CashGate(Rule):
    def __init__(self, window=120, below=0.0, name=None):
        self.window, self.below = window, below
        self.name = name or f"等权指数{window}日线(线下{below:.0%})"

    def scale(self, j, ctx):
        b = ctx["bench"][: j + 1]
        if len(b) < self.window:
            return 1.0
        return 1.0 if b[-1] >= float(np.mean(b[-self.window:])) else self.below


class Kelly(Rule):
    requires_base = True

    def __init__(self, window=120, frac=0.5, name=None):
        self.window, self.frac = window, frac
        self.name = name or f"半Kelly({window}日)"

    def scale(self, j, ctx):
        src = ctx.get("base_rets")
        lo = max(int(ctx.get("start", 0)), j - self.window + 1)
        r = (src if src is not None else ctx["rets"])[lo: j + 1]
        if len(r) < self.window:
            return 1.0
        mu = float(np.nanmean(r)) * TD
        var = float(np.nanvar(r, ddof=1)) * TD
        if var <= 1e-10:
            return 1.0
        return float(np.clip(self.frac * mu / var, 0.0, 1.0))


class MinRule(Rule):
    def __init__(self, rules, name):
        self.rules = rules
        self.name = name
        self.every = min(r.every for r in rules)
        self.requires_base = any(getattr(r, "requires_base", False) for r in rules)

    def reset(self):
        for r in self.rules:
            r.reset()

    def scale(self, j, ctx):
        return min(r.scale(j, ctx) for r in self.rules)


@dataclass
class Config:
    k: int = 5
    reb: int = 5
    cost: float = 0.0005
    # 非对称成本：ETF 买入只有佣金，卖出可能有赎回费（场外口径）
    buy_cost: float | None = None
    sell_cost: float | None = None
    # 按"每只标的的实际持有天数"分档收赎回费（比按调仓周期一刀切更贴近现实）
    fee_tier: bool = False
    tier_short_days: float = 7.0     # < 7 自然日
    tier_long_days: float = 30.0     # >= 30 自然日
    fee_short: float = 0.015
    fee_mid: float = 0.005
    fee_long: float = 0.0015
    delay: int = 1
    min_names: int = 8
    # 调仓计时语义（2026-11-10 新增，默认 grid 与历史结果一致）：
    #   grid            = 从回测起点按 (i-start) % reb == 0 的固定格
    #   rolling         = 距上次调仓 >= reb 交易日就调，到点即重置（实盘当前行为）
    #   rolling_change  = 同样到点重算，但只有目标真的变了才下单并重置；没变则次日照常再判
    reb_mode: str = "grid"
    reb_change_tol: float = 0.05     # rolling_change 判定"权重变了"的阈值（绝对权重）
    cap: float = 1.0
    stop: float | None = None
    stop_mode: str = "entry"          # entry: 自成交价回撤；ma60: 跌破60日线
    weight_mode: str = "invvol"       # invvol / equal / erc / mdp
    buffer_n: int | None = None       # 缓冲带：持仓仍在前 buffer_n 名内则不换出
    rule: Rule = field(default_factory=Fixed)


# ---------------------------------------------------------------- 回测循环
def simulate(close: pd.DataFrame, f: dict, cfg: Config, keep_weights: bool = False) -> dict:
    T = len(close)
    ret = f["ret"].to_numpy()
    ret0 = np.nan_to_num(ret, nan=0.0)
    na = np.isnan(ret)
    vol_a = f["vol20"].to_numpy()
    rm_a = f["rm"].to_numpy()
    cand_a = f["cand"].to_numpy()
    ma60_a = f["ma60"].to_numpy()
    bench = f["bench"].to_numpy()
    base_ret = f.get("base_ret")
    start = f["start"]

    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_up, lim_dn = ret >= lim * 0.98, ret <= -lim * 0.98

    rule = cfg.rule
    rule.reset()
    base_ret = f.get("base_ret")
    if base_ret is None and getattr(rule, "requires_base", False):
        base_ret = base_returns(close, f, cfg)
    w: dict[int, float] = {}
    s_target = 1.0
    entry_px: dict[int, float] = {}
    entry_i: dict[int, int] = {}
    sched: dict[int, tuple] = {}

    port = np.zeros(T)
    gross_a = np.zeros(T)
    cost_a = np.zeros(T)
    turn_a = np.zeros(T)
    expo_a = np.zeros(T)
    scale_a = np.ones(T)
    equity_a = np.ones(T)
    w_hist = []
    # 调仓计时状态（rolling / rolling_change 用）
    last_reb_i = start
    prev_target_names: list = []
    prev_target_w: dict = {}
    n_due = n_skip = 0

    for i in range(T):
        gross = float(sum(wt * ret0[i, c] for c, wt in w.items()))
        cost_today = 0.0
        turn_today = 0.0

        def apply(new_w: dict):
            nonlocal w, cost_today, turn_today, entry_px, entry_i
            bc = cfg.cost if cfg.buy_cost is None else cfg.buy_cost
            sc = cfg.cost if cfg.sell_cost is None else cfg.sell_cost
            cost_step = 0.0
            turn_step = 0.0
            for c in set(w) | set(new_w):
                d = new_w.get(c, 0.0) - w.get(c, 0.0)
                if d > 0:
                    cost_step += bc * d
                    turn_step += d
                elif d < 0:
                    fee = sc
                    if cfg.fee_tier:
                        # 该标的实际持有自然日 ≈ 交易日数 × 7/5
                        held_cal = (i - entry_i.get(c, i)) * 7.0 / 5.0
                        if held_cal < cfg.tier_short_days:
                            fee = bc + cfg.fee_short
                        elif held_cal < cfg.tier_long_days:
                            fee = bc + cfg.fee_mid
                        else:
                            fee = bc + cfg.fee_long
                    cost_step += fee * (-d)
                    turn_step += (-d)
            if turn_step > 0:
                cost_today += cost_step
                turn_today += turn_step
            w = new_w
            entry_px, entry_i = _update_entries(entry_px, entry_i, w, i, close)

        # 1) 调仓（信号来自 delay 天前）
        if i in sched:
            target, vrow = sched.pop(i)
            tset = set(target)
            buy = [c for c in target if not lim_up[i, c] and not na[i, c]]
            stuck = [c for c in w if c not in tset and (lim_dn[i, c] or na[i, c])]
            names = buy + [c for c in stuck if c not in buy]

            j = max(0, i - 1)
            ctx = {"rets": port, "equity": equity_a, "bench": bench, "base_rets": base_ret,
                   "start": start}
            s_new = float(np.clip(rule.scale(j, ctx), 0.0, 1.0))
            base = _scheme_weights(cfg.weight_mode, names, i, close, f, cfg.cap, vrow)
            apply({c: x * s_new for c, x in base.items()})
            s_target = s_new

        # 2) 定期/每日敞口调整（截至 i-1 的数据，收盘成交）
        if i >= start + cfg.delay and (i - start - cfg.delay) % max(rule.every, 1) == 0:
            j = max(0, i - 1)
            ctx = {"rets": port, "equity": equity_a, "bench": bench, "base_rets": base_ret,
                   "start": start}
            s_new = float(np.clip(rule.scale(j, ctx), 0.0, 1.0))
            if abs(s_new - s_target) > 1e-9 and s_target > 1e-9:
                apply({c: x * (s_new / s_target) for c, x in w.items()})
            s_target = s_new
        scale_a[i] = s_target

        # 3) 止损（收盘触发、收盘卖；跌停/停牌当天卖不掉）
        if cfg.stop is not None and w:
            to_sell = []
            for c in list(w):
                if i <= entry_i.get(c, i):
                    continue
                px = close.iat[i, c]
                if not np.isfinite(px) or lim_dn[i, c]:
                    continue
                if cfg.stop_mode == "entry":
                    ep = entry_px.get(c)
                    if ep is not None and np.isfinite(ep) and px / ep - 1.0 <= -cfg.stop:
                        to_sell.append(c)
                elif cfg.stop_mode == "ma60":
                    m = ma60_a[i, c]
                    if np.isfinite(m) and px < m:
                        to_sell.append(c)
            if to_sell:
                apply({c: x for c, x in w.items() if c not in to_sell})

        expo_a[i] = float(sum(w.values()))
        port[i] = gross - cost_today
        gross_a[i] = gross
        cost_a[i] = cost_today
        turn_a[i] = turn_today
        equity_a[i] = (equity_a[i - 1] if i > 0 else 1.0) * (1.0 + port[i])
        if keep_weights:
            w_hist.append(dict(w))

        # 生成下一次调仓计划（当日信号，delay 天后收盘执行）
        if i >= start:
            if cfg.reb_mode == "grid":
                due = ((i - start) % cfg.reb == 0)
            else:
                due = (i - last_reb_i) >= cfg.reb
            if due:
                n_due += 1
                ok = np.flatnonzero(cand_a[i] & ~np.isnan(rm_a[i]))
                order = [int(c) for c in ok[np.argsort(-rm_a[i, ok])]]
                if cfg.buffer_n and w:
                    keep = [c for c in w if c in set(order[: cfg.buffer_n])]
                    target = keep[: cfg.k]
                    for c in order:
                        if len(target) >= cfg.k:
                            break
                        if c not in target:
                            target.append(c)
                else:
                    target = order[: cfg.k]
                do_reb = True
                if cfg.reb_mode == "rolling_change" and target == prev_target_names:
                    new_w = _scheme_weights(cfg.weight_mode, target, i, close, f, cfg.cap, vol_a[i])
                    do_reb = any(abs(prev_target_w.get(c, 0.0) - new_w.get(c, 0.0)) > cfg.reb_change_tol
                                 for c in set(prev_target_w) | set(new_w))
                if do_reb:
                    sched[i + cfg.delay] = (target, vol_a[i].copy())
                    prev_target_names = list(target)
                    prev_target_w = _scheme_weights(cfg.weight_mode, target, i, close, f, cfg.cap, vol_a[i])
                    if cfg.reb_mode != "grid":
                        last_reb_i = i
                else:
                    n_skip += 1
                    if cfg.reb_mode == "rolling":
                        last_reb_i = i

    return {"ret": port, "gross": gross_a, "cost": cost_a, "turn": turn_a,
            "expo": expo_a, "scale": scale_a, "start": start,
            "n_due": n_due, "n_skip": n_skip,
            "w_hist": w_hist, "dates": close.index, "equity": equity_a}


def base_returns(close: pd.DataFrame, f: dict, cfg: Config) -> np.ndarray:
    """重建"未加仓控"的 base 收益：同一 reb / 成本口径 / 权重方案，只用 Fixed(1)。

    VolTarget 必须用这条序列估波动；否则会用已缩放收益，造成 sqrt 反馈。
    """
    base_cfg = replace(cfg, rule=Fixed(1.0), stop=None)
    f2 = dict(f)
    f2.pop("base_ret", None)
    return simulate(close, f2, base_cfg)["ret"]


def run_with_base(close: pd.DataFrame, f: dict, cfg: Config, **kwargs) -> dict:
    """显式重建 base_ret 后再跑；供外部脚本使用。"""
    f2 = dict(f)
    f2["base_ret"] = base_returns(close, f, cfg)
    return simulate(close, f2, cfg, **kwargs)


# ---------------------------------------------------------------- 评价
def perf(r: np.ndarray) -> dict:
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    if len(r) < 20:
        return {"cagr": np.nan, "vol": np.nan, "sharpe": np.nan, "mdd": np.nan,
                "calmar": np.nan, "n": len(r)}
    eq = np.cumprod(1 + r)
    years = len(r) / TD
    cagr = eq[-1] ** (1 / years) - 1 if eq[-1] > 0 else -1.0
    vol = float(np.std(r, ddof=1) * np.sqrt(TD))
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    return {"cagr": float(cagr), "vol": vol,
            "sharpe": float(np.mean(r) * TD / vol) if vol > 0 else np.nan,
            "mdd": dd, "calmar": float(cagr / abs(dd)) if dd < 0 else np.nan, "n": len(r)}


def slice_metrics(res: dict, lo: int, hi: int) -> dict:
    m = perf(res["ret"][lo:hi])
    days = max(hi - lo, 1)
    m["turn_two_way"] = float(np.sum(res["turn"][lo:hi]) / (days / TD))
    m["cost_drag"] = float(np.sum(res["cost"][lo:hi]) / (days / TD))
    m["avg_expo"] = float(np.mean(res["expo"][lo:hi]))
    return m


def yearly_table(res: dict, dates: pd.DatetimeIndex, lo: int) -> pd.DataFrame:
    rows = []
    ret = pd.Series(res["ret"][lo:], index=dates[lo:])
    turn = pd.Series(res["turn"][lo:], index=dates[lo:])
    for y, g in ret.groupby(ret.index.year):
        r = g.to_numpy()
        eq = np.cumprod(1 + r)
        dd = float((eq / np.maximum.accumulate(eq) - 1).min())
        rows.append({"year": int(y), "ret": eq[-1] - 1, "mdd": dd,
                     "turn_two_way": float(turn[turn.index.year == y].sum() / (len(r) / TD))})
    return pd.DataFrame(rows)


def monthly(r: np.ndarray, dates: pd.DatetimeIndex, lo: int) -> np.ndarray:
    s = pd.Series(r[lo:], index=dates[lo:])
    return (1 + s).groupby([s.index.year, s.index.month]).prod().to_numpy() - 1


def stationary_bootstrap_ci(a: np.ndarray, b: np.ndarray, n_boot=2000, p=1 / 6, seed=0):
    """月度收益 stationary bootstrap：Sharpe(a)-Sharpe(b) 与 CAGR(a)-CAGR(b) 的 90% 区间。"""
    rng = np.random.default_rng(seed)
    n = len(a)
    if n < 12:
        return {"sharpe_lo": np.nan, "sharpe_med": np.nan, "sharpe_hi": np.nan,
                "cagr_lo": np.nan, "cagr_med": np.nan, "cagr_hi": np.nan}
    ds, dc = [], []
    for _ in range(n_boot):
        idx, i = [], int(rng.integers(n))
        while len(idx) < n:
            idx.append(i)
            i = int(rng.integers(n)) if rng.random() < p else (i + 1) % n
        idx = np.array(idx)
        aa, bb = a[idx], b[idx]
        sa = aa.mean() / aa.std(ddof=1) if aa.std(ddof=1) > 0 else np.nan
        sb = bb.mean() / bb.std(ddof=1) if bb.std(ddof=1) > 0 else np.nan
        ds.append(sa - sb)
        dc.append((1 + aa).prod() ** (12 / n) - (1 + bb).prod() ** (12 / n))
    ds, dc = np.array(ds), np.array(dc)
    return {"sharpe_lo": float(np.nanquantile(ds, 0.05)), "sharpe_med": float(np.nanquantile(ds, 0.50)),
            "sharpe_hi": float(np.nanquantile(ds, 0.95)),
            "cagr_lo": float(np.nanquantile(dc, 0.05)), "cagr_med": float(np.nanquantile(dc, 0.50)),
            "cagr_hi": float(np.nanquantile(dc, 0.95))}
