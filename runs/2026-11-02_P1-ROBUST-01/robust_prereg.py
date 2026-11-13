"""P1-ROBUST-01 预登记检验：S2（动量+趋势+逆波动）的稳健性。

纪律：不修改仓库任何文件；只读复用 backtest_composite 的 build_panel / build_features /
xs_rank / run_portfolio / perf / volmanage。参数与判据见 registry.csv，运行前已冻结。

对象：S1=动量+趋势；S2=动量+趋势+逆波动；S3=S2 套波控(目标15%,窗口60日)；基准=等权。
基线：k=5, reb=5, cost=0.0005, delay=1, start=20190101。
扰动：k=3/8、reb=10/20、cost=0.001/0.002、delay=0/2（一次只改一个）。
剔除：按全样本涨幅剔除前 3 只、前 5 只后重跑基线。
拆分：S2 相对等权的逐年超额；滚动 3 年（756 交易日，步长 21）年化超额。
判据：见 registry.csv 五条，base、ext 各自判定。
"""
import json
import os
import sys
import datetime

import numpy as np
import pandas as pd

import backtest_composite as bc
from macd_structure import ETF_POOL
from pools import get_pool, load_one

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_2019 = os.path.join(BASE_DIR, "work", "cache2019")
MIN_NAMES = 8
LOOKBACK = 120
START = "20190101"
VMA_TARGET, VMA_WINDOW = 0.15, 60
ROLL_W, ROLL_STEP = 756, 21

S1 = dict(mom=1, trend=True)
S2 = dict(mom=1, trend=True, invvol=True)

COMBOS = [
    ("baseline k5 reb5 c5bp d1", 5, 5, 0.0005, 1),
    ("k=3", 3, 5, 0.0005, 1),
    ("k=8", 8, 5, 0.0005, 1),
    ("reb=10", 5, 10, 0.0005, 1),
    ("reb=20", 5, 20, 0.0005, 1),
    ("cost=0.001", 5, 5, 0.001, 1),
    ("cost=0.002", 5, 5, 0.002, 1),
    ("delay=0", 5, 5, 0.0005, 0),
    ("delay=2", 5, 5, 0.0005, 2),
]


def load_pool(pool_name, start, cache):
    pool = get_pool(pool_name, ETF_POOL)
    data, failed = {}, []
    for code, (name, kind) in pool.items():
        try:
            df, _ = load_one(code, kind, start, cache)
            data[code] = df
        except Exception as e:
            failed.append((name, code, str(e)[:120]))
    return data, failed


def build(data):
    close, amt = bc.build_panel(data)
    f = bc.build_features(close, amt, LOOKBACK)
    return close, amt, f


def prep(f, min_names=MIN_NAMES):
    valid = f["valid"]
    dates = f["close"].index
    rk = {k: bc.xs_rank(f[k], valid, min_names) for k in ("mom5", "mom20", "mom60", "mom120")}
    rk["vol_ratio"] = bc.xs_rank(f["vol_ratio"], f["valid_vol"], min_names)
    rk["crowd"] = bc.xs_rank(f["crowd"], f["valid_vol"], min_names)
    rm = (rk["mom20"] + rk["mom60"] + rk["mom120"]) / 3
    enough = rm.notna().any(axis=1).to_numpy()
    start = int(np.argmax(enough))
    n = len(dates) - start
    bench = f["ret"].where(valid).mean(axis=1).fillna(0).to_numpy().copy()
    bench[:start] = 0.0
    return rk, rm, start, bench


