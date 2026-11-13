"""
综合机制回测 v3（P0 修补版）
相对上一版的改动：
 1. 规则事件研究：基准改为"当日同池截面均值"（全样本与分段都用同一口径），不再减全样本总均值
 2. t 值：方向性（正=规则方向正确）+ Newey-West(滞后=持有天数) + 不重叠样本 t；cooldown 作为不重叠最小间隔
 3. 数据诊断：失败名单、指数替代、成交额缺失率、每日有效标的数；价格类规则不再因成交额缺失被剔除
 4. R4 回到冻结组；探索组命名与内容一致；删除重复的"纯动量_无量能"
"""
import argparse
import json
import os
import numpy as np
import pandas as pd

from macd_structure import ETF_POOL, calc_macd, structure_series
from pools import get_pool, load_one

HORIZONS = (5, 10, 20)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT, "data")


def demo_panel(n_codes=24, n=1600, seed=0, momentum=0.0, regime=False):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-06-03", periods=n)
    mkt = rng.normal(0.0002, 0.010, n)
    if regime:  # 牛-熊-牛：只有市场整体漂移，没有任何个股动量
        a, b = int(n * 0.5), int(n * 0.75)
        mkt[:a] += 0.0010
        mkt[a:b] -= 0.0020
        mkt[b:] += 0.0015
    data = {}
    for i in range(n_codes):
        drift = np.zeros(n)
        for t in range(1, n):
            drift[t] = 0.98 * drift[t - 1] + rng.normal(0, 0.0004)
        r = 0.8 * mkt + momentum * drift + rng.normal(0, 0.012, n)
        data[f"demo{i:02d}"] = pd.DataFrame({
            "date": dates, "close": 100 * np.exp(np.cumsum(r)),
            "amount": np.exp(8 + rng.normal(0, 0.35, n) + 20 * np.abs(r))})
    return data


def post_list_missing(close, amt):
    """成交额缺失率：只统计各标的上市（首个有价格日）之后，避免把上市前的空值算进去"""
    out = {}
    for c in close.columns:
        fv = close[c].first_valid_index()
        s = amt[c].loc[fv:] if fv is not None else amt[c].iloc[0:0]
        out[c] = float(s.isna().mean() * 100) if len(s) else np.nan
    return pd.Series(out)


def build_panel(data, min_len=250, exclude=()):
    closes, amts = {}, {}
    for code, df in data.items():
        if code in exclude or len(df) < min_len: continue
        s = df.drop_duplicates("date").set_index("date").sort_index()
        closes[code] = s["close"].astype(float)
        amts[code] = pd.to_numeric(s["amount"], errors="coerce")

    close = pd.DataFrame(closes).sort_index()
    amt = pd.DataFrame(amts).reindex(close.index)
    amt = amt.where(amt > 0)  # 成交额<=0 视为缺失（停牌/脏数据），不参与量能计算

    missing_report = post_list_missing(close, amt)
    high_missing = missing_report[missing_report > 20]
    if not high_missing.empty:
        print("\n[警告] 以下标的上市后成交额缺失率 > 20%（量能类规则将自动排除这些标的）：")
        for code, rate in high_missing.items():
            print(f"  {code}: {rate:.1f}%")
    return close, amt


def rsi(close, n=6):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def build_features(close, amt, lookback):
    f = {"close": close}
    for n in (5, 20, 60, 120):
        f[f"mom{n}"] = close / close.shift(n) - 1
    for n in (20, 60, 120):
        f[f"ma{n}"] = close.rolling(n, min_periods=n).mean()

    ret = close.pct_change(fill_method=None)
    f["ret"] = ret
    f["vol20"] = ret.rolling(20, min_periods=15).std()

    f["vol_ratio"] = amt / amt.shift(1).rolling(20, min_periods=15).mean()
    f["crowd"] = amt.rolling(60, min_periods=40).apply(lambda x: (x <= x[-1]).mean() * 100, raw=True)
    f["rsi6"] = rsi(close, 6)
    f["hh20"] = close.shift(1).rolling(20, min_periods=20).max()

    flags = {k: pd.DataFrame(False, index=close.index, columns=close.columns)
             for k in ("top_flag", "top_struct", "bot_flag", "bot_struct")}
    for code in close.columns:
        s = close[code].dropna()
        if len(s) < 150: continue
        c = s.values
        dif, _ = calc_macd(c)
        for side, pre in (("top", "top"), ("bottom", "bot")):
            st = structure_series(c, dif, side, lookback=lookback)
            states = np.array([x[0] for x in st])
            flags[pre + "_flag"].loc[s.index, code] = np.isin(states, ["钝化", "结构形成"])
            flags[pre + "_struct"].loc[s.index, code] = states == "结构形成"
    f.update(flags)

    bars = close.notna().cumsum()
    # valid：只要价格类特征可用（动量/波动）。成交额缺失不再剔除标的
    f["valid"] = ((bars >= 150) & close.notna() & f["mom120"].notna() & f["vol20"].notna())
    # valid_vol：量能/拥挤度类规则额外要求成交额特征可用
    f["valid_vol"] = f["valid"] & f["vol_ratio"].notna() & f["crowd"].notna()
    return f


