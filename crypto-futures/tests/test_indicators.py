#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""派生量：时间戳精确对齐、7 天窗口不含当前点、分位数、15 分钟聚合口径、合约价除零。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import helpers  # noqa: E402
import indicators as ind  # noqa: E402
import series as S  # noqa: E402
from helpers import STEP, T0  # noqa: E402


class TestAlignment(unittest.TestCase):
    def test_change_uses_exact_timestamp(self):
        s = helpers.flat_series(T0, 20, g=2.0)
        s["G"][T0] = (3.0, 0.75)
        self.assertAlmostEqual(ind.chg(s, "G", T0, ind.HOUR), 50.0)

    def test_missing_reference_point_returns_none(self):
        s = helpers.flat_series(T0, 20)
        del s["G"][T0 - ind.HOUR]          # 1h 前那个点缺失：不找相邻点
        self.assertIsNone(ind.chg(s, "G", T0, ind.HOUR))
        self.assertIsNone(ind.chg(s, "G", T0, ind.DAY))   # 24h 前根本没数据


class TestWindow(unittest.TestCase):
    def test_window_excludes_current_point(self):
        s = helpers.flat_series(T0, 30, g=1.0)
        s["G"][T0] = (9.0, 0.9)
        vals = ind.window(s, "G", T0, 10)
        self.assertEqual(len(vals), 10)
        self.assertNotIn(9.0, vals)

    def test_quantile_nearest_rank(self):
        vals = list(range(1, 101))
        self.assertEqual(ind.quantile(vals, 0.95), 95)
        self.assertEqual(ind.quantile(vals, 0.05), 5)
        self.assertIsNone(ind.quantile([], 0.5))

    def test_quantile_small_windows(self):
        self.assertEqual(ind.quantile([7.0], 0.95), 7.0)
        self.assertEqual(ind.quantile([1.0, 2.0], 0.05), 1.0)
        self.assertEqual(ind.quantile([1.0, 2.0], 0.95), 2.0)
        self.assertIsNone(ind.rank_pct([], 1.0))

    def test_rank_pct(self):
        self.assertEqual(ind.rank_pct([1, 2, 3, 4], 3), 75.0)


class TestTaker(unittest.TestCase):
    def test_sum_then_divide_not_average_of_ratios(self):
        s = S.empty()
        # 三个 5m 点：比值分别 10、0.1、0.1 → 比值平均 ≈ 3.4；量加总后 (100+10+10)/(10+100+100) ≈ 0.57
        s["K"] = {T0 - 2 * STEP: (100.0, 10.0), T0 - STEP: (10.0, 100.0), T0: (10.0, 100.0)}
        r, buy, sell, vol = ind.taker_15m(s, T0)
        self.assertAlmostEqual(r, 120.0 / 210.0)
        self.assertEqual((buy, sell, vol), (120.0, 210.0, 330.0))

    def test_missing_point_or_zero_sell(self):
        s = S.empty()
        s["K"] = {T0 - STEP: (1.0, 1.0), T0: (1.0, 1.0)}
        self.assertIsNone(ind.taker_15m(s, T0))
        s["K"][T0 - 2 * STEP] = (1.0, 0.0)
        s["K"][T0 - STEP] = (1.0, 0.0)
        s["K"][T0] = (1.0, 0.0)
        self.assertIsNone(ind.taker_15m(s, T0))

    def test_zero_buy_volume_returns_none(self):
        s = S.empty()
        s["K"] = {T0 - 2 * STEP: (0.0, 5.0), T0 - STEP: (0.0, 5.0), T0: (0.0, 5.0)}
        self.assertIsNone(ind.taker_15m(s, T0))           # 否则 R=0，后面算 1/R 会除零

    def test_volume_baseline_needs_half_coverage(self):
        s = helpers.flat_series(T0, 400, buy=5.0, sell=5.0)
        self.assertEqual(ind.taker_volume_baseline(s, T0), 30.0)
        s2 = helpers.flat_series(T0, 60)
        self.assertIsNone(ind.taker_volume_baseline(s2, T0))


class TestPrice(unittest.TestCase):
    def test_price_from_open_interest(self):
        s = S.empty()
        s["OI"][T0] = (2.0, 150.0)
        self.assertEqual(ind.price(s, T0), 75.0)

    def test_zero_or_missing_contracts_gives_none(self):
        s = S.empty()
        s["OI"][T0] = (0.0, 150.0)
        s["OI"][T0 - STEP] = (2.0, None)
        self.assertIsNone(ind.price(s, T0))
        self.assertIsNone(ind.price(s, T0 - STEP))
        self.assertIsNone(ind.price(s, T0 - 2 * STEP))


if __name__ == "__main__":
    unittest.main(verbosity=2)
