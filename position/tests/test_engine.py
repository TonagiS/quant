"""引擎单元测试：VolTarget 前置条件、卖出再买回的建仓日清理。"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from position.engine import (  # noqa: E402
    Config, Fixed, VolTarget, _update_entries, base_returns, features, load_panel, simulate,
)


class TestVolTarget(unittest.TestCase):
    def test_requires_base_returns(self):
        rule = VolTarget(0.15)
        with self.assertRaises(ValueError):
            rule.scale(10, {"rets": np.zeros(20), "base_rets": None, "start": 0, "equity": np.ones(20)})

    def test_base_returns_same_reb(self):
        close = load_panel("ext")
        f = features(close)
        cfg = Config(reb=7, buy_cost=0.0005, sell_cost=0.0005, rule=VolTarget(0.15, every=7))
        br = base_returns(close, f, cfg)
        self.assertEqual(len(br), len(close))
        # 基线 S2（Fixed 1）在 reb=7 下应与 base_returns 完全一致
        base = simulate(close, f, Config(reb=7, buy_cost=0.0005, sell_cost=0.0005, rule=Fixed(1.0)))
        np.testing.assert_allclose(br, base["ret"])

    def test_buffer_reduces_turnover(self):
        close = load_panel("ext")
        f = features(close)
        cfg = Config(reb=5, buy_cost=0.0005, sell_cost=0.0005,
                     rule=VolTarget(0.15, every=5))
        a = simulate(close, f, cfg)
        cfg2 = Config(reb=5, buy_cost=0.0005, sell_cost=0.0005, buffer_n=7,
                      rule=VolTarget(0.15, every=5))
        b = simulate(close, f, cfg2)
        self.assertLess(np.sum(b["turn"]), np.sum(a["turn"]))


class TestEntries(unittest.TestCase):
    def test_sell_then_rebuy_resets_entry(self):
        close = pd.DataFrame({0: [10.0, 11.0, 12.0, 13.0]})
        px, ei = _update_entries({}, {}, {0: 0.5}, 0, close)
        self.assertEqual(ei[0], 0)
        # 继续持有：保留 0
        px, ei = _update_entries(px, ei, {0: 0.5}, 2, close)
        self.assertEqual(ei[0], 0)
        # 清仓：信息消失
        px, ei = _update_entries(px, ei, {}, 2, close)
        self.assertEqual(px, {})
        self.assertEqual(ei, {})
        # 再买回：从 i=3 重新起算，不沿用旧建仓日
        px, ei = _update_entries(px, ei, {0: 0.5}, 3, close)
        self.assertEqual(ei[0], 3)
        self.assertEqual(px[0], 13.0)

    def test_zero_weight_clears_entry(self):
        close = pd.DataFrame({0: [10.0, 11.0]})
        px, ei = _update_entries({0: 10.0}, {0: 1}, {0: 0.0}, 1, close)
        self.assertEqual(px, {})
        self.assertEqual(ei, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
