# -*- coding: utf-8 -*-
"""text.cn_summary：英文财经标题的中文解读（规则版）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypto_core.text import cn_summary, html_to_text, looks_like_junk_page  # noqa: E402


class TestHtmlToText(unittest.TestCase):
    def test_strips_scripts_styles_and_tags(self):
        raw = "<html><head><style>.x{color:red}</style></head><body><script>var a=1;</script><div>Hello <b>world</b></div></body></html>"
        self.assertEqual(html_to_text(raw), "Hello world")

    def test_junk_page_detection(self):
        self.assertTrue(looks_like_junk_page("<style>--chakra-ring: 1</style>"))
        self.assertTrue(looks_like_junk_page('{"@context":"https://schema.org"}'))
        self.assertFalse(looks_like_junk_page("BNB Chain announces a network upgrade on 2026-11-15. " * 10))


class TestCnSummary(unittest.TestCase):
    def test_liquidation_headline(self):
        t = "Bitcoin loses 50-week moving average as 24-hour crypto long liquidations top $1 billion"
        out = cn_summary(t)
        self.assertIn("BTC", out)
        self.assertIn("50 周均线", out)
        self.assertIn("10 亿美元", out)

    def test_selloff_headline(self):
        out = cn_summary("Nvidia-led tech selloff weighs on crypto majors")
        self.assertIn("科技股抛售", out)
        self.assertIn("加密主流币", out)

    def test_macro_headline(self):
        out = cn_summary("Fed cuts rates as CPI cools")
        self.assertIn("美联储降息", out)
        self.assertIn("CPI 降温", out)

    def test_etf_record(self):
        out = cn_summary("ETF outflows hit record high")
        self.assertIn("ETF 净流出创纪录", out)

    def test_laundering_headline(self):
        out = cn_summary("Binance hack launderers mint homemade Elon Musk, animal memecoins to move stolen funds: Elliptic")
        self.assertIn("黑客", out)
        self.assertIn("被盗资金", out)

    def test_unknown_english_gets_word_level_fallback(self):
        out = cn_summary("Ethereum treasury Sharplink’s Q3 net income hits $751 m")
        self.assertIn("ETH", out)
        self.assertIn("净利润", out)

    def test_chinese_title_returns_empty(self):
        self.assertEqual(cn_summary("币安 CFO 离职"), "")

    def test_plain_english_gets_word_level_fallback(self):
        out = cn_summary("Apple unveils new iPhone")
        self.assertIn("发布", out)


if __name__ == "__main__":
    unittest.main()
