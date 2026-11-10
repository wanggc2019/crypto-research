#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""indicators.py 的离线单测：主动买卖、CEX 净流、稳定币净增发、清算折算。

运行：cd crypto-onchain && ../.venv/bin/python -m unittest tests.test_indicators -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import indicators  # noqa: E402

NOW = 1794177000


def trade(ts, usd, side, price=100.0):
    return {"ts": ts, "price": price, "qty": usd / price, "quote_qty": usd,
            "taker_side": side, "id": ts}


class TestTakerStats(unittest.TestCase):
    def test_ratio_and_large_trades(self):
        trades = [
            trade(NOW - 280, 6_000_000, "sell"),
            trade(NOW - 240, 2_000_000, "buy"),
            trade(NOW - 120, 5_500_000, "sell"),
            trade(NOW - 60, 1_000_000, "sell"),
            trade(NOW - 20, 500_000, "buy"),
            trade(NOW - 600, 9_000_000, "sell"),   # 窗口外（15m 内、5m 外）
        ]
        s = indicators.taker_stats(trades, NOW, 300, large_trade_usd=5_000_000)
        self.assertEqual(s["n_trades"], 5)
        self.assertAlmostEqual(s["quote_volume_usd"], 15_000_000)
        self.assertAlmostEqual(s["sell_ratio"], 12_500_000 / 15_000_000)
        self.assertEqual(s["large_sell_count"], 2)
        self.assertAlmostEqual(s["large_sell_usd"], 11_500_000)

    def test_window_moves_with_now(self):
        trades = [trade(NOW - 700, 1_000_000, "sell"), trade(NOW - 60, 1_000_000, "buy")]
        s5 = indicators.taker_stats(trades, NOW, 300)
        s15 = indicators.taker_stats(trades, NOW, 900)
        self.assertEqual(s5["n_trades"], 1)
        self.assertEqual(s15["n_trades"], 2)
        self.assertAlmostEqual(s15["sell_ratio"], 0.5)

    def test_empty_and_zero_volume(self):
        s = indicators.taker_stats([], NOW, 300)
        self.assertEqual(s["n_trades"], 0)
        self.assertEqual(s["quote_volume_usd"], 0.0)
        self.assertIsNone(s["sell_ratio"])

    def test_bad_rows_are_skipped(self):
        trades = [None, {"ts": "x"}, {"ts": NOW - 10, "quote_qty": 1.0, "taker_side": "sell"}]
        s = indicators.taker_stats(trades, NOW, 300)
        self.assertEqual(s["n_trades"], 1)


class TestDirection(unittest.TestCase):
    def test_direction_of(self):
        self.assertEqual(indicators.direction_of(-2.5), "down")
        self.assertEqual(indicators.direction_of(1.2), "up")
        self.assertEqual(indicators.direction_of(0.05), "flat")
        self.assertEqual(indicators.direction_of(None), "unknown")
        self.assertEqual(indicators.direction_of("bad"), "unknown")


class TestOIChange(unittest.TestCase):
    def test_change_from_history(self):
        points = [[NOW - 1000, 100.0], [NOW - 600, 105.0], [NOW, 97.0]]
        # 目标 = NOW-900；取 <= 目标且距离 <= 10 分钟的点 → NOW-1000
        self.assertAlmostEqual(indicators.oi_change_pct(points, NOW, 15), (97.0 - 100.0) / 100.0 * 100)

    def test_insufficient_history(self):
        self.assertIsNone(indicators.oi_change_pct([[NOW - 120, 100.0], [NOW, 99.0]], NOW, 15))

    def test_tolerance_exceeded(self):
        self.assertIsNone(indicators.oi_change_pct([[NOW - 3000, 100.0], [NOW, 99.0]], NOW, 15))

    def test_too_few_points(self):
        self.assertIsNone(indicators.oi_change_pct([[NOW, 99.0]], NOW, 15))
        self.assertIsNone(indicators.oi_change_pct([], NOW, 15))

    def test_dict_points_and_bad_rows(self):
        points = [{"ts": NOW - 950, "oi": 200.0}, {"ts": NOW, "oi": 190.0}, {"ts": "x"}, None]
        self.assertAlmostEqual(indicators.oi_change_pct(points, NOW, 15), -5.0)


