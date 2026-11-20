# -*- coding: utf-8 -*-
"""等权基准的成分拆解：2019+ 段那 9.59%/年 到底是谁贡献的。

回答三个问题：
  1) 按资产类别（宽基/行业/跨境/商品/债券/风格/货币）拆，各类贡献多少；
  2) 剔除某类之后，等权基准还剩多少（反事实）；
  3) 2019 年前已上市 vs 之后上市，谁把基准抬上去的。

口径：与等权臂同一宇宙（features 的 valid & rm 非空），每日等权、零成本（毛收益），
      分解是精确的加法分解：Σ_类别 贡献 = 组合日收益。成本影响单独说明。
窗口：2019-01-02 → 2026-11-13（与 era_split 对齐）。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = r"E:\Code\quant"
OUT = os.path.join(ROOT, "runs", "2026-11-20_era_split")
UNIV = os.path.join(ROOT, "runs", "2026-11-14_layered_v1", "universe_all.csv")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "runs", "2026-11-20_portfolio_multiwindow"))

import bt  # noqa: E402
from position.engine import features  # noqa: E402

SPLIT = pd.Timestamp("2019-01-01")
END = pd.Timestamp("2026-11-13")
ANN = 252.0


def ann_geo(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) < 60:
        return float("nan")
    return float((1.0 + r).prod() ** (ANN / len(r)) - 1.0)


def mdd(r: pd.Series) -> float:
    eq = (1.0 + r.fillna(0.0)).cumprod()
    return float((eq / eq.cummax() - 1.0).min())


def load_categories() -> tuple[dict, dict]:
    u = pd.read_csv(UNIV, header=None, skiprows=1)
    u.columns = ["tx_code", "code6", "SECUCODE", "name", "index_name", "INDEX_CODE",
                 "category", "sector", "focus", "IS_HSETF", "IS_HBETF", "IS_WPETF",
                 "IS_ZQETF", "IS_SPETF", "IS_KJETF", "IS_HYETF", "IS_FGETF",
                 "IS_HSMARKET", "nav", "shares", "aum_est"]
    return (dict(zip(u["tx_code"], u["category"])),
            dict(zip(u["tx_code"], u["name"])))


def decompose(close: pd.DataFrame, cat: dict, name: dict, label: str) -> dict:
    f = features(close, min_names=8)
    mask = (f["valid"] & f["rm"].notna()).reindex(columns=close.columns, fill_value=False)
    ret = close.pct_change()

    seg = slice(int(close.index.searchsorted(SPLIT)), len(close.index))
    mask = mask.iloc[seg]
    ret_m = ret.iloc[seg].where(mask)
    n_live = mask.sum(axis=1)
    ok = n_live > 0
    ret_m, mask, n_live = ret_m[ok], mask[ok], n_live[ok]

    w = mask.div(n_live, axis=0)                      # 每日等权权重，和为 1
    r_p = (ret_m * w).sum(axis=1)                     # 组合毛收益

    cats = sorted({cat.get(c, "未分类") for c in close.columns})
    rows = []
    for c in cats:
        cols = [x for x in close.columns if cat.get(x, "未分类") == c]
        cm = mask[cols].any(axis=1)
        if not cm.any():
            continue
        dep = (ret_m[cols].fillna(0.0) * w[cols]).sum(axis=1)     # 该类对组合的日贡献
        own = ret_m[cols].mean(axis=1)                            # 该类自身等权日收益
        w_share = mask[cols].sum(axis=1) / n_live
        rows.append(dict(
            池=label, 类别=c,
            只数全池=len(cols),
            只数段内有效=int(mask[cols].any().sum()),
            平均权重=float(w_share[cm].mean()),
            年化贡献=float(ANN * dep.mean()),
            贡献占比=float(dep.sum() / r_p.sum()) if r_p.sum() else np.nan,
            该类自身年化=ann_geo(own[cm]),
            该类自身回撤=mdd(own[cm]),
        ))
    byc = pd.DataFrame(rows).sort_values("年化贡献", ascending=False)

    # 反事实：剔除若干类
    cf_rows = []
    for excl_name, excl_cats in (
        ("基线（全池）", []),
        ("剔除商品", ["商品"]),
        ("剔除跨境", ["跨境"]),
        ("剔除商品+跨境", ["商品", "跨境"]),
        ("剔除商品+跨境+债券+货币", ["商品", "跨境", "债券", "货币"]),
        ("只留宽基", None),
        ("只留行业", None),
    ):
        if excl_cats is None:
            keep = "宽基" if excl_name == "只留宽基" else "行业"
            m2 = mask[[x for x in close.columns if cat.get(x) == keep]]
        else:
            drop = [x for x in close.columns if cat.get(x) in excl_cats]
            m2 = mask.drop(columns=drop, errors="ignore")
        n2 = m2.sum(axis=1)
        w2 = m2.div(n2.replace(0, np.nan), axis=0).fillna(0.0)
        r2 = (ret.iloc[seg][ok].where(m2) * w2).sum(axis=1)
        cf_rows.append(dict(池=label, 方案=excl_name,
                            平均只数=float(n2.mean()), 毛年化=ann_geo(r2), 毛回撤=mdd(r2)))
    cf = pd.DataFrame(cf_rows)

    # 老 / 新上市拆分
    first = close.apply(lambda s: s.first_valid_index())
    is_new = pd.Series({c: (first[c] is not None and first[c] >= SPLIT) for c in close.columns})
    lrows = []
    for tag, sel in (("2019-01 前已上市", ~is_new), ("2019 年后上市", is_new)):
        cols = list(is_new.index[sel])
        if not cols:
            continue
        m3 = mask[cols]
        dep = (ret_m[cols].fillna(0.0) * w[cols]).sum(axis=1)
        own = ret_m[cols].mean(axis=1)
        lrows.append(dict(池=label, 分组=tag, 只数=len(cols),
                          段内平均权重=float((mask[cols].sum(axis=1) / n_live).mean()),
                          年化贡献=float(ANN * dep.mean()),
                          该类自身年化=ann_geo(own[m3.any(axis=1)]),
                          该类自身回撤=mdd(own[m3.any(axis=1)])))
    byl = pd.DataFrame(lrows)

    summary = dict(池=label, 段="2019-01-02 ~ 2026-11-13",
                   天数=int(len(r_p)), 毛年化=ann_geo(r_p), 毛回撤=mdd(r_p),
                   平均只数=float(n_live.mean()),
                   分解和年化=float(ANN * r_p.mean()),
                   明细=dict(zip(close.columns,
                                 [f"{cat.get(c,'未分类')}|{name.get(c,'')}" for c in close.columns])))
    return dict(byc=byc, cf=cf, byl=byl, summary=summary)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    cat, name = load_categories()

    etf, _bad = bt.clean(bt.load_etf_panel())
    etf = etf.loc[:END]
    idx = bt.load_idx_panel().loc[:END]

    # 指数池没有 category 字段，按中证行业指数编号手工归类
    idx_cat = {}
    for c in idx.columns:
        idx_cat[c] = "宽基" if c[-6:] in {"000015", "000016", "000300", "000905"} or c == "sz399006" else "行业"
    idx_name = {c: c for c in idx.columns}

    out_b, out_c, out_l, sums = [], [], [], []
    for label, close, cc, nn in (("宽ETF池92", etf, cat, name),
                                 ("指数代理15", idx, idx_cat, idx_name)):
        d = decompose(close, cc, nn, label)
        out_b.append(d["byc"])
        out_c.append(d["cf"])
        out_l.append(d["byl"])
        sums.append(d["summary"])
        print(f"\n===== {label} · 2019+ 段 =====")
        print(f"毛年化 {d['summary']['毛年化']:.4%}  毛回撤 {d['summary']['毛回撤']:.4%}  "
              f"平均只数 {d['summary']['平均只数']:.1f}  天数 {d['summary']['天数']}")
        print("\n-- 按类别 --")
        print(d["byc"].to_string(index=False, float_format=lambda x: "%.4f" % x))
        print("\n-- 反事实 --")
        print(d["cf"].to_string(index=False, float_format=lambda x: "%.4f" % x))
        print("\n-- 老 / 新上市 --")
        print(d["byl"].to_string(index=False, float_format=lambda x: "%.4f" % x))

    pd.concat(out_b).to_csv(os.path.join(OUT, "bench_by_category.csv"), index=False, encoding="utf-8-sig")
    pd.concat(out_c).to_csv(os.path.join(OUT, "bench_counterfactual.csv"), index=False, encoding="utf-8-sig")
    pd.concat(out_l).to_csv(os.path.join(OUT, "bench_by_listing.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(OUT, "bench_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(sums, fh, ensure_ascii=False, indent=2)
    print("\n已写出 bench_by_category.csv / bench_counterfactual.csv / bench_by_listing.csv / bench_meta.json")


if __name__ == "__main__":
    main()
