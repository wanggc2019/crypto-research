#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仓库级 launchd 健康检查（healthcheck --module all）的纯函数单测。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import healthcheck  # noqa: E402


def row(module, level, status="🔴 失败(rc=1)", **extra):
    base = {"module": module, "label": "com.crypto-research." + module,
            "level": level, "status": status, "last_exit_code": 1, "last_run_ago": "5 分钟前"}
    base.update(extra)
    return base


class TestJobsProblems(unittest.TestCase):
    def test_only_bad(self):
        rows = [row("a", "ok", "🟢 正常"), row("b", "error"), row("c", "warn", "🟠 超时(1 小时前)")]
        bad = healthcheck.jobs_problems(rows)
        self.assertEqual([r["module"] for r in bad], ["b", "c"])

    def test_none(self):
        self.assertEqual(healthcheck.jobs_problems([row("a", "ok")]), [])


class TestJobsNotifyText(unittest.TestCase):
    def test_contains_modules_and_escape(self):
        rows = [row("crypto-demo", "error"), row("crypto<evil>", "warn", "🟠 超时")]
        text = healthcheck.jobs_notify_text(rows)
        self.assertIn("launchd 任务异常 2 个", text)
        self.assertIn("crypto-demo", text)
        self.assertIn("&lt;evil&gt;", text)      # HTML 转义
        self.assertNotIn("<evil>", text)
        self.assertIn("launchctl kickstart", text)
        self.assertIn("launchd-jobs-watchdog", text)


if __name__ == "__main__":
    unittest.main()
