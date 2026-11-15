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
        data = {"choices": [{"message": {"content": "  LGTM  "}}]}
        self.assertEqual(ai_review_pr.parse_review_response(data), "LGTM")

    def test_empty_choices_raises(self):
        with self.assertRaises(RuntimeError):
            ai_review_pr.parse_review_response({"choices": []})

    def test_null_content_raises(self):
        with self.assertRaises(RuntimeError):
            ai_review_pr.parse_review_response(
                {"choices": [{"message": {"content": None}}]})


class TestCommentDedup(unittest.TestCase):
    def test_select_existing_marker(self):
        comments = [
            {"id": 1, "body": "normal comment"},
            {"id": 2, "body": "<!-- ai-review -->\nold"},
            {"id": 3, "body": "<!-- ai-review -->\nnew"},
        ]
        self.assertEqual(ai_review_pr.select_existing_comment(comments)["id"], 3)

    def test_comment_updates_existing(self):
        calls = []

        def fake_github(method, path, token, payload=None, timeout=30):
            calls.append((method, path))
            if method == "GET":
                return 200, [{"id": 99, "body": "<!-- ai-review --> old"}]
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
