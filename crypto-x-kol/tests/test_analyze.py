#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_analyze.py —— 中文判定/请求构造/响应解析/缓存/降级（离线，不联网）。"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import analyze  # noqa: E402

CFG = {"enabled": True, "model": "deepseek-chat", "batch_size": 2, "max_tweet_chars": 50}


def post(pid, text, handle="tester"):
    return {"id": pid, "handle": handle, "text": text, "quote": ""}


class TestTextHelpers(unittest.TestCase):
    def test_is_chinese(self):
        self.assertTrue(analyze.is_chinese("比特币 ETF 通过，利好"))
        self.assertTrue(analyze.is_chinese("ETH 升级时间确定"))
        self.assertFalse(analyze.is_chinese("Bitcoin ETF approved"))
        self.assertFalse(analyze.is_chinese(""))

    def test_build_request_and_truncate(self):
        body = analyze.build_request([post("a", "x" * 120)], CFG)
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        user = body["messages"][1]["content"]
        self.assertIn('"id": "a"', user)
        self.assertIn("…", user)          # 超长被截断

    def test_parse_response_variants(self):
        rows = analyze.parse_response('{"items":[{"id":"a","lang":"en","zh":"你好","summary":"总结"}]}')
        self.assertEqual(rows["a"]["zh"], "你好")
        fenced = analyze.parse_response('```json\n{"items":[{"id":"b","zh":"译","summary":"总"}]}\n```')
        self.assertEqual(fenced["b"]["summary"], "总")
        with self.assertRaises(ValueError):
            analyze.parse_response("no json")
        with self.assertRaises(ValueError):
            analyze.parse_response('{"nope": 1}')


class TestAnalyzeFlow(unittest.TestCase):
    def test_batches_and_cache(self):
        calls = []

        def caller(batch, cfg, key):
            calls.append([p["id"] for p in batch])
            return {p["id"]: {"lang": "en", "zh": "译" + p["id"], "summary": "总结" + p["id"]}
                    for p in batch}

        posts = [post(str(i), "hello") for i in range(5)]
        cache = analyze.analyze(posts, CFG, env={"DEEPSEEK_API_KEY": "k"}, caller=caller, log=lambda *_: None)
        self.assertEqual(len(calls), 3)                # batch_size=2 → 2+2+1
        self.assertEqual(len(cache), 5)
        # 二次调用：全部命中缓存，不再请求
        cache2 = analyze.analyze(posts, CFG, env={"DEEPSEEK_API_KEY": "k"}, caller=caller,
                                 cache=cache, log=lambda *_: None)
        self.assertEqual(len(calls), 3)
        self.assertEqual(cache2["3"]["summary"], "总结3")

    def test_failed_batch_is_skipped(self):
        def boom(batch, cfg, key):
            raise RuntimeError("api down")

        posts = [post("a", "hello")]
        cache = analyze.analyze(posts, CFG, env={"DEEPSEEK_API_KEY": "k"}, caller=boom,
                                log=lambda *_: None)
        self.assertEqual(cache, {})

    def test_missing_key_degrades(self):
        posts = [post("a", "hello")]
        with mock.patch.object(analyze, "load_api_key", return_value=""):
            cache = analyze.analyze(posts, CFG, env={}, log=lambda *_: None)
        self.assertEqual(cache, {})

    def test_disabled_skips(self):
        posts = [post("a", "hello")]
        cache = analyze.analyze(posts, {"enabled": False}, caller=lambda *a: {}, env={})
        self.assertEqual(cache, {})

    def test_localize(self):
        cache = {"a": {"lang": "en", "zh": "中文译文", "summary": "一句话总结"}}
        text, summary, lang, translated = analyze.localize(post("a", "English text"), cache)
        self.assertEqual((text, summary, lang, translated), ("中文译文", "一句话总结", "en", True))
        text, summary, _, translated = analyze.localize(post("b", "中文原文"), cache)
        self.assertEqual(text, "中文原文")
        self.assertEqual(summary, "")
        self.assertFalse(translated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
