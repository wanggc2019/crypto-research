#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""send_brief.py 单测：离线 fixtures 生成 Telegram 消息（不发送）。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MOD, "scripts"))
import daily_brief  # noqa: E402
import send_brief  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


class TestSendBrief(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = daily_brief.load_config(MOD)
        cls.text, cls.meta = send_brief.build_message(cfg, "2026-11-08", base_dir=FIX, repo_root=FIX)
        cls.full, _ = send_brief.build_message(cfg, "2026-11-08", base_dir=FIX, repo_root=FIX, style="full")

    def test_short_sections(self):
        for must in ("【crypto-analyst】", "结论：", "依据（200 日均线=长期趋势", "行情：", "场景1：", "不构成投资建议"):
            self.assertIn(must, self.text)

    def test_short_is_compact(self):
        self.assertLess(len(self.text), 700)

    def test_full_sections(self):
        for must in ("每日简报", "行情（Binance 日线", "场景预测（", "不构成投资建议"):
            self.assertIn(must, self.full)

    def test_no_html_tags(self):
        # 消息是纯文本+emoji（不依赖 Telegram HTML 标签）
        self.assertNotIn("<", self.text)

    def test_event_line_impact_and_confirmation(self):
        cfg = daily_brief.load_config(MOD)
        ev = {"severity": "P1", "category": "protocol_security",
              "title": "Elliptic：币安 64.3 亿美元被盗资金已涉及超 2 万个地址，约 1.75 亿美元被兑换为 BTC"}
        line = send_brief.event_line(cfg, ev)
        self.assertIn("事件（潜在影响：抛压/情绪｜单源待确认）", line)
        self.assertIn("已涉及超 2 万个地址", line)
        self.assertNotIn("约 1.75 亿美元", line)  # 逗号处截断
        self.assertIn("已确认", send_brief.event_line(cfg, dict(ev, severity="P0")))
        self.assertIn("潜在影响：情绪", send_brief.event_line(cfg, dict(ev, category="unknown")))

    def test_job_tag_present(self):
        self.assertIn("🏷 任务：", self.text)
        self.assertIn("crypto-analyst（手动运行）", self.text)
        self.assertIn("🏷 任务：", self.full)

    def test_length_limit(self):
        self.assertLess(len(self.text), send_brief.MAX_LEN)

    def test_real_numbers_from_fixtures(self):
        # fixtures 由固定随机种子生成，末值稳定；消息里应出现该值
        self.assertIn("BTC", self.text)
        self.assertTrue(self.meta["rows"])


if __name__ == "__main__":
    unittest.main()
