#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""价格卡片渲染单测（离线，不联网）。"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import cards  # noqa: E402

import common  # noqa: E402,F401  (先导入以初始化 libs 路径)


def alert(**kw):
    base = {
        "schema": "alert/v1",
        "severity": "P0",
        "title": "BTC 5 分钟急跌 2.10%，现价 75,412",
        "assets": ["BTC"],
        "detail": ["5m -2.10% · 15m -2.80% · 1h -3.40% · 24h -6.10%",
                   "24h 区间 75,208 – 78,878"],
        "summary": {"why": "急跌通常伴随杠杆清算", "action": "检查杠杆仓位与止损"},
        "metrics": {"spread_bps": 3.1, "n_sources": 3},
        "evidence": [{"source": "binance"}, {"source": "okx"}, {"source": "coinbase"}],
        "rule": "fast_move",
        "ts": "2026-11-08T03:41:00",
        "urls": ["https://www.binance.com/zh-CN/trade/BTC_USDT?type=spot"],
        "escalated": False,
    }
    base.update(kw)
    return base


class TestFormat(unittest.TestCase):
    def test_p0_card_contains_key_facts(self):
        text = cards.format_price_alert(alert())
        self.assertIn("🚨", text)
        self.assertIn("P0", text)
        self.assertIn("BTC", text)
        self.assertIn("75,412", text)
        self.assertIn("-2.10%", text)
        self.assertIn("Binance", text)
        self.assertIn("3.1bps", text)
        self.assertIn("查看行情", text)
        self.assertIn("<a href=", text)
        self.assertIn("【crypto-price】", text)
        self.assertIn("任务：", text)
        self.assertIn("急涨急跌（fast_move）", text)

    def test_p1_icon(self):
        self.assertIn("⚠️", cards.format_price_alert(alert(severity="P1")))

    def test_simulated_banner_and_marker(self):
        text = cards.format_price_alert(alert(simulated=True))
        self.assertIn("【模拟测试】", text)
        self.assertIn("不是真实行情", text)
        self.assertIn("（模拟测试）", text)

    def test_disclaimer_not_duplicated(self):
        text = cards.format_price_alert(alert(
            summary={"why": "x", "action": "检查仓位（不构成投资建议）"}))
        self.assertEqual(text.count("不构成投资建议"), 1)

    def test_escalation_marker(self):
        self.assertIn("加剧", cards.format_price_alert(alert(escalated=True)))

    def test_html_escaped(self):
        text = cards.format_price_alert(alert(title="BTC <跌破> 75,000 & 新低",
                                              detail=["a<b>c"]))
        self.assertIn("&lt;跌破&gt;", text)
        self.assertIn("&amp;", text)
        self.assertIn("a&lt;b&gt;c", text)
        self.assertNotIn("<跌破>", text)

    def test_length_capped(self):
        text = cards.format_price_alert(alert(detail=["很长的细节" * 500]))
        self.assertLessEqual(len(text), 4096)

    def test_volume_spike_card_shows_current_price(self):
        text = cards.format_price_alert(alert(
            rule="volume_spike", severity="P0",
            title="ETH 5 分钟放量 9.4 倍，价格下跌 1.27%，现价 2,190.21",
            detail=["近 5 分钟每分钟均量 759.98 / 基线 80.73（9.4 倍）"],
            metrics={"ratio": 9.4, "change_pct": -1.27, "price": 2190.21,
                     "spread_bps": 22.8, "n_sources": 3},
        ))
        self.assertIn("<b>现价</b>：2,190.21 USDT", text)
        self.assertIn("<b>变动</b>：-1.27%（-27.82 USDT）", text)

    def test_job_label_prefers_launchd_env(self):
        with mock.patch.dict(os.environ, {"CRYPTO_JOB_LABEL": "com.crypto-research.crypto-price"}):
            text = cards.format_price_alert(alert())
        self.assertIn("com.crypto-research.crypto-price", text)

    def test_source_status_3_of_3(self):
        text = cards.format_price_alert(alert(
            source_health={"binance": {"ok": True}, "okx": {"ok": True}, "coinbase": {"ok": True}},
            primary_sources=["binance", "okx", "coinbase"]))
        self.assertIn("多源核验", text)
        self.assertIn("3/3 binance/okx/coinbase（数据正常）", text)

    def test_source_status_3_of_3_with_wide_spread(self):
        text = cards.format_price_alert(alert(
            source_health={"binance": {"ok": True}, "okx": {"ok": True}, "coinbase": {"ok": True}},
            primary_sources=["binance", "okx", "coinbase"],
            metrics={"spread_bps": 51.5, "max_spread_bps": 50.0, "n_sources": 3}))
        self.assertIn("3/3 binance/okx/coinbase（已获取，但价差 51.5bps 偏大）", text)

    def test_source_status_excluded_outlier(self):
        text = cards.format_price_alert(alert(
            source_health={"binance": {"ok": True}, "okx": {"ok": True}, "coinbase": {"ok": True}},
            primary_sources=["binance", "okx", "coinbase"],
            metrics={"spread_bps": 0.2, "excluded_sources": ["coinbase"], "n_sources": 2}))
        self.assertIn("3/3 binance/okx/coinbase（coinbase 偏离已剔除，2 源有效）", text)

    def test_source_status_2_of_3_missing(self):
        text = cards.format_price_alert(alert(
            source_health={"binance": {"ok": True}, "okx": {"ok": True}, "coinbase": {"ok": False}},
            primary_sources=["binance", "okx", "coinbase"]))
        self.assertIn("2/3 binance/okx/coinbase（coinbase 获取异常）", text)

    def test_helpers(self):
        self.assertEqual(cards.fmt_price(75790.5), "75,790.50")
        self.assertEqual(cards.fmt_price(None), "—")
        self.assertEqual(cards.fmt_pct(-2.1), "-2.10%")
        self.assertEqual(cards.fmt_pct(1.234, digits=1), "+1.2%")
        self.assertEqual(cards.fmt_bps(3.14), "3.1bps")

    def test_url_with_query_is_escaped(self):
        text = cards.format_price_alert(alert(urls=["https://x.com/a?b=1&c=2"]))
        self.assertIn("&amp;c=2", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
