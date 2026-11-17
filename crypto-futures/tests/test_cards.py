#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡片：需求 §5.3 三个样例时点用真实回放数据渲染，检查区块齐全、顺序、长度与用词。"""
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import backtest  # noqa: E402
import cards  # noqa: E402
import engine  # noqa: E402
import helpers  # noqa: E402

import common  # noqa: E402

FORBIDDEN = ("多源核验", "开多", "抄底", "减仓")
_CACHE = {}


def fixture_series():
    if "s" not in _CACHE:
        _CACHE["s"] = backtest.load_series(helpers.FIXTURES, common.load_config("app.yaml")["assets"])
    return _CACHE["s"]


def card_at(asset, ts):
    """在 ts 这一点单独评估该资产，返回卡片（不经过冷却历史）。"""
    s = fixture_series()[asset]
    found, _ = engine.step({asset: s}, engine.new_state(), helpers.rules_cfg(), ts, catchup_s=300)
    return next(c for c in found if c["ts"] == ts)


def render(asset, ts, funding, ref=None):
    card = card_at(asset, ts)
    ctx = cards.context(fixture_series()[asset], ts, funding)
    return card, cards.format_card(card, ctx, ref or {"n": 2, "enough": False}, link="https://example.test/x?a=1&b=2",
                                   job="com.crypto-research.crypto-futures")


class TestPositionWording(unittest.TestCase):
    VALUES = [float(i) for i in range(1, 1001)]       # 1..1000

    def test_extremes(self):
        self.assertEqual(cards._position(self.VALUES, 1000.0), "7 天最高")
        self.assertEqual(cards._position(self.VALUES, 0.5), "7 天最低")

    def test_near_bottom_is_not_shown_as_zero_percent(self):
        self.assertEqual(cards._position(self.VALUES, 5.0), "7 天最低 1% 以内")    # 0.5%：不再显示「0% 分位」
        self.assertEqual(cards._position(self.VALUES, 10.0), "7 天 1% 分位")

    def test_near_top_symmetric(self):
        self.assertEqual(cards._position(self.VALUES, 995.0), "7 天最高 1% 以内")
        self.assertEqual(cards._position(self.VALUES, 970.0), "7 天 97% 分位")

    def test_empty_window(self):
        self.assertEqual(cards._position([], 1.0), "")


class TestSampleCards(unittest.TestCase):
    def check_structure(self, text):
        plain = re.sub(r"<[^>]+>", "", text)
        order = ["【crypto-futures】", "• ", "背景：合约价", "解读：", "历史参照：", "口径：Binance USD-M 单所 · 5m 数据 · 截至",
                 "⏱", "🏷 任务：", "不构成投资建议"]
        positions = [plain.index(x) for x in order]
        self.assertEqual(positions, sorted(positions), "区块顺序不对")
        bg = plain[plain.index("背景："):].split("\n", 1)[0]
        self.assertLess(bg.index("合约价"), bg.index("OI"))
        self.assertLess(bg.index("OI"), bg.index("资金费率"))
        self.assertLessEqual(cards.visible_len(text), cards.MAX_CHARS)
        for word in FORBIDDEN:
            self.assertNotIn(word, plain)
        self.assertIn('href="https://example.test/x?a=1&amp;b=2"', text)

    def test_bnb_p0_crowding(self):
        card, text = render("BNB", helpers.BNB_P0, 0.000086)
        self.assertEqual((card["rule"], card["severity"]), ("crowding_build", "P0"))
        self.check_structure(text)
        self.assertIn("🚨【crypto-futures】<b>P0 · BNB 拥挤加杠杆</b>", text)
        self.assertIn("4h 内 OI 增加 6.7%", text)
        self.assertIn("资金费率 0.0086%", text)

    def test_btc_divergence(self):
        card, text = render("BTC", helpers.BTC_DIV, 0.000043)
        self.assertEqual(card["rule"], "smart_divergence")
        self.check_structure(text)
        self.assertIn("可能减多，也可能加空", text)
        self.assertIn("合约价 77,301", text)

    def test_eth_retail_shift(self):
        card, text = render("ETH", helpers.ETH_SHIFT, 0.000057)
        self.assertEqual(card["rule"], "retail_shift")
        self.check_structure(text)
        self.assertIn("账户多空比 2.46（1h 前 2.03，+21%）", text)

    def test_reference_line_with_enough_samples(self):
        ref = {"n": 8, "up": 2, "down": 6, "median": -0.9, "enough": True, "lookback_days": 30}
        _, text = render("ETH", helpers.ETH_SHIFT, None, ref)
        self.assertIn("近 30 天同规则（3 币）8 次，之后 24h 涨 2 / 跌 6，中位数 −0.9%", text)
        self.assertIn("资金费率 —", text)                 # 缺值显示「—」

    def test_trend_prefix_has_comma(self):
        _, text = render("ETH", helpers.ETH_SHIFT, 0.000057)
        self.assertIn("解读：下跌中，做多的账户", text)

    def test_long_text_is_cut_in_reading_not_footer(self):
        card = card_at("ETH", helpers.ETH_SHIFT)
        ctx = cards.context(fixture_series()["ETH"], helpers.ETH_SHIFT)
        with mock.patch.dict(cards.READING, {("retail_shift", "up"): "很长的解读。" * 200}):
            text = cards.format_card(card, ctx, {"n": 0})
        self.assertLessEqual(cards.visible_len(text), cards.MAX_CHARS)
        self.assertTrue(text.endswith("⚠️ 研究用途，不构成投资建议"))
        self.assertIn("…", text)

    def test_escalated_and_simulated_badges(self):
        card = dict(card_at("ETH", helpers.ETH_SHIFT), escalated=True)
        ctx = cards.context(fixture_series()["ETH"], helpers.ETH_SHIFT)
        text = cards.format_card(card, ctx, {"n": 0}, simulated=True)
        self.assertIn("加剧", text)
        self.assertIn("回放样例", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
