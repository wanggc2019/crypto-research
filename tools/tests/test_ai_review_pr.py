#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai_review_pr 纯函数/防御逻辑单测（不联网）。"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_review_pr  # noqa: E402


class TestTrimDiff(unittest.TestCase):
    def test_short_diff_unchanged(self):
        diff = "diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\n"
        self.assertEqual(ai_review_pr.trim_diff(diff, 1000), diff)

    def test_long_diff_cuts_on_line_and_marks(self):
        diff = "".join("line-%03d %s\n" % (i, "x" * 40) for i in range(100))
        out = ai_review_pr.trim_diff(diff, 500)
        self.assertIn("[diff truncated:", out)
        self.assertTrue(out.rstrip().splitlines()[-2].startswith("line-"))
        self.assertLess(len(out), len(diff) + 200)


class TestReviewResponse(unittest.TestCase):
    def test_valid_content(self):
        self.assertEqual(ai_review_pr.require_review_content("  LGTM  "), "LGTM")

    def test_empty_content_raises(self):
        with self.assertRaises(RuntimeError):
            ai_review_pr.require_review_content("   ")

    def test_null_content_raises(self):
        with self.assertRaises(RuntimeError):
            ai_review_pr.require_review_content(None)

    def test_llm_error_mapped_to_runtime_error(self):
        with mock.patch.object(ai_review_pr.llm, "key_for", return_value="k"), \
                mock.patch.object(ai_review_pr.llm, "complete",
                                  side_effect=ai_review_pr.llm.LLMError("HTTP 401: bad key")):
            with self.assertRaisesRegex(RuntimeError, "Claude HTTP 401"):
                ai_review_pr.review("diff")

    def test_review_returns_stripped_content(self):
        with mock.patch.object(ai_review_pr.llm, "key_for", return_value="k"), \
                mock.patch.object(ai_review_pr.llm, "complete", return_value=" ok "):
            self.assertEqual(ai_review_pr.review("diff"), "ok")

    def test_no_ai_available_skips(self):
        with mock.patch.object(ai_review_pr.llm, "key_for", return_value=""), \
                mock.patch.object(ai_review_pr.llm, "complete", side_effect=AssertionError("不应调用")):
            self.assertEqual(ai_review_pr.review("diff"), "")


class TestCommentDedup(unittest.TestCase):
    def test_select_existing_marker(self):
        comments = [
            {"id": 1, "body": "normal comment", "user": {"type": "User"}},
            {"id": 2, "body": "<!-- ai-review -->\nold", "user": {"type": "Bot"},
             "created_at": "2026-01-01T00:00:00Z"},
            {"id": 3, "body": "<!-- ai-review -->\nnew", "user": {"type": "Bot"},
             "created_at": "2026-01-02T00:00:00Z"},
        ]
        self.assertEqual(ai_review_pr.select_existing_comment(comments)["id"], 3)

    def test_select_ignores_non_bot_marker(self):
        comments = [
            {"id": 1, "body": "<!-- ai-review -->\nuser comment",
             "user": {"type": "User"}},
        ]
        self.assertIsNone(ai_review_pr.select_existing_comment(comments))

    def test_trim_comment_preserves_marker_and_note(self):
        body = "<!-- ai-review -->\n" + ("x" * 1000)
        out = ai_review_pr.trim_comment(body, limit=200)
        self.assertTrue(out.startswith("<!-- ai-review -->"))
        self.assertIn("[comment truncated:", out)

    def test_comment_updates_existing(self):
        calls = []

        def fake_github(method, path, token, payload=None, timeout=30):
            calls.append((method, path))
            if method == "GET":
                return 200, [{"id": 99, "body": "<!-- ai-review --> old",
                              "user": {"type": "Bot"}}]
            return 200, {}

        with mock.patch.object(ai_review_pr, "_github", side_effect=fake_github):
            action = ai_review_pr.comment("new", "token", "o/r", "1")
        self.assertEqual(action, "updated")
        self.assertEqual(calls[1][0], "PATCH")

    def test_comment_creates_when_missing(self):
        calls = []

        def fake_github(method, path, token, payload=None, timeout=30):
            calls.append((method, path))
            return 200, [] if method == "GET" else {}

        with mock.patch.object(ai_review_pr, "_github", side_effect=fake_github):
            action = ai_review_pr.comment("new", "token", "o/r", "1")
        self.assertEqual(action, "created")
        self.assertEqual(calls[1][0], "POST")


class TestRequiredEnv(unittest.TestCase):
    def test_missing_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                ai_review_pr.required_env("REPO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
