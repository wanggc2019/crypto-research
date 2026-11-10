#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crypto-onchain 专用 Bot 凭据解析（独立 Bot（.env 配置））单测。"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import common  # noqa: E402


def bot_env(raw):
    with mock.patch.object(common, "load_env", return_value=raw):
        return common.load_bot_env()


class TestBotEnv(unittest.TestCase):
    def test_prefers_onchain_keys(self):
        env = bot_env({"TELEGRAM_ONCHAIN_BOT_TOKEN": "onchain-token",
                       "TELEGRAM_ONCHAIN_CHAT_ID": "111",
                       "TELEGRAM_BOT_TOKEN": "generic-token",
                       "TELEGRAM_CHAT_ID": "222"})
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "onchain-token")
        self.assertEqual(env["TELEGRAM_CHAT_ID"], "111")

    def test_alias_keys(self):
        env = bot_env({"TELEGRAM_CHAIN_BOT_TOKEN": "chain-token",
                       "TELEGRAM_CHAIN_CHAT_ID": "333"})
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "chain-token")
        self.assertEqual(env["TELEGRAM_CHAT_ID"], "333")

    def test_fallback_to_generic(self):
        env = bot_env({"TELEGRAM_BOT_TOKEN": "generic", "TELEGRAM_CHAT_ID": "42"})
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "generic")
        self.assertEqual(env["TELEGRAM_CHAT_ID"], "42")

    def test_empty_onchain_value_falls_back(self):
        env = bot_env({"TELEGRAM_ONCHAIN_BOT_TOKEN": "", "TELEGRAM_BOT_TOKEN": "generic",
                       "TELEGRAM_CHAT_ID": "42"})
        self.assertEqual(env["TELEGRAM_BOT_TOKEN"], "generic")


if __name__ == "__main__":
    unittest.main()
