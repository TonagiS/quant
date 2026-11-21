#!/usr/bin/env python3
"""framework_layers 用的回测循环：由 position/engine.py 的 simulate 源码派生（不改原文件），只增加
  1) 入场过滤 f["entry_ok"]：新买入须 entry_ok 为真，已持有的不受影响；
  2) 离场层 exit_spec：("TS", x) 自建仓后最高收盘回撤 ≥ x；("DCL", n) 收盘 < 前 n 日最低收盘；
     ("TIME", n) 连续 n 个交易日没有新的建仓后高点。t 日收盘判定，t+1 收盘卖出（跌停/停牌顺延），卖出部分持现金到下次调仓。
不加任何层时与 engine.simulate 逐日一致（fw_layers.py check 核对）。"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "position"))
import engine as E  # noqa: E402

_src = inspect.getsource(E.simulate)
_rep = [
    ("def simulate(close: pd.DataFrame, f: dict, cfg: Config, keep_weights: bool = False) -> dict:",
     "def simulate_x(close: pd.DataFrame, f: dict, cfg: Config, keep_weights: bool = False, exit_spec=None) -> dict:"),
    ("    sched: dict[int, tuple] = {}\n",
     "    sched: dict[int, tuple] = {}\n"
     "    eok_a = f[\"entry_ok\"].to_numpy() if \"entry_ok\" in f else None\n"
     "    close_a = close.to_numpy()\n"
     "    hi_px, hi_i, hi_e, pend = {}, {}, {}, set()\n"
     "    n_exit = 0\n"),
    ("        expo_a[i] = float(sum(w.values()))\n",
     "        if exit_spec is not None:\n"
     "            if pend:\n"
     "                sell = [c for c in pend if c in w and not lim_dn[i, c] and not na[i, c]]\n"
     "                pend = {c for c in pend if c in w and c not in sell}\n"
     "                if sell:\n"
     "                    n_exit += len(sell)\n"
     "                    apply({c: x for c, x in w.items() if c not in sell})\n"
     "            kind, par = exit_spec\n"
     "            for c in list(w):\n"
     "                px = close_a[i, c]\n"
     "                if not np.isfinite(px):\n"
     "                    continue\n"
     "                ei = entry_i.get(c, i)\n"
     "                if hi_e.get(c) != ei:\n"
     "                    hi_e[c], hi_px[c], hi_i[c] = ei, px, i\n"
     "                if px > hi_px[c]:\n"
     "                    hi_px[c], hi_i[c] = px, i\n"
     "                if i <= ei:\n"
     "                    continue\n"
     "                if kind == \"TS\" and px / hi_px[c] - 1.0 <= -par:\n"
     "                    pend.add(c)\n"
     "                elif kind == \"DCL\" and i >= par and px < np.nanmin(close_a[i - par:i, c]):\n"
     "                    pend.add(c)\n"
     "                elif kind == \"TIME\" and i - hi_i[c] >= par:\n"
     "                    pend.add(c)\n"
     "        expo_a[i] = float(sum(w.values()))\n"),
    ("                ok = np.flatnonzero(cand_a[i] & ~np.isnan(rm_a[i]))\n",
     "                ok = np.flatnonzero(cand_a[i] & ~np.isnan(rm_a[i]))\n"
     "                if eok_a is not None:\n"
     "                    ok = np.array([c for c in ok if c in w or eok_a[i, c]], dtype=int)\n"),
    ("            \"n_due\": n_due, \"n_skip\": n_skip,", "            \"n_due\": n_due, \"n_skip\": n_skip, \"n_exit\": n_exit,"),
]
for a, b in _rep:
    assert _src.count(a) == 1, a
    _src = _src.replace(a, b)
_ns = dict(E.__dict__)
exec(compile(_src, "fw_engine_simulate_x", "exec"), _ns)
simulate_x = _ns["simulate_x"]
