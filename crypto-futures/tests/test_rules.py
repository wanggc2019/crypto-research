#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5 条规则的触发 / 不触发边界。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import helpers  # noqa: E402
import indicators as ind  # noqa: E402
import rules as R  # noqa: E402
from helpers import STEP, T0  # noqa: E402

CFG = helpers.rules_cfg()


def varied_series(points=2100):
    """账户比在 2.00–2.99 之间循环（7 天 p95≈2.94、p5≈2.04），其余指标平稳。"""
    s = helpers.flat_series(T0, points)
    for k in range(points):
        ts = T0 - k * STEP
        g = 2.0 + (k % 100) / 100.0
        s["G"][ts] = (g, g / (1 + g))
    return s


class TestRetailShift(unittest.TestCase):
    def _hit(self, now_g):
        s = helpers.flat_series(T0, 20, g=2.0)
        s["G"][T0] = (now_g, 0.7)
        return R.retail_shift(s, T0, CFG)

    def test_up_down_and_below_threshold(self):
        self.assertEqual(self._hit(2.42)["direction"], "up")
        self.assertEqual(self._hit(1.58)["direction"], "down")
        self.assertIsNone(self._hit(2.38))


class TestDivergence(unittest.TestCase):
    def _hit(self, g_now, t_now):
        s = helpers.flat_series(T0, 300, g=2.0, t=2.0)
        s["G"][T0] = (g_now, 0.7)
        s["T"][T0] = (t_now, 0.6)
        return R.smart_divergence(s, T0, CFG)

    def test_both_directions(self):
        self.assertEqual(self._hit(2.4, 1.6)["direction"], "retail_long")
        self.assertEqual(self._hit(1.6, 2.4)["direction"], "retail_short")

    def test_needs_both_legs(self):
        self.assertIsNone(self._hit(2.4, 1.8))      # 大户只降 10%
        self.assertIsNone(self._hit(2.4, 2.4))      # 同向
        hit = self._hit(2.4, 1.6)
        self.assertAlmostEqual(hit["magnitude"], 20.0)   # 两腿变化绝对值的较小者


class TestCrowding(unittest.TestCase):
    def _series(self, g_now, oi_mult):
        s = varied_series()
        s["G"][T0] = (g_now, 0.75)
        s["OI"][T0] = (1000.0 * oi_mult, 1000.0 * oi_mult * 100.0)
        return s

    def test_long_and_short_crowding(self):
        self.assertEqual(R.crowding_build(self._series(3.0, 1.06), T0, CFG)["direction"], "long")
        self.assertEqual(R.crowding_build(self._series(2.0, 1.06), T0, CFG)["direction"], "short")
        self.assertEqual(R.crowding_build(self._series(3.0, 1.06), T0, CFG)["severity"], "P0")

    def test_oi_or_level_not_enough(self):
        self.assertIsNone(R.crowding_build(self._series(3.0, 1.04), T0, CFG))   # OI 只 +4%
        self.assertIsNone(R.crowding_build(self._series(2.5, 1.10), T0, CFG))   # 账户比在中间

    def test_low_coverage_skips(self):
        s = self._series(3.0, 1.06)
        for k in range(1, 400):                   # 7 天窗口只剩 80% 的点
            s["G"].pop(T0 - k * STEP, None)
        self.assertIsNone(R.crowding_build(s, T0, CFG))


class TestTakerBurst(unittest.TestCase):
    def _series(self, buy, sell):
        s = helpers.flat_series(T0, 400, buy=5.0, sell=5.0)
        for i in range(3):
            s["K"][T0 - i * STEP] = (buy, sell)
        return s

    def test_symmetric_thresholds(self):
        self.assertEqual(R.taker_burst(self._series(35.0, 10.0), T0, CFG)["direction"], "buy")    # R = 3.5
        self.assertEqual(R.taker_burst(self._series(10.0, 35.0), T0, CFG)["direction"], "sell")   # R = 1/3.5
        self.assertIsNone(R.taker_burst(self._series(34.0, 10.0), T0, CFG))

    def test_zero_buy_volume_does_not_crash(self):
        self.assertIsNone(R.taker_burst(self._series(0.0, 50.0), T0, CFG))

    def test_needs_volume(self):
        self.assertIsNone(R.taker_burst(self._series(7.0, 2.0), T0, CFG))   # R=3.5 但量不到 3 倍


class TestExtremeLevel(unittest.TestCase):
    def test_only_p2(self):
        s = varied_series()
        s["G"][T0] = (3.5, 0.78)
        hit = R.extreme_level(s, T0, CFG)
        self.assertEqual((hit["direction"], hit["severity"]), ("high", "P2"))
        s["G"][T0] = (2.5, 0.7)
        self.assertIsNone(R.extreme_level(s, T0, CFG))


class TestEvaluate(unittest.TestCase):
    def test_disabled_rule_is_skipped(self):
        s = helpers.flat_series(T0, 20, g=2.0)
        s["G"][T0] = (2.6, 0.72)
        cfg = helpers.rules_cfg(retail_shift={"enabled": False})
        self.assertEqual(R.evaluate(s, T0, cfg), [])
        self.assertEqual([h["rule"] for h in R.evaluate(s, T0, CFG)], ["retail_shift"])

    def test_requires_cover_all_rules(self):
        self.assertEqual(set(R.REQUIRES), set(R.RULES))
        self.assertTrue(all(ind.STEP == STEP for _ in [0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