def xs_rank(df, valid, min_names):
    r = df.where(valid).rank(axis=1, pct=True)
    enough = (valid.sum(axis=1) >= min_names).to_numpy()
    return r.where(np.repeat(enough[:, None], df.shape[1], axis=1))


def event_idx(col, cooldown):
    idx = np.flatnonzero(col)
    if cooldown <= 0: return idx
    out, last = [], -10**9
    for t in idx:
        if t > 0 and col[t - 1]: continue
        if t - last >= cooldown:
            out.append(t)
            last = t
    return np.asarray(out, dtype=int)


def make_rules(f, rk):
    """每条规则: (分组, 名称, 触发mask, 方向+1买/-1回避, 宇宙 px=价格类 / vol=需成交额)"""
    c, vr, cr = f["close"], f["vol_ratio"], f["crowd"]
    rm = (rk["mom20"] + rk["mom60"] + rk["mom120"]) / 3
    up20, up60 = c > f["ma20"], c > f["ma60"]

    return [
        # 冻结组
        ("冻结", "R1 强动量+放量(>1.2)+站上20日线", (rm >= 0.7) & (vr > 1.2) & up20, +1, "vol"),
        ("冻结", "R2 强动量+缩量(<0.8)", (rm >= 0.7) & (vr < 0.8), -1, "vol"),
        # 注意：原 R4 阈值没带回，这里沿用 R2 的缩量口径(<0.8)；如有原文件请核对
        ("冻结", "R4 弱动量+缩量(<0.8)", (rm <= 0.3) & (vr < 0.8), -1, "vol"),
        ("冻结", "R5 拥挤>85", cr > 85, -1, "vol"),
        ("冻结", "R7 顶部结构形成", f["top_struct"], -1, "px"),
        ("冻结", "R8 顶部钝化/结构", f["top_flag"], -1, "px"),
        ("冻结", "R9 底部结构形成", f["bot_struct"], +1, "px"),
        ("冻结", "R10 20日新高突破+站上60日线+放量", (c >= f["hh20"]) & up60 & (vr > 1.2), +1, "vol"),
        ("冻结", "R11 RSI6<20（无趋势过滤）", f["rsi6"] < 20, +1, "px"),
        ("冻结", "R13 跌破20日线+放量(>1.5)", (~up20) & (vr > 1.5), -1, "vol"),
        # 探索组（消融与对照）
        ("探索", "R1_no_vol 强动量+站上20日线(无量)", (rm >= 0.7) & up20, +1, "px"),
        ("探索", "R2_no_vol 强动量(无量)", rm >= 0.7, -1, "px"),
        ("探索", "R10_no_vol 20日新高突破+站上60日线(无量)", (c >= f["hh20"]) & up60, +1, "px"),
        ("探索", "R4_no_vol 弱动量(无量)", rm <= 0.3, -1, "px"),
        ("探索", "R4b 弱动量+更缩量(<0.7)", (rm <= 0.3) & (vr < 0.7), -1, "vol"),
    ]


def nw_se(idx, x, T, lag):
    """触发日序列均值的 Newey-West 标准误（Bartlett 核，按真实日历间隔计滞后）"""
    n = len(x)
    y = np.zeros(T)
    y[idx] = x - x.mean()
    s = float((y * y).sum())
    for l in range(1, lag + 1):
        s += 2 * (1 - l / (lag + 1)) * float((y[:-l] * y[l:]).sum())
    return np.sqrt(max(s, 0.0)) / n


def thin(idx, gap):
    out, last = [], -10**9
    for t in idx:
        if t - last >= gap:
            out.append(t)
            last = t
    return np.asarray(out, dtype=int)


