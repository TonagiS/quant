"""核验 POS-03 最优配置：ext / VT20 / reb35 / cap100%，base_ret 是否按同一 reb/成本重建。"""

import sys

sys.path.insert(0, r"E:\Code\quant")

from position.engine import (  # noqa: E402
    Config,
    VolTarget,
    features,
    load_panel,
    simulate,
    slice_metrics,
)

BUY = 0.0005
REB = 35
CAP = 1.0
TARGET = 0.20


def sell_cost(reb: int) -> float:
    cal = reb * 7 / 5
    fee = 0.015 if cal < 7 else (0.005 if cal < 30 else 0.0015)
    return fee + 0.0005


def run(pool: str, set_base_ret: bool):
    close = load_panel(pool)
    f = features(close)
    cfg = Config(reb=REB, buy_cost=BUY, sell_cost=sell_cost(REB), cap=CAP,
                 rule=VolTarget(target=TARGET, window=60, every=REB))
    if set_base_ret:
        # 手工按同一 reb/成本重建未加仓控的 base_ret，验证与 simulate 内部自动重建一致
        from position.engine import base_returns

        f = dict(f)
        f["base_ret"] = base_returns(close, f, cfg)
    res = simulate(close, f, cfg)
    m = slice_metrics(res, 60, len(close))
    return m


for pool in ("ext", "base"):
    for manual in (False, True):
        m = run(pool, manual)
        tag = "auto base_ret" if not manual else "manual base_ret"
        print(
            f"{pool:5s} {tag}: CAGR {m['cagr']:.2%}  MDD {m['mdd']:.2%}  "
            f"Calmar {m['calmar']:.2f}  turn {m['turn_two_way']:.1f}x  "
            f"expo {m['avg_expo']:.0%}"
        )