def make_sim(f, rm, start, k, reb, cost, delay):
    ret = f["ret"].to_numpy()
    vol = f["vol20"].to_numpy()
    close = f["close"]
    rm_fill = np.nan_to_num((rm - 0.5).to_numpy(), nan=0.0)
    base_cand = (f["valid"] & rm.notna()).to_numpy()
    lim = np.array([0.20 if c.startswith(("sh588", "sz301")) else 0.10 for c in close.columns])
    lim_up, lim_dn = ret >= lim * 0.98, ret <= -lim * 0.98

    def sim(opt):
        cand = base_cand.copy()
        if opt.get("vol") or opt.get("crowd"):
            cand &= f["valid_vol"].to_numpy()
        if opt.get("trend"):
            cand &= (close > f["ma60"]).to_numpy()
        if opt.get("topf"):
            cand &= ~f["top_flag"].to_numpy()
        p, _ = bc.run_portfolio(ret, rm_fill, cand, vol, lim_up, lim_dn, k, reb, cost, delay,
                                start, invvol=opt.get("invvol", False))
        return p
    return sim


def pct(x):
    return "" if x != x else f"{x * 100:.1f}%"


def metrics(p, live):
    m = bc.perf(p[live])
    return dict(年化=m["年化"], 回撤=m["回撤"], 夏普=m["夏普"], 卡玛=m["卡玛"])


def subset(f, exclude):
    """只删列：build_features 全部是按列滚动/按列状态，删列等价于重新构建子面板，但省去重算 MACD。"""
    return {k: (v.drop(columns=[c for c in exclude if c in v.columns]) if isinstance(v, pd.DataFrame) else v)
            for k, v in f.items()}


def yearly_excess(p, bench, dates, live):
    df = pd.DataFrame({"p": p, "b": bench}, index=dates)[live]
    rows = []
    for y, g in df.groupby(df.index.year):
        rp, rb = (1 + g["p"]).prod() - 1, (1 + g["b"]).prod() - 1
        rows.append({"年份": int(y), "S2": rp, "等权": rb, "超额": rp - rb})
    return pd.DataFrame(rows)


