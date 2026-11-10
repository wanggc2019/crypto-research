# -*- coding: utf-8 -*-
"""端到端：用 fixtures 课程内容 + 临时 data 目录跑完整时段，不联网。"""
import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import main  # noqa: E402
import schedule  # noqa: E402

FIXTURE_DIR = os.path.join(HERE, "fixtures", "curriculum")
DAY = "2026-11-10"


def run_cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main.run(argv)
    return code, buf.getvalue()


class E2ETest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ck-test-")
        self.state_path = os.path.join(self.tmp, "state", "progress.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def args(self, *extra):
        return ["--fixtures", FIXTURE_DIR, "--data-dir", self.tmp, "--date", DAY] + list(extra)

    def load_state(self):
        with open(self.state_path, encoding="utf-8") as f:
            return json.load(f)

    def test_slot_mapping(self):
        self.assertEqual(main.slot_for_hour(12), "13")
        self.assertEqual(main.slot_for_hour(13), "13")
        self.assertEqual(main.slot_for_hour(14), "13")
        self.assertEqual(main.slot_for_hour(18), "19")
        self.assertEqual(main.slot_for_hour(19), "19")
        self.assertEqual(main.slot_for_hour(20), "21")
        self.assertEqual(main.slot_for_hour(21), "21")
        self.assertEqual(main.slot_for_hour(23), "23")
        self.assertIsNone(main.slot_for_hour(3))
        self.assertIsNone(main.slot_for_hour(15))
        self.assertIsNone(main.slot_for_hour(17))

    def test_resolve_slot_catch_up(self):
        state = schedule.default_state(DAY)
        afternoon = datetime.datetime(2026, 11, 10, 16, 10)
        # 错过 13:00 → 补发 13
        self.assertEqual(main.resolve_slot("auto", afternoon, state, DAY), "13")
        schedule.mark_sent(state, DAY, "13",
                           {"items": [], "mode": "new", "track": "concept"}, [1])
        # 13 已发、19 未到点 → 没有可补的
        self.assertIsNone(main.resolve_slot("auto", afternoon, state, DAY))
        # 20:30 时 19 未发 → 先补 19
        evening = datetime.datetime(2026, 11, 10, 20, 30)
        self.assertEqual(main.resolve_slot("auto", evening, state, DAY), "19")
        schedule.mark_sent(state, DAY, "19",
                           {"items": [], "mode": "new", "track": "lesson"}, [1])
        self.assertEqual(main.resolve_slot("auto", evening, state, DAY), "21")
        # 早上 08:00 还没到第一班
        early = datetime.datetime(2026, 11, 10, 8, 0)
        self.assertIsNone(main.resolve_slot("auto", early, schedule.default_state(DAY), DAY))
        # 显式时段不受影响
        self.assertEqual(main.resolve_slot("23", early, schedule.default_state(DAY), DAY), "23")

    def test_dry_run_prints_but_does_not_write_state(self):
        code, out = run_cli(self.args("--slot", "13", "--dry-run"))
        self.assertEqual(code, 0)
        self.assertIn("测试概念一", out)
        self.assertFalse(os.path.exists(self.state_path))

    def test_notify_writes_state_and_dedupes(self):
        with mock.patch.object(main, "send", return_value=(True, "")) as sender:
            code, out = run_cli(self.args("--slot", "13", "--notify"))
            self.assertEqual(code, 0, out)
            self.assertEqual(sender.call_count, 1)
        state = self.load_state()
        self.assertEqual(state["sent"][DAY]["13"], ["c101"])
        self.assertEqual(state["cursors"]["concept"], 1)

        with mock.patch.object(main, "send", return_value=(True, "")) as sender2:
            code, out = run_cli(self.args("--slot", "13", "--notify"))
            self.assertEqual(code, 0)
            self.assertEqual(sender2.call_count, 0)
            self.assertIn("已发送", out)

    def test_four_slots_in_one_day(self):
        with mock.patch.object(main, "send", return_value=(True, "")):
            for slot in ("13", "19", "21", "23"):
                code, out = run_cli(self.args("--slot", slot, "--notify"))
                self.assertEqual(code, 0, out)
        state = self.load_state()
        self.assertEqual(sorted(state["sent"][DAY].keys()), ["13", "19", "21", "23"])
        self.assertEqual(state["cursors"], {"concept": 1, "lesson": 1})
        report = os.path.join(self.tmp, "reports", DAY + ".md")
        with open(report, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text.count("## " + DAY), 4)
        self.assertIn("今日回顾与总结", text)
        self.assertIn("关键要点", text)
        self.assertIn("睡前复盘", text)
        self.assertIn("今日复盘", text)

    def test_sunday_2100_renders_weekly_review(self):
        with mock.patch.object(main, "send", return_value=(True, "")):
            for slot in ("13", "19"):
                code, out = run_cli(self.args("--slot", slot, "--notify"))
                self.assertEqual(code, 0, out)
            sunday = ["--fixtures", FIXTURE_DIR, "--data-dir", self.tmp,
                      "--date", "2026-11-15", "--slot", "21", "--notify"]
            code, out = run_cli(sunday)
            self.assertEqual(code, 0, out)
        report = os.path.join(self.tmp, "reports", "2026-11-15.md")
        with open(report, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("本周回顾与总结", text)
        self.assertIn("本周课程", text)
        self.assertIn("本周数据", text)
        self.assertIn("下周预告", text)

    def test_weekly_flag_forces_weekly_on_weekday(self):
        with mock.patch.object(main, "send", return_value=(True, "")):
            code, out = run_cli(self.args("--slot", "21", "--notify", "--weekly"))
            self.assertEqual(code, 0, out)
        report = os.path.join(self.tmp, "reports", DAY + ".md")
        with open(report, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("本周回顾与总结", text)

    def test_weekday_2100_stays_daily_recap(self):
        with mock.patch.object(main, "send", return_value=(True, "")):
            code, out = run_cli(self.args("--slot", "21", "--notify"))
            self.assertEqual(code, 0, out)
        report = os.path.join(self.tmp, "reports", DAY + ".md")
        with open(report, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("今日回顾与总结", text)
        self.assertNotIn("本周回顾与总结", text)

    def test_second_day_advances_content_and_reviews(self):
        with mock.patch.object(main, "send", return_value=(True, "")):
            run_cli(self.args("--slot", "13", "--notify"))
            run_cli(self.args("--slot", "19", "--notify"))
            run_cli(self.args("--slot", "23", "--notify"))
            next_day = ["--fixtures", FIXTURE_DIR, "--data-dir", self.tmp, "--date", "2026-11-11",
                        "--slot", "13", "--notify"]
            code, out = run_cli(next_day)
            self.assertEqual(code, 0, out)
        state = self.load_state()
        self.assertEqual(state["sent"]["2026-11-11"]["13"], ["c102"])

    def test_status_json(self):
        code, out = run_cli(self.args("--status", "--json"))
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["progress"]["total"], 4)
        self.assertEqual(data["progress"]["concept_total"], 2)
        self.assertEqual(data["inventory"]["1"]["total"], 4)

    def test_bad_date_rejected(self):
        code, _ = run_cli(["--fixtures", FIXTURE_DIR, "--data-dir", self.tmp, "--date", "11/10/2026"])
        self.assertEqual(code, 2)

    def test_force_resends_same_item_without_advancing(self):
        with mock.patch.object(main, "send", return_value=(True, "")):
            run_cli(self.args("--slot", "13", "--notify"))
        with mock.patch.object(main, "send", return_value=(True, "")) as sender:
            code, out = run_cli(self.args("--slot", "13", "--notify", "--force"))
            self.assertEqual(code, 0)
            self.assertEqual(sender.call_count, 1)
            self.assertIn("已发送", out)
        state = self.load_state()
        self.assertEqual(state["items"]["c101"]["reps"], 1)
        self.assertEqual(state["cursors"]["concept"], 1)


if __name__ == "__main__":
    unittest.main()