def eval_rules(rules, f, dates, seg, delay, cooldown, start):
    close = f["close"]
    T = len(dates)
    vpx, vvol = f["valid"].to_numpy(), f["valid_vol"].to_numpy()
    pre = np.arange(T) < start
    rows = []

    for h in HORIZONS:
        Fv = (close.shift(-(h + delay)) / close.shift(-delay) - 1).to_numpy()
        fin = np.isfinite(Fv)
        ok = {"px": vpx & fin, "vol": vvol & fin}
        bench = {}
        for u, o in ok.items():
            cnt = o.sum(1)
            ssum = np.where(o, Fv, 0.0).sum(1)
            b = np.full(T, np.nan)
            good = (cnt >= 3) & ~pre
            b[good] = ssum[good] / cnt[good]  # 当日同池截面均值（同一宇宙）
            bench[u] = b

        for group, name, mask, sign, u in rules:
            m = mask.fillna(False).to_numpy(dtype=bool) & ok[u]
            m[pre] = False
            cnt = m.sum(1)
            idx = np.flatnonzero((cnt > 0) & ~np.isnan(bench[u]))
            if len(idx) < 10: continue

            trig = np.where(m, Fv, 0.0).sum(1)[idx] / cnt[idx]
            exc = trig - bench[u][idx]  # 当日超额
            se = nw_se(idx, exc, T, h)
            t_dir = sign * exc.mean() / se if se > 0 else np.nan

            full = np.full(T, np.nan)
            full[idx] = exc
            th = thin(idx, max(h, cooldown))
            ex_th = full[th]
            sd_th = ex_th.std(ddof=1) if len(ex_th) > 2 else np.nan
            t_no = sign * ex_th.mean() / (sd_th / np.sqrt(len(ex_th))) if (len(ex_th) >= 8 and sd_th > 0) else np.nan

            sg = seg[idx]
            edges = [sign * exc[sg == s].mean() if (sg == s).any() else np.nan for s in range(3)]
            same = all(e == e and e > 0 for e in edges) and len(idx) >= 30
            robust = same and t_dir == t_dir and t_dir >= 2 and t_no == t_no and t_no >= 1.5

            rows.append({
                "分组": group, "规则": name, "天数": h,
                "预期": "买入" if sign > 0 else "回避", "宇宙": u,
                "触发日数": len(idx), "不重叠n": len(th),
                "平均收益%": round(trig.mean() * 100, 2),
                "方向边际%": round(sign * exc.mean() * 100, 2),
                "NW_t(方向)": round(t_dir, 2) if t_dir == t_dir else np.nan,
                "不重叠t(方向)": round(t_no, 2) if t_no == t_no else np.nan,
                "训练%": round(edges[0] * 100, 2) if edges[0] == edges[0] else np.nan,
                "验证%": round(edges[1] * 100, 2) if edges[1] == edges[1] else np.nan,
                "测试%": round(edges[2] * 100, 2) if edges[2] == edges[2] else np.nan,
                "三段同向": "✓" if same else "", "稳健": "✓" if robust else "",
            })
    return pd.DataFrame(rows)


def l1_traded(old, new):
    return sum(abs(new.get(c, 0.0) - old.get(c, 0.0)) for c in set(old) | set(new))


def run_portfolio(ret, score, cand, vol, lim_up, lim_dn, k, reb, cost, delay, start, invvol=False, gate=None,
                  posmult=None, expo_out=None):
    n, _ = ret.shape
    ret0 = np.nan_to_num(ret, nan=0.0)
    na = np.isnan(ret)
    w, sched, port, turn = {}, {}, np.zeros(n), []
    for i in range(n):
        if posmult is None:
            day = sum(wt * ret0[i, c] for c, wt in w.items())
        else:  # 结构仓位叠加：持仓名按 posmult 缩放；仓位变动按 cost 计费（换仓成本仍按基础权重计，偏保守）
            day = sum(wt * posmult[i, c] * ret0[i, c] for c, wt in w.items())
            if i > 0:
                day -= cost * sum(wt * abs(posmult[i, c] - posmult[i - 1, c]) for c, wt in w.items())
            if expo_out is not None:
                expo_out[i] = sum(wt * posmult[i, c] for c, wt in w.items())
        if i >= start and (i - start) % reb == 0:
            if gate is not None and not gate[i]:
                target = []
            else:
                ok = np.flatnonzero(cand[i] & ~np.isnan(score[i]))
                target = [int(c) for c in ok[np.argsort(-score[i, ok])[:k]]] if len(ok) else []
            sched[i + delay] = (target, vol[i].copy())
        if i in sched:
            target, vrow = sched.pop(i)
            tset = set(target)
            buy = [c for c in target if not lim_up[i, c] and not na[i, c]]
            stuck = [c for c in w if c not in tset and (lim_dn[i, c] or na[i, c])]
            names = buy + [c for c in stuck if c not in buy]
            new_w = {}
            if names:
                if invvol:
                    iv = np.array([1.0 / max(vrow[c], 1e-4) if vrow[c] == vrow[c] else np.nan for c in names])
                    iv = np.where(np.isnan(iv), np.nanmean(iv) if (~np.isnan(iv)).any() else 1.0, iv)
                    wts = iv / iv.sum()
                else:
                    wts = np.full(len(names), 1.0 / len(names))
                new_w = dict(zip(names, wts))
            traded = l1_traded(w, new_w)
            day -= cost * traded
            turn.append(traded)
            w = new_w
        port[i] = day
    return port, (np.mean(turn) if turn else 0.0)