class TestCexFlow(unittest.TestCase):
    def rows(self):
        return [
            {"name": "Binance", "tvl": 100_000_000_000.0, "inflows_24h": -1_000_000_000.0,
             "inflows_1w": -2_000_000_000.0, "inflows_1m": -3_000_000_000.0},
            {"name": "OKX", "tvl": 20_000_000_000.0, "inflows_24h": 200_000_000.0,
             "inflows_1w": 0.0, "inflows_1m": 0.0},
            {"name": "Small", "tvl": 100_000_000.0, "inflows_24h": -90_000_000.0,
             "inflows_1w": 0.0, "inflows_1m": 0.0},
        ]

    def test_bps_and_min_tvl(self):
        m = indicators.cex_flow_metrics(self.rows(), ["Binance", "OKX"], min_tvl_usd=1_000_000_000)
        self.assertEqual(m["n_eligible"], 2)
        self.assertAlmostEqual(m["tvl_usd"], 120_000_000_000.0)
        self.assertAlmostEqual(m["net_24h_usd"], -800_000_000.0)
        self.assertAlmostEqual(m["net_24h_bps"], -800_000_000.0 / 120_000_000_000.0 * 10000)
        binance = m["exchanges"][0]
        self.assertAlmostEqual(binance["net_24h_bps"], -100.0)

    def test_watch_filter(self):
        m = indicators.cex_flow_metrics(self.rows(), ["OKX"])
        self.assertEqual([e["name"] for e in m["exchanges"]], ["OKX"])

    def test_missing_tvl(self):
        rows = [{"name": "Binance", "tvl": None, "inflows_24h": -1_000_000_000.0}]
        m = indicators.cex_flow_metrics(rows, ["Binance"], min_tvl_usd=0)
        self.assertEqual(m["n_eligible"], 0)
        self.assertIsNone(m["net_24h_bps"])


class TestStablecoin(unittest.TestCase):
    def test_net_issue(self):
        rows = [
            {"symbol": "USDT", "circulating": 100.0, "net_issue_24h": -5.0, "net_issue_1w": -10.0},
            {"symbol": "USDC", "circulating": 50.0, "net_issue_24h": -2.0, "net_issue_1w": None},
        ]
        m = indicators.stablecoin_metrics(rows)
        self.assertEqual(m["n"], 2)
        self.assertAlmostEqual(m["circulating_usd"], 150.0)
        self.assertAlmostEqual(m["net_issue_24h_usd"], -7.0)
        self.assertAlmostEqual(m["net_issue_1w_usd"], -10.0)

    def test_empty(self):
        m = indicators.stablecoin_metrics([])
        self.assertEqual(m["n"], 0)
        self.assertEqual(m["net_issue_24h_usd"], 0.0)


class TestLiquidation(unittest.TestCase):
    def ev(self, ts, sz, pos_side="long"):
        return {"ts": ts, "size": sz, "pos_side": pos_side, "side": "sell" if pos_side == "long" else "buy",
                "inst_id": "BTC-USDT-SWAP", "uly": "BTC-USDT", "price": 74400.0}

    def test_usd_conversion_and_bias(self):
        events = [self.ev(NOW - 600, 10000), self.ev(NOW - 300, 10000), self.ev(NOW - 120, 5000),
                  self.ev(NOW - 60, 5000, "short")]
        m = indicators.liquidation_metrics(events, 0.01, 74400.0, NOW, window_minutes=15)
        self.assertTrue(m["usd_known"])
        self.assertAlmostEqual(m["long_usd"], 25000 * 0.01 * 74400.0)
        self.assertAlmostEqual(m["short_usd"], 5000 * 0.01 * 74400.0)
        self.assertGreater(m["long_ratio"], 0.8)
        self.assertAlmostEqual(m["coverage_minutes"], 9.0)

    def test_window_filter(self):
        events = [self.ev(NOW - 3600, 10000), self.ev(NOW - 60, 10000)]
        m = indicators.liquidation_metrics(events, 0.01, 74400.0, NOW, window_minutes=15)
        self.assertEqual(m["n"], 1)
        self.assertEqual(m["n_total"], 2)

    def test_unknown_price(self):
        m = indicators.liquidation_metrics([self.ev(NOW - 60, 10000)], 0.01, None, NOW)
        self.assertFalse(m["usd_known"])
        self.assertEqual(m["total_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
