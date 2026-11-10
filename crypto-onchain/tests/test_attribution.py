#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""attribution.py 的离线单测：4 类归因、置信度随 OI 升降、反证与不可知段。

运行：cd crypto-onchain && ../.venv/bin/python -m unittest tests.test_attribution -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import attribution  # noqa: E402

RULES = {
    "taker": {
        "p1_ratio": 0.58, "min_window_quote_usd": {"BTC": 2_000_000},
    },
    "liquidations": {
        "p1_usd": {"BTC": 30_000_000, "ETH": 15_000_000, "BNB": 8_000_000},
        "long_bias_ratio": 0.6,
    },
    "cex_flow": {"p1_net_24h_bps_tvl": 50},
    "stablecoin": {"p1_net_issue_24h_usd": -500_000_000},
    "attribution": {"enabled": True, "oi_significant_pct": 2.0},
}


def row(**over):
    base = {
        "direction": "down",
        "change_24h_pct": -2.3,
        "taker_5m": {"sell_ratio": 0.62, "quote_volume_usd": 5_000_000, "large_sell_count": 2},
        "liq_15m": {"total_usd": 0.0, "long_ratio": None, "coverage_minutes": 8.0},
        "funding_rate": -0.0001,
    }
    base.update(over)
    return base


CEX_OUT = {"net_24h_bps": -80, "net_24h_usd": -1_000_000_000}
CEX_FLAT = {"net_24h_bps": -10, "net_24h_usd": -100_000_000}
ST_OUT = {"net_issue_24h_usd": -600_000_000, "n": 4}


class TestAssetAttribution(unittest.TestCase):
    def test_long_liquidation_high_with_oi_drop(self):
        r = row(liq_15m={"total_usd": 40_000_000, "long_ratio": 0.9, "coverage_minutes": 8.0})
        a = attribution.attribute_asset("BTC", r, RULES, CEX_OUT, ST_OUT, oi_change_pct=-2.9)
        self.assertEqual(a["category_code"], attribution.LONG_LIQ)
        self.assertEqual(a["confidence"], "high")
        self.assertTrue(any("清算" in e for e in a["evidence"]))
        self.assertTrue(any("OI" in e for e in a["counter_evidence"]))

    def test_long_liquidation_medium_without_oi(self):
        r = row(liq_15m={"total_usd": 40_000_000, "long_ratio": 0.9, "coverage_minutes": 8.0})
        a = attribution.attribute_asset("BTC", r, RULES, CEX_OUT, ST_OUT, oi_change_pct=None)
        self.assertEqual(a["category_code"], attribution.LONG_LIQ)
        self.assertEqual(a["confidence"], "medium")

    def test_long_liquidation_low_when_oi_rises(self):
        r = row(liq_15m={"total_usd": 40_000_000, "long_ratio": 0.9, "coverage_minutes": 8.0})
        a = attribution.attribute_asset("BTC", r, RULES, CEX_OUT, ST_OUT, oi_change_pct=2.5)
        self.assertEqual(a["category_code"], attribution.LONG_LIQ)
        self.assertEqual(a["confidence"], "low")

    def test_short_add_with_oi_rise_and_negative_funding(self):
        r = row(liq_15m={"total_usd": 0.0, "long_ratio": None, "coverage_minutes": 8.0})
        a = attribution.attribute_asset("BTC", r, RULES, CEX_FLAT, {"n": 0}, oi_change_pct=2.5)
        self.assertEqual(a["category_code"], attribution.SHORT_ADD)
        self.assertEqual(a["confidence"], "high")

    def test_capital_exit_when_market_flows_negative(self):
        r = row(taker_5m={"sell_ratio": 0.45, "quote_volume_usd": 5_000_000, "large_sell_count": 0},
                liq_15m={"total_usd": 0.0, "long_ratio": None, "coverage_minutes": 8.0})
        a = attribution.attribute_asset("BTC", r, RULES, CEX_OUT, ST_OUT, oi_change_pct=None)
        self.assertEqual(a["category_code"], attribution.CAPITAL_EXIT)
        self.assertIn("CEX 净流", " ".join(a["counter_evidence"]))

    def test_deleverage_counter_conclusion(self):
        r = row(taker_5m={"sell_ratio": 0.45, "quote_volume_usd": 5_000_000, "large_sell_count": 0},
                liq_15m={"total_usd": 0.0, "long_ratio": None, "coverage_minutes": 8.0})
        a = attribution.attribute_asset("BTC", r, RULES, CEX_FLAT, {"n": 0}, oi_change_pct=-3.2)
        self.assertEqual(a["category_code"], attribution.DELEVERAGE)
        self.assertIn("去杠杆", a["category"])

    def test_price_up_returns_none(self):
        r = row(direction="up", change_24h_pct=3.0)
        self.assertIsNone(attribution.attribute_asset("BTC", r, RULES, CEX_OUT, ST_OUT, oi_change_pct=-3.0))

    def test_disabled_returns_none(self):
        rules = dict(RULES)
        rules["attribution"] = {"enabled": False}
        r = row()
        self.assertIsNone(attribution.attribute_asset("BTC", r, rules, CEX_OUT, ST_OUT, oi_change_pct=None))

    def test_small_sample_taker_ignored(self):
        # 成交额低于该资产最小样本 → 主动卖占比不参与，避免小样本误判
        r = row(taker_5m={"sell_ratio": 0.9, "quote_volume_usd": 100_000, "large_sell_count": 0},
                liq_15m={"total_usd": 0.0, "long_ratio": None, "coverage_minutes": 8.0})
        a = attribution.attribute_asset("BTC", r, RULES, CEX_OUT, ST_OUT, oi_change_pct=None)
        self.assertEqual(a["category_code"], attribution.CAPITAL_EXIT)

    def test_no_identity_words(self):
        r = row(liq_15m={"total_usd": 40_000_000, "long_ratio": 0.9, "coverage_minutes": 8.0})
        a = attribution.attribute_asset("BTC", r, RULES, CEX_OUT, ST_OUT, oi_change_pct=-2.9)
        text = " ".join(a["evidence"] + a["counter_evidence"]) + a["unknown"]
        for word in ("某机构", "某巨鲸", "某个人", "某基金"):
            self.assertNotIn(word, text)


