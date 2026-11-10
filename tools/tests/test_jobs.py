#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jobs.py 纯函数单测（不依赖 launchd、不读真实 LaunchAgents）。"""
import os
import plistlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jobs  # noqa: E402


class TestSchedule(unittest.TestCase):
    def test_interval(self):
        self.assertEqual(jobs.describe_schedule({"StartInterval": 60})[0], "每 1 分钟")
        self.assertEqual(jobs.describe_schedule({"StartInterval": 600})[0], "每 10 分钟")
        self.assertEqual(jobs.describe_schedule({"StartInterval": 300})[0], "每 5 分钟")
        self.assertEqual(jobs.describe_schedule({"StartInterval": 3600})[0], "每 1 小时")
        self.assertEqual(jobs.describe_schedule({"StartInterval": 45})[0], "每 45 秒")
        self.assertEqual(jobs.describe_schedule({"StartInterval": 600})[1], 600)

    def test_calendar_daily(self):
        text, period = jobs.describe_schedule(
            {"StartCalendarInterval": {"Hour": 10, "Minute": 0}})
        self.assertEqual(text, "每日 10:00")
        self.assertEqual(period, 86400)

    def test_calendar_weekly(self):
        text, period = jobs.describe_schedule(
            {"StartCalendarInterval": {"Weekday": 0, "Hour": 20, "Minute": 0}})
        self.assertEqual(text, "每周日 20:00")
        self.assertEqual(period, 7 * 86400)

    def test_manual(self):
        self.assertEqual(jobs.describe_schedule({})[0], "手动/事件触发")


class TestPrintParse(unittest.TestCase):
    SAMPLE = """gui/501/com.crypto-research.crypto-price = {
\tstate = not running
\tprogram = /bin/bash
\tstdout path = /tmp/out.log
\tstderr path = /tmp/err.log
\truns = 277
\tlast exit code = 0
}
"""

    def test_parse(self):
        out = jobs.parse_print_output(self.SAMPLE)
        self.assertEqual(out["state"], "not running")
        self.assertEqual(out["runs"], 277)
        self.assertEqual(out["last_exit_code"], 0)
        self.assertEqual(out["stdout_path"], "/tmp/out.log")

    def test_parse_empty(self):
        self.assertEqual(jobs.parse_print_output(""), {})


class TestModuleAndAge(unittest.TestCase):
    def test_module_from_working_dir(self):
        self.assertEqual(
            jobs.module_of(["/bin/bash", "/repo/crypto-x-kol/scripts/collect.sh"],
                           "", "/repo/crypto-x-kol"), "crypto-x-kol")

    def test_module_excludes_repo_name(self):
        self.assertEqual(
            jobs.module_of(["/bin/bash", "/repo/crypto-news/scripts/collect.sh"], ""),
            "crypto-news")

    def test_format_age(self):
        self.assertEqual(jobs.format_age(30), "30 秒前")
        self.assertEqual(jobs.format_age(120), "2 分钟前")
        self.assertEqual(jobs.format_age(7200), "2.0 小时前")
        self.assertEqual(jobs.format_age(172800), "2 天前")
        self.assertEqual(jobs.format_age(None), "未知")


class TestHealth(unittest.TestCase):
    NOW = 1_800_000_000

    def test_not_loaded(self):
        self.assertEqual(jobs.classify_health({}, None, 60, self.NOW)[1], "warn")

    def test_running(self):
        status, level = jobs.classify_health({"pid": 1, "last_exit_code": 0}, None, 60, self.NOW)
        self.assertEqual(level, "ok")

    def test_partial_failure_is_warn(self):
        # rc=1 = 部分源失败但有产出（降级），不应算红色失败
        status, level = jobs.classify_health({"last_exit_code": 1}, self.NOW - 10, 60, self.NOW)
        self.assertEqual(level, "warn")
        self.assertIn("rc=1", status)

    def test_network_failure_is_error(self):
        status, level = jobs.classify_health({"last_exit_code": 3}, self.NOW - 10, 60, self.NOW)
        self.assertEqual(level, "error")
        self.assertIn("rc=3", status)

    def test_lock_skip_is_ok(self):
        status, level = jobs.classify_health({"last_exit_code": 4}, self.NOW - 10, 60, self.NOW)
        self.assertEqual(level, "ok")

    def test_overdue(self):
        status, level = jobs.classify_health({"last_exit_code": 0}, self.NOW - 3600, 60, self.NOW)
        self.assertEqual(level, "warn")

    def test_ok(self):
        status, level = jobs.classify_health({"last_exit_code": 0}, self.NOW - 30, 60, self.NOW)
        self.assertEqual(level, "ok")


class TestDiscover(unittest.TestCase):
    def test_discover_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, interval in (("crypto-news", 600), ("crypto-price", 60)):
                label = f"com.crypto-research.{name}"
                plist = {
                    "Label": label,
                    "ProgramArguments": ["/bin/bash", f"/repo/{name}/scripts/collect.sh"],
                    "WorkingDirectory": f"/repo/{name}",
                    "StartInterval": interval,
                    "StandardOutPath": f"/repo/{name}/data/logs/launchd.out.log",
                }
                with open(os.path.join(tmp, label + ".plist"), "wb") as f:
                    plistlib.dump(plist, f)
            rows = jobs.discover(tmp)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["module"], "crypto-news")
            self.assertEqual(rows[0]["frequency"], "每 10 分钟")
            self.assertEqual(rows[1]["period_seconds"], 60)

    def test_discover_missing_dir(self):
        self.assertEqual(jobs.discover("/nonexistent-dir-xyz"), [])


class TestBadRows(unittest.TestCase):
    def test_default_includes_warn_and_error(self):
        rows = [{"level": "ok"}, {"level": "warn"}, {"level": "error"}, {"level": "unknown"}]
        self.assertEqual(len(jobs.bad_rows(rows)), 2)

    def test_errors_only(self):
        rows = [{"level": "ok"}, {"level": "warn"}, {"level": "error"}]
        self.assertEqual([r["level"] for r in jobs.bad_rows(rows, errors_only=True)], ["error"])


if __name__ == "__main__":
    unittest.main()
