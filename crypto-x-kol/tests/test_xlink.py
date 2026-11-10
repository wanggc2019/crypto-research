#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_xlink.py —— 从 X 主页 HTML 还原原帖链接（离线纯函数）。"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import xlink  # noqa: E402

HTML = """
<html><body>
<article><a href="/PhyrexNi/status/123456789">2h</a>
  <div>如果美伊达成协议，美国对伊朗继续打击的可能性很低 川普接受 CNN 采访时表示…</div></article>
<article><a href="/PhyrexNi/status/987654321">1h</a>
  <div>另一条关于 BTC 的推文，内容完全不同。</div></article>
<article><div>没有状态的卡片</div></article>
</body></html>
"""


class TestParse(unittest.TestCase):
    def test_parse_articles(self):
        arts = xlink.parse_articles(HTML)
        self.assertEqual(len(arts), 2)                       # 没有 /status/ 的块被丢弃
        self.assertEqual([a["tweet_id"] for a in arts], ["123456789", "987654321"])
        self.assertIn("如果美伊达成协议", arts[0]["text"])

    def test_match_text(self):
        arts = xlink.parse_articles(HTML)
        self.assertEqual(xlink.match_tweet("如果美伊达成协议，美国对伊朗继续打击的可能性很低", arts),
                         "123456789")
        self.assertEqual(xlink.match_tweet("另一条关于 BTC 的推文，内容完全不同。", arts),
                         "987654321")
        self.assertIsNone(xlink.match_tweet("完全无关的另一段话", arts))

    def test_match_empty(self):
        self.assertIsNone(xlink.match_tweet("", []))
        self.assertIsNone(xlink.match_tweet("x", []))


class TestAttach(unittest.TestCase):
    def test_attach_sets_tweet_url_once_per_handle(self):
        posts = [
            {"id": "a", "handle": "PhyrexNi", "text": "如果美伊达成协议，美国对伊朗继续打击的可能性很低"},
            {"id": "b", "handle": "PhyrexNi", "text": "另一条关于 BTC 的推文，内容完全不同。"},
        ]
        cfg = {"fetch": {"tweet_link": True}}
        with mock.patch.object(xlink, "fetch_html", return_value=HTML) as fetched:
            xlink.attach(posts, cfg, log=lambda *_: None)
        self.assertEqual(fetched.call_count, 1)               # 同 handle 只抓一次
        self.assertEqual(posts[0]["tweet_url"], "https://x.com/PhyrexNi/status/123456789")
        self.assertEqual(posts[1]["tweet_url"], "https://x.com/PhyrexNi/status/987654321")

    def test_attach_failure_is_silent(self):
        posts = [{"id": "a", "handle": "x", "text": "hello"}]
        with mock.patch.object(xlink, "fetch_html", side_effect=RuntimeError("timeout")):
            xlink.attach(posts, {"fetch": {"tweet_link": True}}, log=lambda *_: None)
        self.assertNotIn("tweet_url", posts[0])               # 回退主页链接，不抛错

    def test_attach_disabled(self):
        posts = [{"id": "a", "handle": "x", "text": "hello"}]
        with mock.patch.object(xlink, "fetch_html", return_value=HTML) as fetched:
            xlink.attach(posts, {"fetch": {"tweet_link": False}}, log=lambda *_: None)
        self.assertEqual(fetched.call_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
