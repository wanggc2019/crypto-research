# -*- coding: utf-8 -*-
"""render.py 的消息渲染测试（HTML 转义、自测多题、复盘课、截断）。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import content  # noqa: E402
import render  # noqa: E402

FIXTURE_DIR = os.path.join(HERE, "fixtures", "curriculum")
CTX = {
    "brand": "crypto-knowledge",
    "job_label": "com.crypto-research.crypto-knowledge",
    "day_no": 3,
    "stage": 1,
    "stage_title": "测试阶段",
}


def load():
    return {it["id"]: it for it in content.load_all(FIXTURE_DIR)}


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.by_id = load()

    def test_concept_header_and_time_plan(self):
        text = render.render_concept(self.by_id["c101"], CTX)
        self.assertIn("【crypto-knowledge】", text)
        self.assertIn("🏷 任务：com.crypto-research.crypto-knowledge", text)
        self.assertIn("第 3 天", text)
        self.assertIn("概念课 · 13:00", text)
        self.assertIn("测试概念一", text)
        self.assertIn("答案 23:00 复盘课揭晓", text)
        self.assertIn("⏱ 约 11 分钟", text)
        self.assertNotIn("tg-spoiler", text)

    def test_lesson_uses_type_emoji(self):
        theory = render.render_lesson(self.by_id["t101"], CTX)
        practice = render.render_lesson(self.by_id["p101"], CTX)
        self.assertIn("📖", theory)
        self.assertIn("🛠", practice)
        self.assertIn("约 14 分钟", practice)

    def test_quiz_block_lists_all_questions(self):
        text = render.render_concept(self.by_id["c101"], CTX)
        self.assertIn("自测", text)
        self.assertIn("1. 这是第一道测试问题吗？", text)
        self.assertIn("2. 测试内容需要多少道自测题？", text)

    def test_html_escaped(self):
        item = dict(self.by_id["c101"])
        item["title"] = "<script>alert(1)</script>"
        text = render.render_concept(item, CTX)
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)

    def test_review_is_a_lesson_not_just_recap(self):
        text = render.render_review(
            CTX,
            [self.by_id["c101"], self.by_id["t101"]],
            [self.by_id["c102"]],
            {"concept": self.by_id["c102"], "lesson": self.by_id["p101"]},
        )
        self.assertIn("睡前复盘课", text)
        self.assertIn("今日复盘", text)
        self.assertIn("间隔复习", text)
        self.assertIn("综合思考", text)
        self.assertIn("明日预告", text)
        self.assertIn("约 12 分钟", text)
        self.assertIn("<tg-spoiler>", text)
        self.assertIn("「测试概念一」和「测试理论课」", text)
        self.assertIn("用 3 句话", text)

    def test_review_without_today_still_works(self):
        text = render.render_review(CTX, [], [], {})
        self.assertIn("今天没有新内容", text)
        self.assertIn("综合思考", text)
        self.assertNotIn("间隔复习", text)

    def test_recap_summarizes_day(self):
        stats = {"day_no": 3, "learned": 9, "total": 160, "due": 2, "streak": 3}
        text = render.render_recap(
            CTX,
            [self.by_id["c101"], self.by_id["t101"]],
            stats,
            {"concept": self.by_id["c102"], "lesson": self.by_id["p101"]},
        )
        self.assertIn("今日回顾与总结", text)
        self.assertIn("今日课程", text)
        self.assertIn("关键要点", text)
        self.assertIn("今日进度", text)
        self.assertIn("已学 9/160 条", text)
        self.assertIn("连续学习 3 天", text)
        self.assertIn("一句话总结", text)
        self.assertIn("明日预告", text)
        self.assertIn("约 8 分钟", text)

    def test_recap_without_today_still_works(self):
        stats = {"day_no": 1, "learned": 0, "total": 160, "due": 0, "streak": 0}
        text = render.render_recap(CTX, [], stats, {})
        self.assertIn("今天的课程还没发出", text)
        self.assertIn("今日进度", text)

    def test_weekly_review_summarizes_week(self):
        rows = [
            {"date": "2026-11-09", "items": [self.by_id["c101"]]},
            {"date": "2026-11-10", "items": [self.by_id["t101"]]},
            {"date": "2026-11-11", "items": []},
            {"date": "2026-11-12", "items": []},
            {"date": "2026-11-13", "items": []},
            {"date": "2026-11-14", "items": []},
            {"date": "2026-11-15", "items": []},
        ]
        stats = {"streak": 3, "learned": 9, "total": 160, "due": 2, "day_no": 7}
        stages = {1: {"learned": 5, "total": 40}, 2: {"learned": 0, "total": 40}}
        text = render.render_weekly(
            CTX, rows, stats, 2, stages,
            {"concept": self.by_id["c102"], "lesson": self.by_id["p101"]},
        )
        self.assertIn("本周回顾与总结 · 周日 21:00", text)
        self.assertIn("本周课程", text)
        self.assertIn("周一", text)
        self.assertIn("本周关键要点", text)
        self.assertIn("本周数据", text)
        self.assertIn("已学 9/160 条", text)
        self.assertIn("阶段 1 5/40", text)
        self.assertIn("本周自测", text)
        self.assertIn("<tg-spoiler>", text)
        self.assertIn("下周预告", text)
        self.assertIn("约 10 分钟", text)

    def test_weekly_review_empty_week(self):
        rows = [{"date": "2026-11-1%d" % i, "items": []} for i in range(0, 7)]
        stats = {"streak": 0, "learned": 0, "total": 160, "due": 0, "day_no": 1}
        text = render.render_weekly(CTX, rows, stats, 0, {1: {"learned": 0, "total": 40}}, {})
        self.assertIn("本周还没有学习记录", text)
        self.assertIn("本周数据", text)

    def test_truncate(self):
        text = render.truncate("a" * 5000, 100)
        self.assertLessEqual(len(text), 102)
        self.assertTrue(text.endswith("…"))

    def test_truncate_does_not_cut_tag_in_half(self):
        text = "<b>" + "a" * 198 + "</b>"
        out = render.truncate(text, 100)
        self.assertLessEqual(out.count("<b>"), 1)
        self.assertNotIn("<b>a…", out)


if __name__ == "__main__":
    unittest.main()
