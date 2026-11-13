"""R10 的预先登记单次检验（只此一次，不扫其他规则/期限/方向）。

检验对象：冻结组 R10「20日新高突破 + 站上60日线 + 放量(>1.2)」，方向=买入。
固定参数：h=10 日、delay=1、cooldown=10、min_names=8、start=20070101。
宇宙：① stocks（22 只蓝筹，2007-01-04~最后可得日）② long 指数段（截到 2018-12-31）。
判据：NW_t(方向) >= 2，且 方向边际(超额) > 0、平均收益 > 0，两个宇宙各自成立才算通过。

计算全部复用 backtest_composite 的函数（build_panel / build_features / xs_rank /
make_rules / eval_rules），只把 HORIZONS 临时限定为 (10,)；不修改任何仓库文件、不改阈值。
"""
import os
import sys

import numpy as np
import pandas as pd

import backtest_composite as bc
from macd_structure import ETF_POOL
from pools import get_pool, load_one

START = "20070101"
CACHE = os.environ.get("QUANT_CACHE", "cache")
MIN_NAMES = 8
RULE_PREFIX = "R10 "          # 只认冻结组 R10（带放量）；R10_no_vol 是消融项，不参与本次检验
HORIZON = 10


def build(pool_name):
    pool = get_pool(pool_name, ETF_POOL)
    data, failed = {}, []
    for code, (name, kind) in pool.items():
        try:
            df, _ = load_one(code, kind, START, CACHE)
            data[code] = df
        except Exception as e:
            failed.append((name, f"{code} {str(e)[:100]}"))
    close, amt = bc.build_panel(data)
    f = bc.build_features(close, amt, 120)
    return f, failed


def slice_to(f, end):
    m = f["close"].index <= pd.Timestamp(end)
    return {k: (v.loc[m] if isinstance(v, pd.DataFrame) else v) for k, v in f.items()}


def prep(f):
    rk = {k: bc.xs_rank(f[k], f["valid"], MIN_NAMES) for k in ("mom5", "mom20", "mom60", "mom120")}
    rk["vol_ratio"] = bc.xs_rank(f["vol_ratio"], f["valid_vol"], MIN_NAMES)
    rk["crowd"] = bc.xs_rank(f["crowd"], f["valid_vol"], MIN_NAMES)
    rm = (rk["mom20"] + rk["mom60"] + rk["mom120"]) / 3
    enough = rm.notna().any(axis=1).to_numpy()
    start = int(np.argmax(enough))
    n = len(f["close"]) - start
    seg = np.zeros(len(f["close"]), dtype=int)
    seg[start + int(n * 0.5):] = 1
    seg[start + int(n * 0.75):] = 2
    return rk, start, seg


def run_one(label, pool_name, end=None):
    f, failed = build(pool_name)
    if end:
        f = slice_to(f, end)
    rk, start, seg = prep(f)
    dates = f["close"].index
    n_valid = f["valid"].sum(axis=1).to_numpy()[start:]
    rules = bc.make_rules(f, rk)
    res = bc.eval_rules(rules, f, dates, seg, 1, 10, start)
    row = res[(res["规则"].str.startswith(RULE_PREFIX)) & (res["天数"] == HORIZON)]
    out = {
        "宇宙": label, "标的数": int(f["close"].shape[1]), "失败": len(failed),
        "实盘起": str(dates[start].date()), "实盘止": str(dates[-1].date()),
        "有效数(中位/最少)": f"{int(np.median(n_valid))}/{int(n_valid.min())}",
    }
    if row.empty:
        out.update({"触发日数": 0, "结论": "无触发，检验不成立"})
        print(pd.DataFrame([out]).T.to_string(header=False))
        return out
    r = row.iloc[0]
    passed = (r["NW_t(方向)"] >= 2) and (r["方向边际%"] > 0) and (r["平均收益%"] > 0)
    out.update({
        "触发日数": int(r["触发日数"]), "不重叠n": int(r["不重叠n"]),
        "平均收益%": r["平均收益%"], "方向边际%": r["方向边际%"],
        "NW_t(方向)": r["NW_t(方向)"], "不重叠t(方向)": r["不重叠t(方向)"],
        "训练%": r["训练%"], "验证%": r["验证%"], "测试%": r["测试%"],
        "结论": "通过" if passed else "未通过",
    })
    print(f"\n=== {label}（{out['实盘起']} ~ {out['实盘止']}，{out['标的数']} 只，失败 {len(failed)}，"
          f"有效数中位 {out['有效数(中位/最少)']}）===")
    if failed:
        for n, m in failed:
            print(f"  失败 {n}: {m}")
    cols = ["触发日数", "不重叠n", "平均收益%", "方向边际%", "NW_t(方向)", "不重叠t(方向)", "训练%", "验证%", "测试%"]
    print(r[cols].to_frame().T.to_string(index=False))
    print(f"判据 NW_t>=2 且 平均收益>0 且 方向边际>0 -> {out['结论']}")
    return out


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    print("R10 预先登记单次检验（h=10、买入、delay=1、cooldown=10、min_names=8、start=20070101）")
    print(f"规则 = {RULE_PREFIX.strip()} 20日新高突破+站上60日线+放量；R10_no_vol 不参与")
    bc.HORIZONS = (HORIZON,)  # 只算 10 日，避免顺手扫到 5/20 日
    rows = [run_one("stocks 个股池", "stocks"),
            run_one("long 指数段 2007-2018", "long", end="2018-12-31")]
    df = pd.DataFrame(rows)
    df.to_csv("r10_result.csv", index=False, encoding="utf-8-sig")
    print("\n=== 汇总 ===")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
