#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_parse.py —— X 主页 Markdown 解析（离线 fixtures）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fetch  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def read_fixture(*parts):
    with open(os.path.join(FIXTURES, *parts), "r", encoding="utf-8") as f:
        return f.read()


class TestParseProfile(unittest.TestCase):
    def setUp(self):
        self.page = read_fixture("x.com", "saylor.md")
        self.posts = fetch.parse_page(self.page, "saylor")

    def test_counts_and_filters(self):
        # pinned / 纯图 / 转推都被丢弃，剩 2 条本人原创
        self.assertEqual(len(self.posts), 2)
        texts = [p["text"] for p in self.posts]
        self.assertTrue(any("We purchased 5,000 BTC" in t for t in texts))
        self.assertTrue(any("self-custody" in t for t in texts))
        self.assertFalse(any("apex asset" in t for t in texts))       # 置顶
        self.assertFalse(any("Reposted content" in t for t in texts))  # 转推

    def test_quote_split(self):
        post = [p for p in self.posts if "We purchased" in p["text"]][0]
        self.assertEqual(post["quote"], "Strategy has acquired 5,000 BTC.")
        self.assertTrue(post["has_media"])

    def test_markdown_stripped(self):
        for post in self.posts:
            self.assertNotIn("pbs.twimg.com", post["text"])
            self.assertNotIn("](", post["text"])
            self.assertNotIn("Image ", post["text"])

    def test_fingerprint_stable_across_image_urls(self):
        changed = self.page.replace("DEF456", "NEW999").replace(
            "https://blob:https://x.com/abc", "https://blob:https://x.com/zzz")
        posts2 = fetch.parse_page(changed, "saylor")
        self.assertEqual(
            sorted(p["fingerprint"] for p in self.posts),
            sorted(p["fingerprint"] for p in posts2),
        )

    def test_fingerprint_differs_between_accounts(self):
        renamed = self.page.replace("x.com/saylor)", "x.com/saylor2)")
        posts2 = fetch.parse_page(renamed, "saylor2")
        self.assertEqual(len(posts2), 2)
        self.assertNotEqual(self.posts[0]["fingerprint"], posts2[0]["fingerprint"])


class TestAgePrefix(unittest.TestCase):
    """主页渲染可能带 "Name @handle 1h " 前缀；相对时间变化不能改变指纹。"""

    def _page(self, age1="1h", age2="2h", tail1="23 24 149 20K", tail2="24 25 157 21K"):
        def post(age, text, tail="23 24 149 20K"):
            return ("*   [![Image 1: @WuBlockchain](https://pbs.twimg.com/profile_images/1/a_normal.jpg)]"
                    "(https://x.com/WuBlockchain) Wu Blockchain @WuBlockchain %s %s Show more %s\n"
                    % (age, text, tail))
        return ("Title: Wu Blockchain (@WuBlockchain) on X\n\n## Wu Blockchain’s posts\n\n"
                + post(age1, "Elliptic: North Korean hackers create meme tokens on NEAR.", tail1)
                + "\n" + post(age2, "Another post about ETF flows.", tail2))

    def test_prefix_stripped(self):
        posts = fetch.parse_page(self._page(), "WuBlockchain")
        self.assertEqual(len(posts), 2)
        self.assertTrue(posts[0]["text"].startswith("Elliptic:"))
        self.assertTrue(posts[1]["text"].startswith("Another post"))
        self.assertNotIn("Wu Blockchain @WuBlockchain", posts[0]["text"])
        self.assertNotIn("Show more", posts[0]["text"])
        self.assertNotIn("20K", posts[0]["text"])

    def test_fingerprint_stable_when_age_changes(self):
        a = fetch.parse_page(self._page("1h", "2h"), "WuBlockchain")
        b = fetch.parse_page(self._page("6h", "7h"), "WuBlockchain")
        self.assertEqual([p["fingerprint"] for p in a], [p["fingerprint"] for p in b])
        self.assertEqual([p["text_key"] for p in a], [p["text_key"] for p in b])
        self.assertEqual([p["head_key"] for p in a], [p["head_key"] for p in b])

    def test_engagement_counters_do_not_change_hash(self):
        a = fetch.parse_page(self._page(tail1="23 24 149 20K"), "WuBlockchain")
        b = fetch.parse_page(self._page(tail1="999 1000 5000 2M"), "WuBlockchain")
        self.assertEqual(a[0]["fingerprint"], b[0]["fingerprint"])
        self.assertEqual(a[0]["text_key"], b[0]["text_key"])

    def test_strip_ui_tail(self):
        self.assertEqual(fetch.strip_ui_tail("正文 Show more 1 2 3 4K"),
                         "正文")
        self.assertEqual(fetch.strip_ui_tail("正文 Show replies 1 2"),
                         "正文")
        self.assertEqual(fetch.strip_ui_tail("正文没有尾巴"), "正文没有尾巴")

    def test_strip_helper_directly(self):
        self.assertEqual(fetch.strip_profile_prefix("Wu Blockchain @WuBlockchain 3h Hello"),
                         "Hello")
        self.assertEqual(fetch.strip_profile_prefix("@WuBlockchain 2m Hello"), "Hello")
        self.assertEqual(fetch.strip_profile_prefix("正常正文 @someone 3h"), "正常正文 @someone 3h")


class TestParseErrors(unittest.TestCase):
    def test_login_wall(self):
        with self.assertRaises(fetch.ParseError) as ctx:
            fetch.parse_page(read_fixture("pages", "login_wall.md"), "heyibinance")
        self.assertIn("登录墙", str(ctx.exception))

    def test_missing_posts_section(self):
        with self.assertRaises(fetch.ParseError):
            fetch.parse_page("Title: X\n\nhello world", "saylor")

    def test_missing_account(self):
        page = "Title: X\n\nThis account doesn’t exist.\n"
        with self.assertRaises(fetch.ParseError) as ctx:
            fetch.parse_page(page, "nobody")
        self.assertIn("找不到 posts 区", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
