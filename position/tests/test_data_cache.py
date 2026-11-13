"""缓存覆盖检查：早于缓存起点的 start 必须报错，而不是静默短样本。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

from pools import CACHE_2019, load_one  # noqa: E402


class TestCacheCoverage(unittest.TestCase):
    def test_start_before_cache_raises(self):
        with self.assertRaises(ValueError):
            load_one("sh510300", "etf", "20100101", CACHE_2019)

    def test_normal_start_ok(self):
        df, _ = load_one("sh510300", "etf", "20190101", CACHE_2019)
        self.assertGreater(len(df), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
