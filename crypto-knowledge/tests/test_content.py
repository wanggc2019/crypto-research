# -*- coding: utf-8 -*-
"""content.py 的内容加载与 v2 校验测试（每条 10–15 分钟）。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import content  # noqa: E402

FIXTURE_DIR = os.path.join(HERE, "fixtures", "curriculum")
PROD_DIR = os.path.join(HERE, "..", "config", "curriculum")

BODY = ("这是测试用的正文段落，长度需要满足五十个字符的下限要求；它还需要贡献足够的字数，"
        "让整条内容的正文总量稳定落在八百到一千八百字之间，因此每个小节都要写得足够充实，"
        "不能只放一句占位符，也不能靠重复同一句话来凑数。补充一句：测试数据也要保持语义通顺，"
        "这样在审查校验失败信息时，不会被无关的占位符干扰判断。")
HEADS = ["一句话", "为什么重要", "核心机制", "例子", "常见误解", "边界与代价"]


def _sections(count=6):
    return [{"h": HEADS[i % len(HEADS)], "p": BODY} for i in range(count)]


def _base_item(**overrides):
    item = {
        "id": "c901", "type": "concept", "stage": 1, "order": 1,
        "title": "测试条目", "minutes": 11,
        "sections": _sections(6),
        "quiz": [
            {"q": "这是测试问题一吗？", "a": "这是测试答案一，长度需要满足二十个字符的下限要求，用于验证校验器。"},
            {"q": "这是测试问题二吗？", "a": "这是测试答案二，长度同样需要满足二十个字符的下限要求，保证整条内容达标。"},
        ],
    }
    item.update(overrides)
    return item


class ContentTest(unittest.TestCase):
    def test_load_fixtures(self):
        items = content.load_all(FIXTURE_DIR)
        self.assertEqual([it["id"] for it in items], ["c101", "c102", "t101", "p101"])
        self.assertEqual(items[0]["_stage_title"], "测试阶段")
        self.assertEqual(content.validate(items), [])

    def test_split_tracks(self):
        concepts, lessons = content.split_tracks(content.load_all(FIXTURE_DIR))
        self.assertEqual([it["id"] for it in concepts], ["c101", "c102"])
        self.assertEqual([it["id"] for it in lessons], ["t101", "p101"])

    def test_inventory(self):
        inv = content.inventory(content.load_all(FIXTURE_DIR))
        self.assertEqual(inv[1], {"concept": 2, "theory": 1, "practice": 1, "total": 4})

    def test_quiz_items_supports_old_and_new_shape(self):
        self.assertEqual(len(content.quiz_items({"quiz": {"q": "q", "a": "a"}})), 1)
        self.assertEqual(len(content.quiz_items({"quiz": [{"q": "q", "a": "a"}] * 3})), 3)
        self.assertEqual(content.quiz_items({}), [])

    def test_duplicate_id(self):
        items = [_base_item(), _base_item()]
        problems = content.validate(items)
        self.assertTrue(any("重复" in p for p in problems), problems)

    def test_missing_quiz(self):
        problems = content.validate([_base_item(quiz=None)])
        self.assertTrue(any("缺少字段" in p for p in problems), problems)

    def test_too_few_quizzes_for_concept(self):
        problems = content.validate([_base_item(quiz=[{"q": "只有一个问题吗？", "a": "只有一个答案，长度需要满足二十个字符的下限要求。"}])])
        self.assertTrue(any("至少需要 2 道" in p for p in problems), problems)

    def test_bad_type(self):
        problems = content.validate([_base_item(type="video")])
        self.assertTrue(any("type 非法" in p for p in problems), problems)

    def test_minutes_out_of_range(self):
        problems = content.validate([_base_item(minutes=5)])
        self.assertTrue(any("minutes" in p for p in problems), problems)

    def test_section_too_short(self):
        sections = [{"h": "一句话", "p": "太短"}] * 4
        problems = content.validate([_base_item(sections=sections)])
        self.assertTrue(any("sections" in p for p in problems), problems)

    def test_body_too_short(self):
        item = _base_item(sections=_sections(4), quiz=[
            {"q": "短问题一吗？", "a": "短答案一，长度需要满足二十个字符的下限要求。"},
            {"q": "短问题二吗？", "a": "短答案二，长度需要满足二十个字符的下限要求。"},
        ])
        # 4×120 + 约 120 = 600 左右，低于 800
        problems = content.validate([item])
        self.assertTrue(any("正文合计" in p for p in problems), problems)

    def test_bad_source_url(self):
        problems = content.validate([_base_item(sources=["not-a-url"])])
        self.assertTrue(any("sources" in p for p in problems), problems)

    def test_validate_or_raise(self):
        with self.assertRaises(ValueError):
            content.validate_or_raise([_base_item(id="bad")])


@unittest.skipUnless(
    os.path.isdir(PROD_DIR) and any(n.startswith("stage") and n.endswith((".yaml", ".yml"))
                                    for n in os.listdir(PROD_DIR)),
    "生产课程内容尚未就绪",
)
class ProductionContentTest(unittest.TestCase):
    def test_production_content_valid_and_counted(self):
        items = content.load_all(PROD_DIR)
        content.validate_or_raise(items)
        inv = content.inventory(items)
        self.assertEqual(sorted(inv), [1, 2, 3, 4])
        self.assertEqual(sum(b["concept"] for b in inv.values()), 80)
        self.assertEqual(sum(b["theory"] for b in inv.values()), 45)
        self.assertEqual(sum(b["practice"] for b in inv.values()), 35)
        self.assertEqual(len(items), 160)

    def test_ids_unique_across_stages(self):
        items = content.load_all(PROD_DIR)
        ids = [it["id"] for it in items]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_item_is_10_to_15_minutes(self):
        for item in content.load_all(PROD_DIR):
            self.assertTrue(10 <= int(item["minutes"]) <= 15, item["id"])
            self.assertGreaterEqual(len(content.quiz_items(item)), 1, item["id"])


if __name__ == "__main__":
    unittest.main()