def perf(r):
    r = np.asarray(r, float)
    if len(r) < 20: return dict(年化=np.nan, 波动=np.nan, 夏普=np.nan, 回撤=np.nan, 卡玛=np.nan)
    eq = np.cumprod(1 + r)
    ann = eq[-1] ** (252 / len(r)) - 1 if eq[-1] > 0 else -1.0
    vol = r.std(ddof=1) * np.sqrt(252)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return dict(年化=ann, 波动=vol, 夏普=r.mean() * 252 / vol if vol > 0 else np.nan, 回撤=dd,
                卡玛=ann / abs(dd) if dd < 0 else np.nan)


# 已删除重复的"纯动量_无量能"（纯动量本来就不含量能，两行完全相同）；
# 新增"动量+趋势+顶部"用来把逆波动加权的贡献单独拆出来
VARIANTS = [
    ("纯动量", dict(mom=1)),
    ("动量+趋势过滤", dict(mom=1, trend=True)),
    ("动量+趋势+逆波动", dict(mom=1, trend=True, invvol=True)),
    ("动量+量能", dict(mom=1, vol=1)),
    ("动量+量能+低拥挤", dict(mom=1, vol=1, crowd=-1)),
    ("动量+趋势+顶部", dict(mom=1, trend=True, topf=True)),
    ("动量+趋势+顶部+逆波动", dict(mom=1, trend=True, topf=True, invvol=True)),
]


def pct(x): return "" if x != x else f"{x * 100:.1f}%"


def data_report(close, amt, f, start, failed, idx_names, meta, names):
    """数据诊断：打印并写入 meta"""
    n_px = f["valid"].sum(axis=1).to_numpy()[start:]
    n_vol = f["valid_vol"].sum(axis=1).to_numpy()[start:]
    miss = post_list_missing(close, amt).round(1)
    first = {c: str(close[c].first_valid_index().date()) for c in close.columns}
    print("=== 数据诊断 ===")
    print(f"进入面板: {close.shape[1]} 只 | 拉取失败: {len(failed)} 只 | 指数替代(拉不到ETF时): {len(idx_names)} 只")
    print(f"每日有效标的数(价格类): 中位 {int(np.median(n_px))} / 最少 {int(n_px.min())} / 最多 {int(n_px.max())}")
    print(f"每日有效标的数(含量能): 中位 {int(np.median(n_vol))} / 最少 {int(n_vol.min())} / 最多 {int(n_vol.max())}")
    for name, msg in failed:
        print(f"  失败 {name}: {msg}")
    if idx_names:
        print(f"  指数替代(无成交额，价格类规则可用，量能类不可用): {', '.join(idx_names)}")
    bad = miss[miss > 5]
    if len(bad):
        print("  上市后成交额缺失率>5%: " + ", ".join(f"{names.get(c, c)}={v}%" for c, v in bad.items()))
    late = sorted(first.items(), key=lambda kv: kv[1])[-5:]
    print("  上市最晚的5只: " + ", ".join(f"{names.get(c, c)}({d})" for c, d in late))
    meta.update({
        "panel_count": int(close.shape[1]),
        "failed": [{"name": n, "err": m} for n, m in failed],
        "index_fallback": list(idx_names),
        "valid_px_daily": {"median": int(np.median(n_px)), "min": int(n_px.min()), "max": int(n_px.max())},
        "valid_vol_daily": {"median": int(np.median(n_vol)), "min": int(n_vol.min()), "max": int(n_vol.max())},
        "amount_missing_pct": {c: float(v) for c, v in miss.items() if v > 0},
        "first_date": first,
    })


def struct_positions(ts, tf, bs, bf, b=0.6, cut=0.4, add=0.2, recall_n=2, hold=20, use_top=True, use_bot=True):
    """单只仓位状态机（信号在收盘产生，成交延迟由调用方按 delay 位移）。
    顶部结构形成(边沿) -> 仓位 -cut；底部结构形成(边沿) -> 仓位 +add；叠加后限制在[0,1]。
    撤回：事件后 recall_n 天内，对应的钝化/结构标记消失(失效) -> 撤销该次调整；recall_n=0 表示不撤回。
    到期：调整持续 hold 天后自动失效（回到基础仓位）。"""
    T, N = ts.shape
    pos = np.full((T, N), float(b))
    for j in range(N):
        events = []  # [t0, delta, kind]  kind 0=顶部 1=底部
        for t in range(T):
            if events:
                keep = []
                for ev in events:
                    age = t - ev[0]
                    if age > hold: continue
                    if 0 < age <= recall_n and not (tf[t, j] if ev[2] == 0 else bf[t, j]): continue
                    keep.append(ev)
                events = keep
            if use_top and ts[t, j] and not (t > 0 and ts[t - 1, j]): events.append((t, -cut, 0))
            if use_bot and bs[t, j] and not (t > 0 and bs[t - 1, j]): events.append((t, add, 1))
            if events:
                pos[t, j] = min(1.0, max(0.0, b + sum(e[1] for e in events)))
    return pos


