#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""notify.py 的单元测试（不联网）。"""
import datetime
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import notify  # noqa: E402


class TestEnv(unittest.TestCase):
    def test_load_and_save(self):
        with tempfile.NamedTemporaryFile("w+", suffix=".env", delete=False) as f:
            f.write("# comment\nA=1\nB='x y'\n")
            path = f.name
        try:
            env = notify.load_env(path)
            self.assertEqual(env["A"], "1")
            self.assertEqual(env["B"], "x y")
            notify.save_env_value("C", "3", path)
            self.assertEqual(notify.load_env(path)["C"], "3")
            notify.save_env_value("A", "9", path)
            self.assertEqual(notify.load_env(path)["A"], "9")
        finally:
            os.unlink(path)

    def test_check_credentials(self):
        ok_token = "123456789:" + "A" * 35
        _, _, problems = notify.check_credentials({"TELEGRAM_BOT_TOKEN": ok_token, "TELEGRAM_CHAT_ID": "123"})
        self.assertEqual(problems, [])
        _, _, bad = notify.check_credentials({"TELEGRAM_BOT_TOKEN": "你的token", "TELEGRAM_CHAT_ID": "你的chat_id"})
        self.assertEqual(len(bad), 2)


class TestFormat(unittest.TestCase):
    def _alert(self, level="P0"):
        return {
            "level": level, "category": "exchange_custody", "titles": ["Binance <suspends> withdrawals"],
            "assets": ["BNB", "BTC"], "count": 2, "age_hours": 3.5, "score": 88.0,
            "components": {"S": 100, "T": 90, "M": 80, "C": 50, "D": 0.9},
            "hard": ["exchange_withdrawal"], "url": "https://example.com/a?x=1&y=2",
        }

    def test_p0_card(self):
        text = notify.format_alert(self._alert("P0"))
        self.assertIn("🚨", text)
        self.assertIn("P0", text)
        self.assertIn("88.0", text)
        self.assertIn("BNB", text)
        self.assertIn("&lt;suspends&gt;", text)  # HTML 转义
        self.assertIn("&amp;y=2", text)

    def test_p1_card_and_length(self):
        text = notify.format_alert(self._alert("P1"))
        self.assertIn("⚠️", text)
        self.assertLessEqual(len(text), notify.MAX_LEN)

    def test_event_key_ignores_score_and_sources(self):
        a1, a2 = self._alert(), self._alert()
        a2["score"] = 99.9
        a2["sources"] = ["other_source"]
        a2["count"] = 9
        self.assertEqual(notify.event_key(a1), notify.event_key(a2), "评分/来源变化不应改变去重键")

    def test_event_key_stable_and_unique(self):
        a1, a2 = self._alert(), self._alert()
        self.assertEqual(notify.event_key(a1), notify.event_key(a2))
        a2["titles"] = ["something else"]
        self.assertNotEqual(notify.event_key(a1), notify.event_key(a2))


class TestQuietHours(unittest.TestCase):
    def test_quiet_window(self):
        self.assertTrue(notify.in_quiet_hours(datetime.datetime(2026, 11, 8, 23, 30)))
        self.assertTrue(notify.in_quiet_hours(datetime.datetime(2026, 11, 8, 3, 0)))
        self.assertFalse(notify.in_quiet_hours(datetime.datetime(2026, 11, 8, 12, 0)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