class TestMarketAttribution(unittest.TestCase):
    def test_capital_exit_market_level(self):
        a = attribution.attribute_market(CEX_OUT, ST_OUT, "down")
        self.assertEqual(a["category_code"], attribution.CAPITAL_EXIT)

    def test_market_none_when_price_up(self):
        self.assertIsNone(attribution.attribute_market(CEX_OUT, ST_OUT, "up"))

    def test_market_below_threshold_and_switches(self):
        self.assertIsNone(attribution.attribute_market(CEX_FLAT, ST_OUT, "down"))
        # enabled=false 时市场级也关闭；阈值从 rules 读取（调高后同一流量不触发）
        off = {"attribution": {"enabled": False}}
        self.assertIsNone(attribution.attribute_market(CEX_OUT, ST_OUT, "down", rules=off))
        strict = dict(RULES)
        strict["cex_flow"] = {"p1_net_24h_bps_tvl": 200}
        self.assertIsNone(attribution.attribute_market(CEX_OUT, ST_OUT, "down", rules=strict))


class TestBuildAttributions(unittest.TestCase):
    def test_shape(self):
        assets = {"BTC": row(liq_15m={"total_usd": 40_000_000, "long_ratio": 0.9,
                                      "coverage_minutes": 8.0})}
        out = attribution.build_attributions(
            assets, CEX_OUT, ST_OUT, RULES, {"BTC": "down"}, oi_changes={"BTC": -2.9},
        )
        self.assertIn("assets", out)
        self.assertEqual(out["assets"]["BTC"]["category_code"], attribution.LONG_LIQ)
        self.assertEqual(out["market"]["category_code"], attribution.CAPITAL_EXIT)

    def test_empty(self):
        out = attribution.build_attributions({}, {}, {}, RULES, {})
        self.assertIsNone(out["market"])
        self.assertEqual(out["assets"], {})


if __name__ == "__main__":
    unittest.main()