def shift_states(arrs, rng, min_off=250):
    """对照：每只标的的结构状态做循环位移（保留结构自身的形态与持续性，打破与收益的对应关系）"""
    T, N = arrs[0].shape
    offs = rng.integers(min_off, max(min_off + 1, T - min_off), N)
    return [np.stack([np.roll(a[:, j], offs[j]) for j in range(N)], axis=1) for a in arrs]


def overlay_ew(ret0, valid, pe, start, cost):
    """等权池 × 单只仓位叠加：返回(日收益, 平均仓位序列)。成本按仓位变动计。"""
    T, N = ret0.shape
    v = valid.astype(float)
    nv = np.maximum(v.sum(1), 1.0)
    pe = pe * v
    pe[:start] = 0.0
    gross = (pe * ret0).sum(1) / nv
    dpos = np.abs(np.diff(pe, axis=0, prepend=np.zeros((1, N)))).sum(1) / nv
    return gross - cost * dpos, pe.sum(1) / nv


A_VARIANTS = [
    ("结构分档 撤回0日(不撤回)", dict(recall_n=0)),
    ("结构分档 撤回1日", dict(recall_n=1)),
    ("结构分档 撤回2日", dict(recall_n=2)),
    ("结构分档 撤回3日", dict(recall_n=3)),
    ("仅顶部减仓 撤回2日", dict(recall_n=2, use_bot=False)),
    ("仅底部加仓 撤回2日", dict(recall_n=2, use_top=False)),
    ("结构分档 撤回2日 持有60日", dict(recall_n=2, hold=60)),
]
B_VARIANTS = [
    ("顶部减仓 不撤回", dict(recall_n=0, use_bot=False)),
    ("顶部减仓 撤回2日", dict(recall_n=2, use_bot=False)),
    ("结构分档 撤回2日(底部只恢复)", dict(recall_n=2)),
]
BASE_OPT = dict(mom=1, trend=True)  # B 部分的基线：动量+趋势过滤


VM_BASE_OPT = dict(mom=1, trend=True, invvol=True)


