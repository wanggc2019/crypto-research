#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram 凭据：TELEGRAM_FUTURES_* 优先，没填回退共享 Bot。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import common  # noqa: E402
from crypto_core.notify import check_credentials  # noqa: E402

SHARED = {"TELEGRAM_BOT_TOKEN": "shared", "TELEGRAM_CHAT_ID": "1"}


class TestBotEnv(unittest.TestCase):
    def load(self, raw):
        with mock.patch.object(common._env, "load_env", return_value=dict(raw)):
            return common.load_env()

    def test_dedicated_bot_preferred(self):
        env = self.load(dict(SHARED, TELEGRAM_FUTURES_BOT_TOKEN="futures", TELEGRAM_FUTURES_CHAT_ID="3"))
        self.assertEqual((env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]), ("futures", "3"))

    def test_falls_back_to_shared_bot(self):
        env = self.load(SHARED)
        self.assertEqual((env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]), ("shared", "1"))

    def test_chat_only_keeps_shared_token(self):
        env = self.load(dict(SHARED, TELEGRAM_FUTURES_CHAT_ID="3"))
        self.assertEqual((env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]), ("shared", "3"))

    def test_nothing_configured_gives_empty_strings(self):
        env = self.load({})
        self.assertEqual((env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]), ("", ""))
        _, _, problems = check_credentials(env)
        self.assertTrue(problems)                  # 没配凭据 → run.py 报配置错误（rc=2），不会静默

    def test_token_only_keeps_shared_chat(self):
        env = self.load(dict(SHARED, TELEGRAM_FUTURES_BOT_TOKEN="futures"))
        self.assertEqual((env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]), ("futures", "1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
