#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""signals.py 的离线单测：阈值边界、方向对齐、覆盖不足降级、候选排序。

运行：cd crypto-onchain && ../.venv/bin/python -m unittest tests.test_signals -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import signals  # noqa: E402

RULES = {
    "taker": {
        "p1_ratio": 0.58, "p0_ratio": 0.65,
        "p1_min_large_sells": 3, "p0_min_large_sells": 6,
        "min_window_quote_usd": 500_000,
    },
    "cex_flow": {"p1_net_24h_bps_tvl": 50, "p0_net_24h_bps_tvl": 150, "p1_net_1w_bps_tvl": 400},
    "liquidations": {
        "window_minutes": 15,
        "p1_usd": {"BTC": 30_000_000, "ETH": 15_000_000, "BNB": 8_000_000},
        "p0_usd": {"BTC": 100_000_000, "ETH": 50_000_000, "BNB": 25_000_000},
        "long_bias_ratio": 0.6, "min_coverage_minutes": 5,
    },
    "stablecoin": {"p1_net_issue_24h_usd": -500_000_000, "p0_net_issue_24h_usd": -1_500_000_000,
                   "p0_requires_btc_drop_pct": 3.0},
}


def taker_stats(ratio, large=3, volume=10_000_000):
    return {"sell_ratio": ratio, "large_sell_count": large, "quote_volume_usd": volume,
            "large_sell_usd": 1.0, "window_change_pct": -0.5, "sell_quote_usd": 1.0}


class TestTakerSignal(unittest.TestCase):
    def test_p1_when_ratio_and_large_sells(self):
        c = signals.eval_taker("BTC", taker_stats(0.60), RULES, "down")
        self.assertIsNotNone(c)
        self.assertEqual(c["severity"], "P1")
        self.assertTrue(c["would_alert"])

    def test_p0_requires_six_large_sells(self):
        c = signals.eval_taker("BTC", taker_stats(0.70, large=3), RULES, "down")
        self.assertEqual(c["severity"], "P1")
        c2 = signals.eval_taker("BTC", taker_stats(0.70, large=6), RULES, "down")
        self.assertEqual(c2["severity"], "P0")

    def test_below_threshold_is_none(self):
        self.assertIsNone(signals.eval_taker("BTC", taker_stats(0.50), RULES, "down"))
        self.assertIsNone(signals.eval_taker("BTC", taker_stats(0.60, large=2), RULES, "down"))

    def test_insufficient_sample(self):
        stats = taker_stats(0.70, large=6, volume=100_000)
        self.assertIsNone(signals.eval_taker("BTC", stats, RULES, "down"))

    def test_min_volume_per_asset(self):
        rules = dict(RULES)
        rules["taker"] = dict(RULES["taker"])
        rules["taker"]["min_window_quote_usd"] = {"BTC": 2_000_000, "BNB": 200_000}
        # 同样 30 万美元成交额：BTC 样本不足，BNB 足够
        stats = taker_stats(0.70, large=6, volume=300_000)
        self.assertIsNone(signals.eval_taker("BTC", stats, rules, "down"))
        self.assertIsNotNone(signals.eval_taker("BNB", stats, rules, "down"))

    def test_require_price_direction_off(self):
        rules = dict(RULES)
        rules["taker"] = dict(RULES["taker"])
        rules["taker"]["require_price_direction"] = False
        c = signals.eval_taker("BTC", taker_stats(0.60), rules, "up")
        self.assertIsNone(c["aligned"])
        self.assertTrue(c["would_alert"])

    def test_alignment(self):
        c = signals.eval_taker("BTC", taker_stats(0.60), RULES, "up")
        self.assertFalse(c["aligned"])
        self.assertFalse(c["would_alert"])
        self.assertEqual(c["severity"], "P1")  # 阈值仍记录，供影子期评估
        c2 = signals.eval_taker("BTC", taker_stats(0.60), RULES, "unknown")
        self.assertIsNone(c2["aligned"])

    def test_note_has_no_identity_claim(self):
        c = signals.eval_taker("BTC", taker_stats(0.60), RULES, "down")
        for word in ("机构", "巨鲸", "某个人", "某基金"):
            self.assertNotIn(word, c["note"])


