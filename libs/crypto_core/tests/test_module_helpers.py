# -*- coding: utf-8 -*-
"""模块公共逻辑下沉（Bot 凭据 / data 布局 / 时区 / 日志行 / 配置缓存）。"""
import os
import tempfile
import time
import unittest
from unittest import mock

from crypto_core import config, env, paths, store, timeutil


class TestResolveBotEnv(unittest.TestCase):
    KEYS = (("TELEGRAM_X_BOT_TOKEN", "TELEGRAM_X_TOKEN"), ("TELEGRAM_X_CHAT_ID",))

    def test_dedicated_wins(self):
        info = env.resolve_bot_env({"TELEGRAM_X_TOKEN": "t2", "TELEGRAM_X_CHAT_ID": "9",
                                    "TELEGRAM_BOT_TOKEN": "shared", "TELEGRAM_CHAT_ID": "1"}, *self.KEYS)
        self.assertEqual((info["TELEGRAM_BOT_TOKEN"], info["TELEGRAM_CHAT_ID"]), ("t2", "9"))
        self.assertEqual((info["token_key"], info["chat_key"]), ("TELEGRAM_X_TOKEN", "TELEGRAM_X_CHAT_ID"))
        self.assertTrue(info["dedicated"])

    def test_alias_order(self):
        info = env.resolve_bot_env({"TELEGRAM_X_BOT_TOKEN": "a", "TELEGRAM_X_TOKEN": "b"}, *self.KEYS)
        self.assertEqual(info["TELEGRAM_BOT_TOKEN"], "a")

    def test_independent_fallback(self):
        info = env.resolve_bot_env({"TELEGRAM_X_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": 123}, *self.KEYS)
        self.assertEqual((info["TELEGRAM_BOT_TOKEN"], info["TELEGRAM_CHAT_ID"]), ("t", "123"))
        self.assertEqual(info["chat_key"], "TELEGRAM_CHAT_ID")
        self.assertFalse(info["dedicated"])

    def test_empty(self):
        info = env.resolve_bot_env({})
        self.assertEqual((info["TELEGRAM_BOT_TOKEN"], info["TELEGRAM_CHAT_ID"]), ("", ""))
        self.assertFalse(info["dedicated"])


class TestDataLayout(unittest.TestCase):
    def test_layout(self):
        d = paths.data_layout("/tmp/x/../y")
        self.assertEqual(d["root"], os.path.abspath("/tmp/y"))
        self.assertEqual(set(d), {"root", "raw", "state", "reports", "exports", "logs"})
        self.assertEqual(d["logs"], os.path.join(d["root"], "logs"))


class TestApplyTimezone(unittest.TestCase):
    def test_noop_and_set(self):
        self.assertIsNone(timeutil.apply_timezone(""))
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(time, "tzset") as tzset:
            self.assertEqual(timeutil.apply_timezone("UTC"), "UTC")
            self.assertEqual(os.environ["TZ"], "UTC")
            tzset.assert_called_once()


class TestAppendLogLine(unittest.TestCase):
    def test_creates_parent_and_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "logs", "x.log")
            line = store.append_log_line(path, "hello", ts="2026-01-01 00:00:00")
            store.append_log_line(path, "world")
            self.assertEqual(line, "2026-01-01 00:00:00 hello")
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            self.assertEqual(lines[0], line)
            self.assertRegex(lines[1], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} world$")

    def test_unwritable_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "file")
            open(blocker, "w").close()
            self.assertTrue(store.append_log_line(os.path.join(blocker, "x.log"), "m").endswith(" m"))


class TestConfigCache(unittest.TestCase):
    def test_cache_suffix_nondict_and_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "config"))
            p = os.path.join(tmp, "config", "app.json")
            with open(p, "w") as f:
                f.write('{"a": 1}')
            with open(os.path.join(tmp, "config", "list.json"), "w") as f:
                f.write("[1, 2]")
            cache = config.ConfigCache(tmp)
            self.assertEqual(cache.get("app"), {"a": 1})
            self.assertIs(cache.get("app.json"), cache.get("app.json"))
            self.assertEqual(cache.get("list.json"), {})
            self.assertEqual(cache.get("missing.yaml"), {})
            with open(p, "w") as f:
                f.write('{"a": 2}')
            self.assertEqual(cache.get("app")["a"], 1)
            cache.clear()
            self.assertEqual(cache.get("app")["a"], 2)


if __name__ == "__main__":
    unittest.main()
