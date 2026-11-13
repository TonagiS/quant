"""模拟盘幂等测试：同一天重复执行不重复成交、不改账户。"""
import os
import sys
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "position", "live"))
sys.path.insert(0, os.path.join(ROOT, "position", "live", "shadow"))
sys.path.insert(0, os.path.join(ROOT, "indicators", "code"))

import paper_account as pa  # noqa: E402


class TestPaperIdempotent(unittest.TestCase):
    def test_no_adx_book(self):
        self.assertNotIn("vt15_adx", [b[0] for b in pa.BOOKS])

    def test_same_day_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            pa.PAPER = tmp
            pa.BOOKS_DIR = os.path.join(tmp, "books")
            pa.VENUE = "场内"
            pa.BOOKS = [("idem", "IDEM", None, False)]
            targets = {"sh510300": 0.5}
            info = {"expo": 1.0}
            with patch.object(pa, "book_targets", return_value=(targets, 1.0, info, "2026-11-04")), \
                 patch.object(pa, "raw_for", return_value={"codes": {"sh510300": 4.0}}):
                pa.trade_book("idem", "IDEM", None, ds=date(2026, 11, 4), quiet=True)
                acc1 = pa.load(pa.acc_path("idem"), None)
                trades1 = len(pd.read_csv(pa.trades_path("idem")))
                pa.trade_book("idem", "IDEM", None, ds=date(2026, 11, 4), quiet=True)
                acc2 = pa.load(pa.acc_path("idem"), None)
                trades2 = len(pd.read_csv(pa.trades_path("idem")))
            self.assertEqual(acc1["cash"], acc2["cash"])
            self.assertEqual(acc1["positions"], acc2["positions"])
            self.assertEqual(trades1, trades2)

    def test_crash_between_save_and_trades(self):
        """账户已落盘、成交记录还没写完就中断，重跑后账户状态必须与干净跑一致。

        已知边界：这次中断会丢掉 trades.csv 的那几行（account 已推进、last_rebalance
        已更新，重跑走"非调仓日"分支）。所以断言的是**账户状态一致 + 不重复成交**，
        不是成交明细完整；明细缺失要在事后按 account.json 补，不能靠重跑了事。
        """
        targets = {"sh510300": 0.5}
        info = {"expo": 1.0}

        def run_clean(tmp):
            pa.PAPER = tmp
            pa.BOOKS_DIR = os.path.join(tmp, "books")
            pa.VENUE = "场内"
            pa.BOOKS = [("idem", "IDEM", None, False)]
            with patch.object(pa, "book_targets", return_value=(targets, 1.0, info, "2026-11-04")), \
                 patch.object(pa, "raw_for", return_value={"codes": {"sh510300": 4.0}}):
                pa.trade_book("idem", "IDEM", None, ds=date(2026, 11, 4), quiet=True)
            return pa.load(pa.acc_path("idem"), None), len(pd.read_csv(pa.trades_path("idem")))

        with tempfile.TemporaryDirectory() as tmp:
            clean_acc, clean_trades = run_clean(tmp)

        with tempfile.TemporaryDirectory() as tmp:
            pa.PAPER = tmp
            pa.BOOKS_DIR = os.path.join(tmp, "books")
            pa.VENUE = "场内"
            pa.BOOKS = [("idem", "IDEM", None, False)]
            real_append = pa.append_rows
            calls = {"n": 0}

            def flaky_append(path, cols, row):
                calls["n"] += 1
                if calls["n"] == 1:  # 模拟成交记录写到一半进程挂掉
                    raise RuntimeError("simulated crash before trades.csv")
                return real_append(path, cols, row)

            with patch.object(pa, "book_targets", return_value=(targets, 1.0, info, "2026-11-04")), \
                 patch.object(pa, "raw_for", return_value={"codes": {"sh510300": 4.0}}), \
                 patch.object(pa, "append_rows", side_effect=flaky_append):
                with self.assertRaises(RuntimeError):
                    pa.trade_book("idem", "IDEM", None, ds=date(2026, 11, 4), quiet=True)

            acc_mid = pa.load(pa.acc_path("idem"), None)
            self.assertEqual(acc_mid["cash"], clean_acc["cash"])
            self.assertEqual(acc_mid["positions"], clean_acc["positions"])

            # 重跑：应当走"非调仓日"，不再成交
            with patch.object(pa, "book_targets", return_value=(targets, 1.0, info, "2026-11-04")), \
                 patch.object(pa, "raw_for", return_value={"codes": {"sh510300": 4.0}}):
                pa.trade_book("idem", "IDEM", None, ds=date(2026, 11, 4), quiet=True)
            acc_after = pa.load(pa.acc_path("idem"), None)

        self.assertEqual(acc_after["cash"], clean_acc["cash"])
        self.assertEqual(acc_after["positions"], clean_acc["positions"])
        trades_path = pa.trades_path("idem")
        n_trades = len(pd.read_csv(trades_path)) if os.path.exists(trades_path) else 0
        self.assertEqual(n_trades, 0)
        self.assertGreaterEqual(clean_trades, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
