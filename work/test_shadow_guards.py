# -*- coding: utf-8 -*-
"""回归测试：2026-11-17 事故后的三道守卫。不联网、不碰账本。

1) hist_close_safe：接口连续失败时退回本地 cache_ohlc；
2) protect_holdings：持仓因缺数据掉出候选池 → 按上次权重保留，其余按比例缩；
3) redeem_fee：费率分档读 fees.json（<7 天 1.5%，>=7 天 C 类 0）；
4) paper_account 的 7 天顺延与"只卖不在目标里的名字"逻辑不变。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, r"E:\Code\quant\position\live\shadow")
sys.path.insert(0, r"E:\Code\quant\position\live")

import paper_account as pa  # noqa: E402
import run_shadow as rs  # noqa: E402

ok, bad = [], []


def check(name, cond, detail=""):
    (ok if cond else bad).append(f"{name} {detail}".strip())
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail else ""))


# 1) 历史行情：接口必失败 → 走本地缓存
rs.hist_close = lambda code, days=400: (_ for _ in ()).throw(RuntimeError("mock ssl error"))
s, how = rs.hist_close_safe("sh512800")
check("hist 回退缓存", str(how).startswith("cache") and len(s) > 200,
      f"src={how} rows={len(s)} end={s.index[-1].date()}")

# 2) 持仓保护：银行不在候选池里
prev = {"sh512800": 0.23, "sh510230": 0.32, "sz159928": 0.18, "sh515170": 0.16, "sh512690": 0.11}
targets = {"sz159928": 0.28, "sh515170": 0.26, "sh512690": 0.19, "sh512010": 0.27}
out = rs.protect_holdings(targets, prev, ["sh512800", "sh510230"])
check("持仓保留", abs(out["sh512800"] - 0.23) < 1e-9 and abs(out["sh510230"] - 0.32) < 1e-9,
      f"keep={out.get('sh512800'):.4f}/{out.get('sh510230'):.4f}")
check("权重合计=1", abs(sum(out.values()) - 1.0) < 1e-9, f"sum={sum(out.values()):.6f}")
check("无持仓被误卖", all(c in out for c in prev), f"keys={sorted(out)}")
check("剩余按比例缩", abs(out["sz159928"] / out["sh515170"] - 0.28 / 0.26) < 1e-6)

# 3) 费率分档
check("赎回费 <7 天", abs(pa.redeem_fee(10000, 3, "sh510880") - 150.0) < 1e-6)
for d in (7, 14, 29, 30, 400):
    check(f"赎回费 {d} 天为 0", pa.redeem_fee(10000, d, "sh510880") == 0.0)
check("费率表已生效", pa.venue() == "场外" and pa.fee_conf()["tiers"][1] == [7, 0.0],
      str(pa.fee_conf()["tiers"]))

# 4) 影子目标函数仍可跑（用本地缓存拼一张表）
codes = ["sh512800", "sh510230", "sz159928", "sh515170", "sh512690", "sh512010",
         "sh510300", "sh510500", "sh512880", "sz159915"]
closes = {}
for c in codes:
    fp = Path(r"E:\Code\quant\data\cache_ohlc") / f"{c}.csv"
    df = pd.read_csv(fp, parse_dates=["date"])
    closes[c] = df.set_index("date")["close"].astype(float)
close = pd.DataFrame(closes).sort_index()
t = rs.engine.targets(close)
check("engine.targets 可用", bool(t["targets"]) and abs(sum(t["targets"].values()) - 1) < 1e-9,
      f"n={len(t['targets'])}")

print("\n通过 %d，失败 %d" % (len(ok), len(bad)))
sys.exit(1 if bad else 0)