def rolling_excess(p, bench, live):
    lp, lb = np.log1p(p[live]), np.log1p(bench[live])
    n = len(lp)
    out = []
    if n < ROLL_W + 1:
        return pd.DataFrame(columns=["起始日", "结束日", "S2年化", "等权年化", "年化超额"])
    for i in range(0, n - ROLL_W + 1, ROLL_STEP):
        ap = np.exp(lp[i:i + ROLL_W].sum()) ** (252 / ROLL_W) - 1
        ab = np.exp(lb[i:i + ROLL_W].sum()) ** (252 / ROLL_W) - 1
        out.append({"起始日": "", "结束日": "", "S2年化": ap, "等权年化": ab, "年化超额": ap - ab})
    return pd.DataFrame(out)


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("P1-ROBUST-01 预登记检验（判据见 registry.csv，运行前冻结）")
    print(f"基线：k=5 reb=5 cost=0.0005 delay=1 start={START}；S3=波控({VMA_TARGET:.0%},{VMA_WINDOW}日)")
    all_summary = []
    detail = {}
    for pool_name in ("base", "ext"):
        print("\n" + "#" * 100)
        print(f"# 池 {pool_name}")
        data, failed = load_pool(pool_name, START, CACHE_2019)
        close, amt, f = build(data)
        print(f"  载入 {len(data)} 只，失败 {len(failed)} 只，面板 {close.shape[1]} 只，"
              f"区间 {close.index[0].date()} ~ {close.index[-1].date()}")
        for n, c, m in failed:
            print(f"    失败 {n}({c}): {m}")
        rk, rm, start, bench = prep(f)
        dates = close.index
        live = np.arange(len(dates)) >= start
        print(f"  实盘段 {dates[start].date()} ~ {dates[-1].date()}（{int(live.sum())} 交易日）")

        # ---- 9 个参数组合 ----
        rows = []
        for label, k, reb, cost, delay in COMBOS:
            sim = make_sim(f, rm, start, k, reb, cost, delay)
            p1, p2 = sim(S1), sim(S2)
            p3, _ = bc.volmanage(p2, start, VMA_TARGET, VMA_WINDOW, 1.0, cost, delay)
            m1, m2, m3, mb = metrics(p1, live), metrics(p2, live), metrics(p3, live), metrics(bench, live)
            rows.append({"组合": label, "k": k, "reb": reb, "cost": cost, "delay": delay,
                         "S1年化": m1["年化"], "S1回撤": m1["回撤"],
                         "S2年化": m2["年化"], "S2回撤": m2["回撤"], "S2夏普": m2["夏普"], "S2卡玛": m2["卡玛"],
                         "S2超额": m2["年化"] - mb["年化"],
                         "S3年化": m3["年化"], "S3回撤": m3["回撤"], "S3夏普": m3["夏普"],
                         "等权年化": mb["年化"], "等权回撤": mb["回撤"]})
        combos = pd.DataFrame(rows)

        # ---- 剔除检验 ----
        full_ret = {}
        for c in close.columns:
            s = close[c].dropna()
            if len(s) > 20:
                full_ret[c] = s.iloc[-1] / s.iloc[0] - 1
        order = [c for c, _ in sorted(full_ret.items(), key=lambda kv: -kv[1])]
        ex_rows = []
        for label, n_ex in (("baseline 全池", 0), ("剔除涨幅前3", 3), ("剔除涨幅前5", 5)):
            excl = set(order[:n_ex])
            f_sub = subset(f, excl) if n_ex else f
            rk_s, rm_s, start_s, bench_s = prep(f_sub)
            live_s = np.arange(len(dates)) >= start_s
            sim_s = make_sim(f_sub, rm_s, start_s, 5, 5, 0.0005, 1)
            p2 = sim_s(S2)
            m2, mb = metrics(p2, live_s), metrics(bench_s, live_s)
            ex_rows.append({"方案": label, "剔除": ",".join(sorted(excl)) if excl else "",
                            "实盘起": str(dates[start_s].date()),
                            "S2年化": m2["年化"], "S2回撤": m2["回撤"],
                            "等权年化": mb["年化"], "超额": m2["年化"] - mb["年化"]})
        excl_df = pd.DataFrame(ex_rows)

        # ---- 逐年 / 滚动（基线 S2）----
        sim0 = make_sim(f, rm, start, 5, 5, 0.0005, 1)
        p2_base = sim0(S2)
        ydf = yearly_excess(p2_base, bench, dates, live)
        rdf = rolling_excess(p2_base, bench, live)
        if len(rdf):
            rdf["起始日"] = [str(dates[live][i].date()) for i in range(0, int(live.sum()) - ROLL_W + 1, ROLL_STEP)][:len(rdf)]
            rdf["结束日"] = [str(dates[live][i + ROLL_W - 1].date()) for i in range(0, int(live.sum()) - ROLL_W + 1, ROLL_STEP)][:len(rdf)]

        # ---- 判据 ----
        c1 = bool((combos["S2超额"] > 0).all())
        c2 = bool((excl_df.loc[excl_df["方案"] != "baseline 全池", "超额"] > 0).all())
        pos_years = int((ydf["超额"] > 0).sum())
        c3 = pos_years / len(ydf) >= 0.60
        max_y = ydf.loc[ydf["超额"].idxmax(), "年份"]
        rem = pd.DataFrame({"p": p2_base, "b": bench}, index=dates)[live]
        rem = rem[rem.index.year != max_y]
        ann_p = (1 + rem["p"]).prod() ** (252 / len(rem)) - 1
        ann_b = (1 + rem["b"]).prod() ** (252 / len(rem)) - 1
        c4 = bool(ann_p - ann_b > 0)
        pos_roll = int((rdf["年化超额"] > 0).sum()) if len(rdf) else 0
        c5 = (pos_roll / len(rdf) >= 0.70) if len(rdf) else False

        print("\n  [A] 9 个参数组合（S1/S2/S3 描述，S2 参与判定）")
        show = combos.assign(S1年化=lambda d: d["S1年化"].map(pct), S1回撤=lambda d: d["S1回撤"].map(pct),
                             S2年化=lambda d: d["S2年化"].map(pct), S2回撤=lambda d: d["S2回撤"].map(pct),
                             S2超额=lambda d: d["S2超额"].map(lambda x: f"{x*100:+.1f}pp"),
                             S3年化=lambda d: d["S3年化"].map(pct), S3回撤=lambda d: d["S3回撤"].map(pct),
                             等权年化=lambda d: d["等权年化"].map(pct))
        print(show[["组合", "S1年化", "S1回撤", "S2年化", "S2回撤", "S2夏普", "S2卡玛", "S2超额",
                    "S3年化", "S3回撤", "S3夏普", "等权年化"]].to_string(index=False))

        print("\n  [B] 剔除涨幅最强的标的")
        print(excl_df.assign(S2年化=lambda d: d["S2年化"].map(pct), S2回撤=lambda d: d["S2回撤"].map(pct),
                             等权年化=lambda d: d["等权年化"].map(pct),
                             超额=lambda d: d["超额"].map(lambda x: f"{x*100:+.1f}pp"))[
            ["方案", "实盘起", "S2年化", "S2回撤", "等权年化", "超额"]].to_string(index=False))

        print("\n  [C] S2 逐年超额")
        print(ydf.assign(S2=lambda d: d["S2"].map(pct), 等权=lambda d: d["等权"].map(pct),
                         超额=lambda d: d["超额"].map(lambda x: f"{x*100:+.1f}pp")).to_string(index=False))

        print("\n  [D] 滚动 3 年年化超额（756 日，步长 21）")
        if len(rdf):
            print(f"    窗口数 {len(rdf)}；>0 占比 {pos_roll/len(rdf):.1%}；"
                  f"中位 {rdf['年化超额'].median()*100:+.1f}pp；最小 {rdf['年化超额'].min()*100:+.1f}pp；"
                  f"最大 {rdf['年化超额'].max()*100:+.1f}pp")
        else:
            print("    样本不足 3 年，无法计算")

        verdicts = {"①9组合全为正": c1, "②剔除后仍为正": c2,
                    f"③逐年为正占比≥60%({pos_years}/{len(ydf)})": c3,
                    f"④去掉最大年({max_y})后年化超额>0": c4,
                    f"⑤滚动3年>0占比≥70%({pos_roll}/{len(rdf)})": c5}
        print("\n  判据：", " | ".join(f"{k}{'✓' if v else '✗'}" for k, v in verdicts.items()))
        passed = all(verdicts.values())
        print(f"  判定：{'稳健（通过）' if passed else '未通过 → 热门主题特例，不能当通用规则'}")

        for nm, df in (("robust_combos", combos), ("robust_exclude", excl_df),
                       ("robust_yearly", ydf), ("robust_rolling", rdf)):
            df.to_csv(f"{nm}_{pool_name}.csv", index=False, encoding="utf-8-sig")
        detail[pool_name] = {"passed": passed, "verdicts": verdicts,
                             "live_start": str(dates[start].date()), "live_end": str(dates[-1].date()),
                             "roll_share": (pos_roll / len(rdf)) if len(rdf) else None}
        all_summary.append({"池": pool_name, "判定": "通过" if passed else "未通过",
                            **{k: bool(v) for k, v in verdicts.items()}})

    summary = pd.DataFrame(all_summary)
    summary.to_csv("robust_summary.csv", index=False, encoding="utf-8-sig")
    meta = {"id": "P1-ROBUST-01", "run_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "start": START, "combos": [c[0] for c in COMBOS], "detail": detail}
    with open("robust_meta.json", "w", encoding="utf-8") as fp:
        json.dump(meta, fp, indent=2, ensure_ascii=False)
    print("\n" + "=" * 100)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
