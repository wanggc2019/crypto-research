# -*- coding: utf-8 -*-
"""schedule.py 的选卡、SRS 与状态容错测试。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import content  # noqa: E402
import schedule  # noqa: E402

FIXTURE_DIR = os.path.join(HERE, "fixtures", "curriculum")
DAY = "2026-11-10"


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        items = content.load_all(FIXTURE_DIR)
        self.concepts, self.lessons = schedule.split_tracks(items)
        self.items = items
        self.state = schedule.default_state(DAY)

    def mark(self, slot, item, mode="new", track=None):
        schedule.mark_sent(self.state, DAY, slot,
                           {"items": [item], "mode": mode, "track": track},
                           [1, 3, 7, 14, 30, 60])

    def test_default_state(self):
        self.assertEqual(self.state["schema_version"], 1)
        self.assertEqual(self.state["cursors"], {"concept": 0, "lesson": 0})
        self.assertEqual(self.state["created"], DAY)

    def test_normalize_corrupt_state_falls_back(self):
        self.assertEqual(schedule.normalize({"schema_version": 99}, DAY)["cursors"],
                         {"concept": 0, "lesson": 0})
        self.assertEqual(schedule.normalize(None, DAY)["sent"], {})
        broken = {"schema_version": 1, "cursors": {"concept": "x"}, "items": [], "sent": []}
        fixed = schedule.normalize(broken, DAY)
        self.assertEqual(fixed["cursors"]["concept"], 0)
        self.assertEqual(fixed["items"], {})
        self.assertEqual(fixed["sent"], {})

    def test_new_concept_advances_cursor_and_srs(self):
        pick = schedule.pick_concept(self.state, self.concepts, DAY)
        self.assertEqual(pick["mode"], "new")
        self.assertEqual(pick["item"]["id"], "c101")
        self.mark("13", pick["item"], "new", "concept")
        self.assertEqual(self.state["cursors"]["concept"], 1)
        rec = self.state["items"]["c101"]
        self.assertEqual(rec["reps"], 1)
        self.assertEqual(rec["next_due"], "2026-11-11")
        self.assertTrue(schedule.already_sent(self.state, DAY, "13"))
        self.assertFalse(schedule.already_sent(self.state, DAY, "19"))

    def test_due_after_one_day(self):
        pick = schedule.pick_concept(self.state, self.concepts, DAY)
        self.mark("13", pick["item"], "new", "concept")
        self.assertEqual(schedule.due_items(self.state, self.items, DAY), [])
        due = schedule.due_items(self.state, self.items, "2026-11-11")
        self.assertEqual([it["id"] for it in due], ["c101"])

    def test_review_excludes_today_items(self):
        c = schedule.pick_concept(self.state, self.concepts, DAY)["item"]
        lesson = schedule.pick_lesson(self.state, self.lessons, DAY)["item"]
        self.mark("13", c, "new", "concept")
        self.mark("19", lesson, "new", "lesson")
        today, due = schedule.pick_review(self.state, self.items, DAY, 2)
        self.assertEqual([it["id"] for it in today], ["c101", "t101"])
        self.assertEqual(due, [])

    def test_review_advances_srs_interval(self):
        c = schedule.pick_concept(self.state, self.concepts, DAY)["item"]
        self.mark("13", c, "new", "concept")
        _, due = schedule.pick_review(self.state, self.items, "2026-11-11", 2)
        self.assertEqual([it["id"] for it in due], ["c101"])
        schedule.mark_sent(self.state, "2026-11-11", "23",
                           {"items": due, "mode": "review", "track": None}, [1, 3, 7, 14, 30, 60])
        rec = self.state["items"]["c101"]
        self.assertEqual(rec["reps"], 2)
        self.assertEqual(rec["next_due"], "2026-11-14")  # +3 天

    def test_srs_interval_sequence(self):
        days = [1, 3, 7, 14, 30, 60]
        self.assertEqual([schedule.interval_days(n, days) for n in range(1, 8)],
                         [1, 3, 7, 14, 30, 60, 60])
        self.assertEqual(schedule.interval_days(0, days), 1)

    def test_exhaustion_falls_back_to_review(self):
        for _ in range(len(self.concepts)):
            pick = schedule.pick_concept(self.state, self.concepts, DAY)
            self.mark("13", pick["item"], "new", "concept")
        pick = schedule.pick_concept(self.state, self.concepts, "2026-12-31")
        self.assertEqual(pick["mode"], "review")
        self.assertIsNotNone(pick["item"])

    def test_preview_shows_next_cursor_items(self):
        preview = schedule.preview(self.state, self.concepts, self.lessons)
        self.assertEqual(preview["concept"]["id"], "c101")
        self.assertEqual(preview["lesson"]["id"], "t101")

    def test_day_number(self):
        self.assertEqual(schedule.day_number(self.state, DAY), 1)
        self.assertEqual(schedule.day_number(self.state, "2026-11-12"), 3)

    def test_already_sent_after_empty_plan(self):
        schedule.mark_sent(self.state, DAY, "21",
                           {"items": [], "mode": "recap", "track": None}, None)
        self.assertTrue(schedule.already_sent(self.state, DAY, "21"))
        self.assertFalse(schedule.already_sent(self.state, DAY, "23"))

    def test_streak_days(self):
        self.assertEqual(schedule.streak_days(self.state, DAY), 0)
        self.mark("13", self.concepts[0], "new", "concept")
        self.assertEqual(schedule.streak_days(self.state, DAY), 1)
        self.state["sent"]["2026-11-11"] = {"13": ["c102"]}
        self.assertEqual(schedule.streak_days(self.state, "2026-11-11"), 2)
        self.assertEqual(schedule.streak_days(self.state, "2026-11-12"), 0)

    def mark_on(self, day, slot, item, mode="new", track=None):
        schedule.mark_sent(self.state, day, slot,
                           {"items": [item], "mode": mode, "track": track}, [1, 3, 7, 14, 30, 60])

    def test_week_range_and_weekday(self):
        start, end = schedule.week_range("2026-11-11")
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-11-09", "2026-11-15"))
        self.assertTrue(schedule.is_weekday("2026-11-15", 7))
        self.assertFalse(schedule.is_weekday("2026-11-14", 7))

    def test_week_log_and_active_days(self):
        c1 = self.concepts[0]
        lesson = self.lessons[0]
        self.mark_on("2026-11-09", "13", c1, "new", "concept")
        self.mark_on("2026-11-10", "19", lesson, "new", "lesson")
        rows = schedule.week_log(self.state, self.items, "2026-11-11")
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["date"], "2026-11-09")
        self.assertEqual([it["id"] for it in rows[0]["items"]], ["c101"])
        self.assertEqual([it["id"] for it in rows[1]["items"]], ["t101"])
        self.assertEqual(schedule.week_active_days(self.state, "2026-11-11"), 2)

    def test_stage_progress(self):
        self.mark_on("2026-11-09", "13", self.concepts[0], "new", "concept")
        self.mark_on("2026-11-10", "13", self.concepts[1], "new", "concept")
        progress = schedule.stage_progress(self.state, self.items)
        self.assertEqual(progress[1], {"learned": 2, "total": 4})

    def test_recap_stats(self):
        self.mark("13", self.concepts[0], "new", "concept")
        stats = schedule.recap_stats(self.state, self.items, DAY)
        self.assertEqual(stats["day_no"], 1)
        self.assertEqual(stats["learned"], 1)
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["due"], 0)
        self.assertEqual(stats["streak"], 1)

    def test_progress(self):
        p = schedule.progress(self.state, self.items, DAY)
        self.assertEqual(p["total"], 4)
        self.assertEqual(p["concept_total"], 2)
        self.assertEqual(p["lesson_total"], 2)
        self.assertEqual(p["sent"], {"13": False, "19": False, "21": False, "23": False})


if __name__ == "__main__":
    unittest.main()
