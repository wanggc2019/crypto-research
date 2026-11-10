#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_fetch.py —— 基线/增量抓取与状态机（离线 fixtures，不联网）。"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fetch  # noqa: E402

DATE = "2026-11-08"
NEW_POST = (
    "\n*   [![Image 99: @saylor](https://pbs.twimg.com/profile_images/1/new_normal.jpg)]"
    "(https://x.com/saylor) New all-time high inflows into spot ETFs.\n"
)


def run_fetch(fixtures, data_dir, **kwargs):
    argv = ["--fixtures", fixtures, "--data-dir", data_dir, "--date", DATE,
            "--only", "saylor,willywoo"]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = fetch.main(argv)
    return rc, out.getvalue()


class TestFetchPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="crypto-x-kol-test-")
        self.fixtures = os.path.join(self.tmp, "fixtures")
        shutil.copytree(os.path.join(ROOT, "tests", "fixtures"), self.fixtures)
        self.data = os.path.join(self.tmp, "data")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_baseline_then_increment(self):
        rc, out = run_fetch(self.fixtures, self.data)
        self.assertEqual(rc, 0)
        self.assertIn("新增 0 条", out)
        self.assertIn("基线", out)

        state = os.path.join(self.data, "state", "kol-saylor.json")
        self.assertTrue(os.path.isfile(state))

        with open(os.path.join(self.fixtures, "x.com", "saylor.md"), "a", encoding="utf-8") as f:
            f.write(NEW_POST)
        rc, out = run_fetch(self.fixtures, self.data)
        self.assertEqual(rc, 0)
        self.assertIn("新增 1 条", out)

        raw = os.path.join(self.data, "raw", DATE, "posts.jsonl")
        with open(raw, "r", encoding="utf-8") as f:
            lines = [line for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("all-time high inflows", lines[0])

    def test_same_page_twice_is_deduped(self):
        run_fetch(self.fixtures, self.data)
        rc, out = run_fetch(self.fixtures, self.data)
        self.assertEqual(rc, 0)
        self.assertIn("新增 0 条", out)

    def test_partial_failure_rc_and_error_state(self):
        os.remove(os.path.join(self.fixtures, "x.com", "willywoo.md"))
        rc, out = run_fetch(self.fixtures, self.data)
        self.assertEqual(rc, 1)  # 部分失败但有产出（基线成功）
        self.assertIn("@willywoo", out)
        self.assertIn("失败", out)
        state = os.path.join(self.data, "state", "kol-willywoo.json")
        self.assertTrue(os.path.isfile(state))

    def _write_login_wall(self, handle):
        src = os.path.join(ROOT, "tests", "fixtures", "pages", "login_wall.md")
        with open(src, "r", encoding="utf-8") as f:
            body = f.read()
        with open(os.path.join(self.fixtures, "x.com", handle + ".md"), "w", encoding="utf-8") as f:
            f.write(body)

    def test_login_wall_is_skip_not_error(self):
        self._write_login_wall("saylor")
        rc, out = run_fetch(self.fixtures, self.data)
        self.assertEqual(rc, 0)                    # 登录墙不再算失败
        self.assertIn("跳过 1", out)
        self.assertIn("SKIP", out)
        state = os.path.join(self.data, "state", "kol-saylor.json")
        import json
        with open(state, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("登录墙", data.get("last_skipped", ""))
        self.assertFalse(data.get("initialized"))

    def test_all_login_wall_rc0_no_alert(self):
        """设计约束：网页限制抓不到就等下一轮，不告警。"""
        self._write_login_wall("saylor")
        self._write_login_wall("willywoo")
        rc, out = run_fetch(self.fixtures, self.data)
        self.assertEqual(rc, 0)
        self.assertIn("跳过 2", out)
        self.assertIn("等下一轮", out)

    def test_all_web_blocked_rc0_no_alert(self):
        err = urllib.error.HTTPError("https://r.jina.ai/https://x.com/saylor", 403,
                                     "Forbidden", {}, None)
        with mock.patch.object(fetch, "load_page", side_effect=err):
            rc, out = run_fetch(self.fixtures, self.data)
        self.assertEqual(rc, 0)
        self.assertIn("跳过 2", out)
        self.assertIn("等下一轮", out)

    def test_all_failed_rc3(self):
        shutil.rmtree(os.path.join(self.fixtures, "x.com"))
        rc, _ = run_fetch(self.fixtures, self.data)
        self.assertEqual(rc, 3)


class TestWebRestriction(unittest.TestCase):
    def test_markers(self):
        cases = [
            "ParseError: 命中 X 登录墙（匿名访问被拦截）",
            "HTTPError: HTTP Error 403: Forbidden",
            "HTTPError: HTTP Error 429: Too Many Requests",
            '{"name":"AbuseAlleviationError","status":40305}',
            "Anonymous access to domain x.com blocked until Tue Nov 10 2026",
        ]
        for err in cases:
            self.assertTrue(fetch.is_web_restriction(err), err)

    def test_structural_errors_are_not_web_restriction(self):
        self.assertFalse(fetch.is_web_restriction("ParseError: 找不到 posts 区（可能是登录墙或页面改版）"))
        self.assertFalse(fetch.is_web_restriction("TimeoutError: timed out"))


class TestSeenState(unittest.TestCase):
    def test_mark_seen_records_text_key(self):
        state = {}
        posts = [{"fingerprint": "fp1", "text_key": "tk1", "head_key": "hk1"}]
        fetch._mark_seen(state, posts)
        self.assertEqual(state["seen"]["fp1"] is not None, True)
        self.assertIn("tk1", state["seen_texts"])
        self.assertIn("hk1", state["seen_heads"])


class TestBuildItem(unittest.TestCase):
    def test_includes_role(self):
        item = fetch.build_item(
            {"handle": "saylor", "text": "t", "quote": "", "has_media": False, "fingerprint": "f"},
            {"handle": "saylor", "name": "Michael Saylor", "tier": "T1",
             "role": "Strategy 执行主席"},
            DATE)
        self.assertEqual(item["role"], "Strategy 执行主席")
        self.assertEqual(item["name"], "Michael Saylor")


class TestDiffPosts(unittest.TestCase):
    def test_first_run_baseline(self):
        fresh, baseline = fetch.diff_posts({}, [{"fingerprint": "a"}, {"fingerprint": "b"}])
        self.assertEqual(fresh, [])
        self.assertTrue(baseline)

    def test_text_key_prevents_duplicate_when_fingerprint_shifts(self):
        state = {"initialized": True, "seen": {"old-fp": "t"},
                 "seen_texts": {"stable-text": "t"}}
        fresh, _ = fetch.diff_posts(
            state, [{"fingerprint": "new-fp", "text_key": "stable-text"}])
        self.assertEqual(fresh, [])

    def test_head_key_prevents_duplicate_when_tail_changes(self):
        state = {"initialized": True, "seen": {}, "seen_texts": {},
                 "seen_heads": {"stable-head": "t"}}
        fresh, _ = fetch.diff_posts(
            state, [{"fingerprint": "new-fp", "text_key": "new-tk",
                     "head_key": "stable-head"}])
        self.assertEqual(fresh, [])

    def test_second_run_new_only(self):
        state = {"initialized": True, "seen": {"a": "t"}}
        fresh, baseline = fetch.diff_posts(state, [{"fingerprint": "a"}, {"fingerprint": "b"}])
        self.assertFalse(baseline)
        self.assertEqual([p["fingerprint"] for p in fresh], ["b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
