# -*- coding: utf-8 -*-
"""课程目录生成测试（docs/crypto-knowledge/course-index.md 必须与内容库同步）。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import build_index  # noqa: E402
import content  # noqa: E402

INDEX = os.path.join(HERE, "..", "..", "docs", "crypto-knowledge", "course-index.md")


class BuildIndexTest(unittest.TestCase):
    def test_build_contains_all_items(self):
        items = content.load_all()
        text = build_index.build(items)
        self.assertIn("# 课程目录", text)
        for item in items:
            self.assertIn("`%s`" % item["id"], text)
        # 每条只有一次（阶段内唯一）
        self.assertEqual(text.count("| `c101` |"), 1)

    @unittest.skipUnless(os.path.isfile(INDEX), "课程目录尚未生成")
    def test_index_is_up_to_date(self):
        items = content.load_all()
        expected = build_index.build(items)
        with open(INDEX, encoding="utf-8") as f:
            current = f.read()
        self.assertEqual(current, expected, "course-index.md 过期，请运行 scripts/build_index.py")


if __name__ == "__main__":
    unittest.main()
