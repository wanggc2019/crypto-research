#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""context.py 单测：结合价格/新闻/资金面判断"值不值得发"。"""
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import context  # noqa: E402

CFG = {
    "p0_usd": 50_000_000, "p0_cex_in_usd": 25_000_000,
    "whale_usd": 100_000_000, "whale_cex_in_usd": 50_000_000,
    "smart": {"enabled": True, "min_score": 2, "stress_move_24h_pct": 3.0,
              "news_lookback_hours": 6, "cex_outflow_bps": 100,
              "stablecoin_outflow_usd": 500_000_000},
}
NOW = datetime.datetime(2026, 11, 10, 15, 0)


def rec(usd=60_000_000, direction="unknown"):
    return {"tx": "0xabc", "log_index": 0, "usd": usd, "direction": direction,
            "from": "0x1", "to": "0x2", "symbol": "USDT", "chain": "ethereum"}


def news(sev="P1", title="Binance 被黑客攻击", ts="2026-11-10T14:30:00+04:00"):
    return {"severity": sev, "title": title, "category": "exchange",
            "published_at": ts, "hard": []}


class TestAssess(unittest.TestCase):
    def test_whale_always_send(self):
        v = context.assess(rec(150_000_000), CFG, {}, NOW)
        self.assertTrue(v["send"])
        self.assertTrue(v["whale"])

    def test_flat_market_low_context_suppressed(self):
        ctx = {"assets": {"BTC": {"change_24h_pct": 0.2}}}
        v = context.assess(rec(60_000_000), CFG, ctx, NOW)
        self.assertFalse(v["send"])
        self.assertEqual(v["score"], 0)

    def test_price_stress_sends(self):
        ctx = {"assets": {"BTC": {"change_24h_pct": -4.2}}}
        v = context.assess(rec(60_000_000), CFG, ctx, NOW)
        self.assertTrue(v["send"])
        self.assertGreaterEqual(v["score"], 2)
        self.assertTrue(any("BTC" in r for r in v["reasons"]))

    def test_news_p1_keyword_sends(self):
        ctx = {"assets": {"BTC": {"change_24h_pct": 0.1}}, "news": [news()]}
        v = context.assess(rec(60_000_000), CFG, ctx, NOW)
        self.assertTrue(v["send"])
        self.assertTrue(any("新闻" in r for r in v["reasons"]))

    def test_news_p2_ignored(self):
        ctx = {"news": [news(sev="P2")]}
        v = context.assess(rec(60_000_000), CFG, ctx, NOW)
        self.assertFalse(v["send"])

    def test_old_news_ignored(self):
        ctx = {"news": [news(ts="2026-11-10T06:00:00+04:00")]}
        v = context.assess(rec(60_000_000), CFG, ctx, NOW)
        self.assertFalse(v["send"])

    def test_flows_bonus(self):
        ctx = {"cex_net_24h_bps": -150, "stablecoin_net_issue_24h_usd": -800_000_000}
        v = context.assess(rec(60_000_000), CFG, ctx, NOW)
        self.assertTrue(v["send"])
        self.assertIn("CEX 24h 净流出", "".join(v["reasons"]))

    def test_cex_in_with_drop(self):
        # 40M 入所未达巨鲸线（50M），BTC -3.5% 触发价格压力 + 入所加成
        ctx = {"assets": {"BTC": {"change_24h_pct": -3.5}}}
        v = context.assess(rec(40_000_000, direction="cex_in"), CFG, ctx, NOW)
        self.assertTrue(v["send"])
        self.assertTrue(any("转入交易所" in r for r in v["reasons"]))

    def test_disabled_smart_sends_everything(self):
        cfg = dict(CFG, smart={"enabled": False})
        v = context.assess(rec(60_000_000), cfg, {}, NOW)
        self.assertTrue(v["send"])


if __name__ == "__main__":
    unittest.main()
