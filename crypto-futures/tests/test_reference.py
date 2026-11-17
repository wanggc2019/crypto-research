#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史参照：只用更早且已满 24h 的同规则触发；样本 < 3 显示「样本不足」。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import cards  # noqa: E402
import helpers  # noqa: E402
import reference  # noqa: E402
from helpers import T0  # noqa: E402

DAY = 86400


def rec(ts, fwd, rule="retail_shift"):
    return {"ts": ts, "asset": "BTC", "rule": rule, "severity": "P1", "direction": "up",
            "magnitude": 20.0, "price": 100.0, "fwd_pct": fwd, "source": "live"}


class TestStats(unittest.TestCase):
    def test_excludes_current_recent_and_other_rules(self):
        records = [rec(T0 - 3 * DAY, -2.0), rec(T0 - 2 * DAY, 1.0), rec(T0 - 1 * DAY, -0.5),
                   rec(T0 - 3600, 4.0),                  # 未满 24h：结果还没出来
                   rec(T0, 9.0),                         # 当前这次
                   rec(T0 - 2 * DAY, 5.0, "taker_burst"),  # 别的规则
                   rec(T0 - 40 * DAY, 3.0)]              # 超出 30 天
        st = reference.stats(records, "retail_shift", T0)
        self.assertEqual((st["n"], st["up"], st["down"]), (3, 1, 2))
        self.assertAlmostEqual(st["median"], -0.5)
        self.assertTrue(st["enough"])

    def test_not_enough_samples(self):
        st = reference.stats([rec(T0 - 2 * DAY, 1.0)], "retail_shift", T0)
        self.assertFalse(st["enough"])
        self.assertEqual(cards.reference_line(st), "历史参照：样本不足（1 次）")


class TestRecords(unittest.TestCase):
    def test_add_cards_dedup_and_fill_outcome(self):
        s = helpers.flat_series(T0 + DAY, 400, px=100.0)
        s["OI"][T0 + DAY] = (1000.0, 1000.0 * 110.0)      # 24h 后合约价 110
        card = {"asset": "BTC", "ts": T0, "hits": [{"rule": "retail_shift", "severity": "P1",
                                                    "direction": "up", "magnitude": 20.0}]}
        records = []
        self.assertEqual(reference.add_cards(records, [card, card], {"BTC": s}), 1)
        self.assertEqual(reference.fill_outcomes(records, {"BTC": s}), 1)
        self.assertAlmostEqual(records[0]["fwd_pct"], 10.0)

    def test_prune_keeps_one_year(self):
        records = [rec(T0 - 400 * DAY, 1.0), rec(T0 - 10 * DAY, 1.0)]
        reference.prune(records, T0)
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
