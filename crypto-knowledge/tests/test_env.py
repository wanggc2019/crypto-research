# -*- coding: utf-8 -*-
"""Bot 凭据解析测试：专用 Bot 优先、别名兼容、回退共享 Bot、--whoami 打码。"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import main  # noqa: E402

import common  # noqa: E402

DEDICATED_TOKEN = "111111111:" + "A" * 35
SHARED_TOKEN = "999999999:" + "B" * 35


class ResolveBotEnvTest(unittest.TestCase):
    def test_dedicated_preferred(self):
        info = common.resolve_bot_env({
            "TELEGRAM_KNOWLEDGE_BOT_TOKEN": DEDICATED_TOKEN,
            "TELEGRAM_KNOWLEDGE_CHAT_ID": "42",
            "TELEGRAM_BOT_TOKEN": SHARED_TOKEN,
            "TELEGRAM_CHAT_ID": "7",
        })
        self.assertEqual(info["TELEGRAM_BOT_TOKEN"], DEDICATED_TOKEN)
        self.assertEqual(info["TELEGRAM_CHAT_ID"], "42")
        self.assertTrue(info["dedicated"])
        self.assertEqual(info["token_key"], "TELEGRAM_KNOWLEDGE_BOT_TOKEN")

    def test_alias_names(self):
        info = common.resolve_bot_env({
            "TELEGRAM_LEARNING_BOT_TOKEN": DEDICATED_TOKEN,
            "TELEGRAM_LEARNING_CHAT_ID": "43",
        })
        self.assertEqual(info["TELEGRAM_BOT_TOKEN"], DEDICATED_TOKEN)
        self.assertEqual(info["TELEGRAM_CHAT_ID"], "43")
        self.assertTrue(info["dedicated"])

    def test_fallback_to_shared(self):
        info = common.resolve_bot_env({
            "TELEGRAM_BOT_TOKEN": SHARED_TOKEN,
            "TELEGRAM_CHAT_ID": "7",
        })
        self.assertEqual(info["TELEGRAM_BOT_TOKEN"], SHARED_TOKEN)
        self.assertEqual(info["TELEGRAM_CHAT_ID"], "7")
        self.assertFalse(info["dedicated"])

    def test_missing_everything(self):
        info = common.resolve_bot_env({})
        self.assertEqual(info["TELEGRAM_BOT_TOKEN"], "")
        self.assertEqual(info["TELEGRAM_CHAT_ID"], "")
        self.assertFalse(info["dedicated"])

    def test_partial_dedicated_falls_back_chat(self):
        info = common.resolve_bot_env({
            "TELEGRAM_KNOWLEDGE_BOT_TOKEN": DEDICATED_TOKEN,
            "TELEGRAM_CHAT_ID": "7",
        })
        self.assertEqual(info["TELEGRAM_CHAT_ID"], "7")
        self.assertFalse(info["dedicated"])

    def test_mask(self):
        self.assertEqual(main._mask("123456789:ABCDEFGHIJK"), "123456…HIJK")
        self.assertEqual(main._mask("short"), "sh***")


class SendPolicyTest(unittest.TestCase):
    def test_send_is_not_silent(self):
        with mock.patch.object(common.core_tg, "send_message", return_value=(True, "")) as sender:
            ok, err = main.send({"TELEGRAM_BOT_TOKEN": DEDICATED_TOKEN,
                                 "TELEGRAM_CHAT_ID": "42"}, "hi")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertFalse(sender.call_args.kwargs.get("silent"))


class WhoamiTest(unittest.TestCase):
    def run_whoami(self, env):
        buf = io.StringIO()
        with mock.patch.object(common, "load_env", return_value=env), \
                mock.patch.object(common.core_tg, "tg_call",
                                  return_value={"ok": True, "result": {
                                      "username": "YourLearningBot", "first_name": "Learning"}}):
            with contextlib.redirect_stdout(buf):
                code = main.run(["--whoami"])
        return code, buf.getvalue()

    def test_whoami_dedicated(self):
        code, out = self.run_whoami({
            "TELEGRAM_KNOWLEDGE_BOT_TOKEN": DEDICATED_TOKEN,
            "TELEGRAM_KNOWLEDGE_CHAT_ID": "42",
        })
        self.assertEqual(code, 0)
        self.assertIn("@YourLearningBot", out)
        self.assertIn("专用 Bot：是", out)
        self.assertNotIn(DEDICATED_TOKEN, out)

    def test_whoami_fallback_warns(self):
        code, out = self.run_whoami({
            "TELEGRAM_BOT_TOKEN": SHARED_TOKEN,
            "TELEGRAM_CHAT_ID": "7",
        })
        self.assertEqual(code, 0)
        self.assertIn("回退共享 Bot", out)


if __name__ == "__main__":
    unittest.main()