class TestCexSignal(unittest.TestCase):
    def flows(self, bps, n=3, net_1w_bps=0):
        return {"net_24h_bps": bps, "n_eligible": n,
                "net_24h_usd": bps * 1_000_000, "net_1w_bps": net_1w_bps,
                "net_1w_usd": 0, "tvl_usd": 100_000_000_000,
                "exchanges": [{"name": "Binance", "eligible": True}]}

    def test_p1_outflow(self):
        c = signals.eval_cex_flow(self.flows(-80), RULES, "down")
        self.assertEqual(c["severity"], "P1")
        self.assertTrue(c["aligned"])

    def test_below_threshold(self):
        self.assertIsNone(signals.eval_cex_flow(self.flows(-20), RULES, "down"))
        self.assertIsNone(signals.eval_cex_flow(self.flows(-80, n=0), RULES, "down"))

    def test_weekly_outflow_only(self):
        c = signals.eval_cex_flow(self.flows(-10, net_1w_bps=-450), RULES, "down")
        self.assertEqual(c["severity"], "P1")

    def test_p0(self):
        c = signals.eval_cex_flow(self.flows(-200), RULES, "down")
        self.assertEqual(c["severity"], "P0")


class TestLiquidationSignal(unittest.TestCase):
    def liq(self, total, coverage=8.0, long_ratio=0.8):
        return {"total_usd": total, "long_usd": total * long_ratio,
                "short_usd": total * (1 - long_ratio), "long_ratio": long_ratio,
                "coverage_minutes": coverage, "usd_known": True}

    def test_p1_and_category(self):
        c = signals.eval_liquidation("BTC", self.liq(40_000_000), RULES, "down")
        self.assertEqual(c["severity"], "P1")
        self.assertIn("多头被强平", c["note"])
        self.assertTrue(c["aligned"])

    def test_coverage_too_short(self):
        self.assertIsNone(signals.eval_liquidation("BTC", self.liq(40_000_000, coverage=2.0), RULES, "down"))

    def test_single_event_coverage_unknown(self):
        # 只有 1 条清算 → coverage=None，无法判断是否瀑布，按样本不足丢弃
        self.assertIsNone(signals.eval_liquidation("BTC", self.liq(40_000_000, coverage=None), RULES, "down"))

    def test_below_threshold(self):
        self.assertIsNone(signals.eval_liquidation("BTC", self.liq(10_000_000), RULES, "down"))

    def test_short_bias_implies_up(self):
        c = signals.eval_liquidation("BTC", self.liq(40_000_000, long_ratio=0.2), RULES, "down")
        self.assertIn("空头被强平", c["note"])
        self.assertFalse(c["aligned"])


class TestStablecoinSignal(unittest.TestCase):
    def stable(self, net):
        return {"n": 4, "net_issue_24h_usd": net, "net_issue_1w_usd": net * 2,
                "circulating_usd": 200_000_000_000,
                "symbols": [{"symbol": "USDT"}, {"symbol": "USDC"}]}

    def test_p1(self):
        c = signals.eval_stablecoin(self.stable(-600_000_000), RULES, -2.0)
        self.assertEqual(c["severity"], "P1")
        self.assertTrue(c["aligned"])

    def test_p0_needs_btc_drop(self):
        c = signals.eval_stablecoin(self.stable(-1_600_000_000), RULES, -1.0)
        self.assertEqual(c["severity"], "P1")
        c2 = signals.eval_stablecoin(self.stable(-1_600_000_000), RULES, -4.0)
        self.assertEqual(c2["severity"], "P0")

    def test_net_issuance_is_not_candidate(self):
        self.assertIsNone(signals.eval_stablecoin(self.stable(300_000_000), RULES, -2.0))


class TestBuildCandidates(unittest.TestCase):
    def test_sorting_and_scope(self):
        assets = {
            "BTC": {"taker_5m": taker_stats(0.70, large=6), "liq_15m": {"total_usd": 120_000_000,
                    "long_ratio": 0.9, "coverage_minutes": 8.0, "usd_known": True, "long_usd": 108_000_000,
                    "short_usd": 12_000_000}},
            "ETH": {"taker_5m": taker_stats(0.10), "liq_15m": {"total_usd": 0, "usd_known": True,
                    "coverage_minutes": 8.0, "long_ratio": None}},
        }
        flows = {"net_24h_bps": -60, "n_eligible": 2, "net_24h_usd": -1e9, "net_1w_bps": 0,
                 "net_1w_usd": 0, "tvl_usd": 1e11, "exchanges": []}
        stable = {"n": 2, "net_issue_24h_usd": -600_000_000, "net_issue_1w_usd": -1e9,
                  "circulating_usd": 2e11, "symbols": [{"symbol": "USDT"}]}
        out = signals.build_candidates(assets, flows, stable, RULES, {"BTC": "down", "ETH": "down"})
        # BTC 主动卖压 P0 + BTC 清算 P0 + CEX 净流 P1 + 稳定币 P1
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0]["severity"], "P0")
        self.assertEqual(out[1]["severity"], "P0")

    def test_empty(self):
        self.assertEqual(signals.build_candidates({}, {}, {}, RULES, {}), [])


if __name__ == "__main__":
    unittest.main()