def volmanage(p, start, target, window, cap, cost, delay):
    """波动率管理（Daniel-Moskowitz 思路）：用策略自身已实现波动把总仓位缩放到目标波动，不加杠杆(cap=1)。
    波动只用 t-delay-1 及之前的数据；仓位变动按 cost 计费；剩余部分为现金（不计息）。"""
    q = pd.Series(p[start:])
    sig = (q.rolling(window, min_periods=max(20, window // 2)).std().shift(delay + 1) * np.sqrt(252)).to_numpy()
    sc = np.where(np.isfinite(sig) & (sig > 1e-6), np.minimum(cap, target / sig), 1.0)
    ds = np.abs(np.diff(sc, prepend=sc[0]))
    out = np.zeros(len(p)); out[start:] = sc * p[start:] - cost * ds
    scale = np.zeros(len(p)); scale[start:] = sc
    return out, scale


def volmanaged_section(simulate, a, start, live):
    p0, _ = simulate(VM_BASE_OPT)
    m0 = perf(p0[live])
    rows = [{"机制": "动量+趋势+逆波动(基线)", "年化": pct(m0["年化"]), "回撤": pct(m0["回撤"]), "夏普": round(m0["夏普"], 2),
             "卡玛": round(m0["卡玛"], 2), "实现波动": pct(m0["波动"]), "平均仓位": "", "卡玛-固定同仓位": ""}]
    for tv in [float(x) for x in a.vol_targets.split(",") if x.strip()]:
        p, sc = volmanage(p0, start, tv, a.vol_window, 1.0, a.cost, a.delay)
        m = perf(p[live])
        c = float(sc[live].mean())
        fixed = perf((c * p0)[live])  # 同平均仓位的固定缩放（现金不计息）
        rows.append({"机制": f"波控 目标{tv:.0%} 窗口{a.vol_window}日", "年化": pct(m["年化"]), "回撤": pct(m["回撤"]),
                     "夏普": round(m["夏普"], 2), "卡玛": round(m["卡玛"], 2), "实现波动": pct(m["波动"]),
                     "平均仓位": f"{c:.0%}", "卡玛-固定同仓位": round(m["卡玛"] - fixed["卡玛"], 2)})
    df = pd.DataFrame(rows)
    print("\n" + "=" * 30 + " B2. 波动率管理（基线=动量+趋势+逆波动）" + "=" * 30)
    print(df.to_string(index=False))
    print("说明：'卡玛-固定同仓位'>0 才说明动态缩放优于同平均仓位的固定缩放；仓位上限100%，不加杠杆。")
    if not a.demo:
        df.to_csv("composite_volmanaged.csv", index=False, encoding="utf-8-sig")


def struct_section(f, ret, bench, simulate, a, start, live):
    ts, tf = f["top_struct"].to_numpy(bool), f["top_flag"].to_numpy(bool)
    bs, bf = f["bot_struct"].to_numpy(bool), f["bot_flag"].to_numpy(bool)
    T, N = ts.shape
    d = a.delay + 1  # 收盘出信号 -> delay 天后收盘成交 -> 次日起生效
    ret0 = np.nan_to_num(ret, nan=0.0)
    vmask = f["valid"].to_numpy()
    rng = np.random.default_rng(7)

    def eff(pos, b):
        e = np.full_like(pos, float(b))
        e[d:] = pos[:-d]
        return e

    def run_A(states, **kw):
        return overlay_ew(ret0, vmask, eff(struct_positions(*states, **kw), kw["b"]), start, a.cost)

    def run_B(states, **kw):
        expo = np.zeros(T)
        p, _ = simulate(BASE_OPT, posmult=eff(struct_positions(*states, **kw), kw["b"]), expo=expo)
        return p, expo

    def evaluate(title, runner, variants, b, base_series, base_label, base_scale=1.0):
        mb = perf((base_scale * base_series)[live])
        rows = [{"机制": base_label, "年化": pct(mb["年化"]), "回撤": pct(mb["回撤"]), "夏普": round(mb["夏普"], 2),
                 "卡玛": round(mb["卡玛"], 2), "平均仓位": "", "卡玛-固定同仓位": "", "胜过移位随机(卡玛)": "", "胜过移位随机(夏普)": ""}]
        for name, kw in variants:
            full = dict(b=b, cut=a.cut, add=a.add, recall_n=a.recall, hold=a.hold, use_top=True, use_bot=True)
            full.update(kw)
            p, expo = runner((ts, tf, bs, bf), **full)
            m = perf(p[live])
            c = float(expo[live].mean())
            fixed = perf((c * base_series)[live])  # 同平均仓位的固定仓位（现金不计息）
            cal, sh = [], []
            for _ in range(a.n_struct_random):
                p2, _e = runner(shift_states([ts, tf, bs, bf], rng), **full)
                m2 = perf(p2[live])
                cal.append(m2["卡玛"]); sh.append(m2["夏普"])
            cal, sh = np.array(cal, float), np.array(sh, float)
            rows.append({"机制": name, "年化": pct(m["年化"]), "回撤": pct(m["回撤"]), "夏普": round(m["夏普"], 2),
                         "卡玛": round(m["卡玛"], 2), "平均仓位": f"{c:.0%}",
                         "卡玛-固定同仓位": round(m["卡玛"] - fixed["卡玛"], 2),
                         "胜过移位随机(卡玛)": pct(np.nanmean(cal < m["卡玛"])),
                         "胜过移位随机(夏普)": pct(np.nanmean(sh < m["夏普"]))})
        print("\n" + "=" * 30 + f" {title}（移位随机 {a.n_struct_random} 次）" + "=" * 30)
        df = pd.DataFrame(rows)
        print(df.to_string(index=False))
        return df

    # A. 等权池 + 单只结构仓位（基础仓位 struct_base）
    dfA = evaluate("C1. 等权池×结构分档仓位", run_A, A_VARIANTS, a.struct_base, bench, f"固定{a.struct_base:.0%}仓(基线,等权池)",
                  base_scale=a.struct_base)
    # B. 动量+趋势过滤 持仓上叠加结构减仓
    p_base, _ = simulate(BASE_OPT)
    dfB = evaluate("C2. 动量+趋势过滤 持仓×结构减仓", run_B, B_VARIANTS, 1.0, p_base, "动量+趋势过滤(基线)")
    print("\n说明：'卡玛-固定同仓位'为相同平均仓位下的固定仓位对照（现金不计息）；"
          "'胜过移位随机'为把每只标的的结构状态循环位移后的分位数，≥95% 才算有择时价值。")
    if not a.demo:
        pd.concat([dfA.assign(部分="C1"), dfB.assign(部分="C2")]).to_csv(
            "composite_structure_overlay.csv", index=False, encoding="utf-8-sig")


def main():
    try:  # GBK 控制台打印 ✓ 会崩：不可编码字符替换成 ?，不改变原编码
        import sys
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="使用随机游走假数据")
    ap.add_argument("--demo-momentum", type=float, default=0.0, help="假数据植入动量系数")
    ap.add_argument("--demo-regime", action="store_true", help="假数据加入牛-熊-牛市场漂移（无个股动量），用于检验分段口径")
    ap.add_argument("--start", default="20190101")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--reb", type=int, default=5)
    ap.add_argument("--cost", type=float, default=0.0005)
    ap.add_argument("--delay", type=int, default=1)
    ap.add_argument("--cooldown", type=int, default=10, help="不重叠样本的最小间隔（取 max(持有天数, cooldown)）")
    ap.add_argument("--min-names", type=int, default=8)
    ap.add_argument("--lookback", type=int, default=120)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--n-random", type=int, default=200, help="随机基线抽样次数")
    ap.add_argument("--pool", default="ext", help="标的池：base/ext/long/stocks，可用+组合，如 long+stocks")
    ap.add_argument("--vol-targets", default="0.12,0.15,0.20", help="波动率管理目标年化波动，逗号分隔")
    ap.add_argument("--vol-window", type=int, default=60, help="波动率管理的已实现波动窗口(日)")
    ap.add_argument("--skip-struct", action="store_true", help="跳过 C 部分(结构分档仓位变体)")
    ap.add_argument("--n-struct-random", type=int, default=50, help="结构变体的移位随机次数")
    ap.add_argument("--struct-base", type=float, default=0.6, help="C1 基础仓位")
    ap.add_argument("--cut", type=float, default=0.4, help="顶部结构形成减仓幅度")
    ap.add_argument("--add", type=float, default=0.2, help="底部结构形成加仓幅度")
    ap.add_argument("--recall", type=int, default=2, help="结构失效撤回窗口(天)")
    ap.add_argument("--hold", type=int, default=20, help="结构调整持续天数")
    ap.add_argument("--cache", default="", help="缓存目录；默认按 --start 自动选 data/cache2019 或 data/cache2007")
    a = ap.parse_args()

    cache_dir = a.cache or os.path.join(DATA_DIR, "cache2007" if a.start <= "20070101" else "cache2019")
    print(f"缓存目录: {cache_dir}")

    data, failed, idx_names, names = {}, [], [], {}
    if a.demo:
        data = demo_panel(momentum=a.demo_momentum, regime=a.demo_regime)
        print(f"【演示模式】生成假数据，植入动量系数 {a.demo_momentum}，牛熊漂移={a.demo_regime}")
    else:
        pool = get_pool(a.pool, ETF_POOL)
        names = {c: n for c, (n, k) in pool.items()}
        kinds = [k for _, (n, k) in pool.items()]
        print(f"标的池 {a.pool}: 候选 {len(pool)} 只（ETF {kinds.count('etf')} / 指数 {kinds.count('index')} / 股票 {kinds.count('stock')}）")
        for code, (name, kind) in pool.items():
            try:
                df, is_index = load_one(code, kind, a.start, cache_dir)
                data[code] = df
                if is_index:
                    idx_names.append(name)
            except Exception as e:
                failed.append((name, f"{code} {str(e)[:100]}"))

        print(f"\n=== 数据加载汇报 ===")
        print(f"成功: {len(data)} 只 | 失败: {len(failed)} 只")
        for name, msg in failed:
            print(f"  失败 {name}: {msg}")

        if len(data) < 8:
            print("可用数据少于 8 只，终止执行。")
            return

    close, amt = build_panel(data, exclude=set(filter(None, a.exclude.split(","))))
    f = build_features(close, amt, a.lookback)
    valid = f["valid"]
    dates = close.index

    rk = {k: xs_rank(f[k], valid, a.min_names) for k in ("mom5", "mom20", "mom60", "mom120")}
    rk["vol_ratio"] = xs_rank(f["vol_ratio"], f["valid_vol"], a.min_names)
    rk["crowd"] = xs_rank(f["crowd"], f["valid_vol"], a.min_names)
    rm = (rk["mom20"] + rk["mom60"] + rk["mom120"]) / 3
    enough = rm.notna().any(axis=1).to_numpy()
    if not enough.any(): return

    start = int(np.argmax(enough))
    seg = np.zeros(len(dates), dtype=int)
    n_live = len(dates) - start
    seg[start + int(n_live * 0.5):] = 1
    seg[start + int(n_live * 0.75):] = 2

    meta = {
        "timestamp": str(dates[-1].date()),
        "data_end": str(dates[-1].date()),
        "params": vars(a),
        "pool": a.pool,
        "etf_count": int(close.shape[1]),
        "failed_count": len(failed),
        "split_dates": {
            "train": [str(dates[start].date()), str(dates[start + int(n_live * 0.5) - 1].date())],
            "valid": [str(dates[start + int(n_live * 0.5)].date()), str(dates[start + int(n_live * 0.75) - 1].date())],
            "test": [str(dates[start + int(n_live * 0.75)].date()), str(dates[-1].date())],
        },
    }
    data_report(close, amt, f, start, failed, idx_names, meta, names)
    if not a.demo:
        with open("composite_meta.json", "w", encoding="utf-8") as fp:
            json.dump(meta, fp, indent=2, ensure_ascii=False)

    print(f"\n=== 回测环境 ===")
    print(f"数据区间: {dates[0].date()} ~ {dates[-1].date()}")
    print(f"训练段: {meta['split_dates']['train'][0]} ~ {meta['split_dates']['train'][1]} ({(seg[start:] == 0).sum()}天)")
    print(f"验证段: {meta['split_dates']['valid'][0]} ~ {meta['split_dates']['valid'][1]} ({(seg == 1).sum()}天)")
    print(f"测试段: {meta['split_dates']['test'][0]} ~ {meta['split_dates']['test'][1]} ({(seg == 2).sum()}天)\n")

    res = eval_rules(make_rules(f, rk), f, dates, seg, a.delay, a.cooldown, start)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_rows", 300)
    print("=" * 30, "A. 规则事件研究（超额=相对当日同池均值；t 为方向性）", "=" * 30)
    print(f"口径：NW_t 为 Newey-West(滞后=持有天数)；不重叠t 取间隔 max(h,{a.cooldown}) 的样本。")
    print(f"      共 {res['规则'].nunique() * len(HORIZONS) if not res.empty else 0} 个检验，Bonferroni 约需 t≥3；'稳健'仅表示通过初筛(三段同向且NW_t≥2且不重叠t≥1.5)。")
    if not res.empty:
        print(res.to_string(index=False))
        if not a.demo:
            res.to_csv("composite_rules.csv", index=False, encoding="utf-8-sig")

    ret = f["ret"].to_numpy()
    vol = f["vol20"].to_numpy()
    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_up, lim_dn = ret >= lim * 0.98, ret <= -lim * 0.98
    base_cand = (valid & rm.notna()).to_numpy()
    valid_vol = f["valid_vol"].to_numpy()

    bench = f["ret"].where(valid).mean(axis=1).fillna(0).to_numpy().copy()
    bench[:start] = 0.0

    def simulate(opt, score=None, posmult=None, expo=None):
        if score is None:
            rm_fill = np.nan_to_num((rm - 0.5).to_numpy(), nan=0.0)
            vr_fill = np.nan_to_num((rk["vol_ratio"] - 0.5).to_numpy(), nan=0.0) if opt.get("vol") else np.zeros_like(rm_fill)
            cr_fill = np.nan_to_num((rk["crowd"] - 0.5).to_numpy(), nan=0.0) if opt.get("crowd") else np.zeros_like(rm_fill)
            score = (opt.get("mom", 0) * rm_fill + opt.get("vol", 0) * vr_fill + opt.get("crowd", 0) * cr_fill)
        cand = base_cand.copy()
        if opt.get("vol") or opt.get("crowd"): cand &= valid_vol
        if opt.get("trend"): cand &= (close > f["ma60"]).to_numpy()
        if opt.get("topf"): cand &= ~f["top_flag"].to_numpy()
        return run_portfolio(ret, score, cand, vol, lim_up, lim_dn, a.k, a.reb, a.cost, a.delay, start,
                             invvol=opt.get("invvol", False), posmult=posmult, expo_out=expo)

    idx = np.arange(len(dates))
    live = idx >= start

    rng = np.random.default_rng(42)
    rand_raw, rand_filt = [], []
    trend_mask = (close > f["ma60"]).to_numpy()
    for _ in range(a.n_random):
        p_raw, _ = simulate({}, score=rng.random(ret.shape))
        rand_raw.append(perf(p_raw[live])["年化"])
        filt_score = np.where(trend_mask, rng.random(ret.shape), np.nan)  # 趋势外设为 NaN，真正"过滤后再随机"
        p_filt, _ = simulate({}, score=filt_score)
        rand_filt.append(perf(p_filt[live])["年化"])

    rand_raw = np.array(rand_raw)
    rand_filt = np.array(rand_filt)

    rows = []
    b = perf(bench[live])
    rows.append({"机制": "等权基准", "年化": pct(b["年化"]), "回撤": pct(b["回撤"]), "夏普": round(b["夏普"], 2), "胜过纯随机": "", "胜过过滤随机": ""})
    rows.append({"机制": "纯随机_全池", "年化": pct(np.median(rand_raw)), "回撤": "-", "夏普": "-", "胜过纯随机": "", "胜过过滤随机": ""})
    rows.append({"机制": "纯随机_仅趋势", "年化": pct(np.median(rand_filt)), "回撤": "-", "夏普": "-", "胜过纯随机": "", "胜过过滤随机": ""})

    for name, opt in VARIANTS:
        p, _ = simulate(opt)
        m = perf(p[live])
        rows.append({
            "机制": name, "年化": pct(m["年化"]), "回撤": pct(m["回撤"]), "夏普": round(m["夏普"], 2),
            "胜过纯随机": pct((rand_raw < m["年化"]).mean()),
            "胜过过滤随机": pct((rand_filt < m["年化"]).mean())
        })

    print("\n" + "=" * 30 + f" B. 组合回测（随机基线 {a.n_random} 次）" + "=" * 30)
    print(pd.DataFrame(rows).to_string(index=False))
    if not a.demo:
        pd.DataFrame(rows).to_csv("composite_portfolio.csv", index=False, encoding="utf-8-sig")

    volmanaged_section(simulate, a, start, live)

    if not a.skip_struct:
        struct_section(f, ret, bench, simulate, a, start, live)


if __name__ == "__main__":
    main()
