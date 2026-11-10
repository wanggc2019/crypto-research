# -*- coding: utf-8 -*-
"""translate_titles.py：模型输出解析与错误回退（不联网）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import translate_titles as tt  # noqa: E402


class TestParseTranslations(unittest.TestCase):
    def test_plain_array(self):
        out = tt.parse_translations('["甲","乙"]', 2)
        self.assertEqual(out, ["甲", "乙"])

    def test_code_fence(self):
        out = tt.parse_translations('```json\n["甲","乙"]\n```', 2)
        self.assertEqual(out, ["甲", "乙"])

    def test_length_mismatch_returns_none(self):
        self.assertIsNone(tt.parse_translations('["甲"]', 2))

    def test_invalid_json_returns_none(self):
        self.assertIsNone(tt.parse_translations("not json", 1))

    def test_missing_key_without_network(self):
        # find_api_key 只读文件/环境；这里只验证不会抛异常
        self.assertIsInstance(tt.find_api_key(), str)


if __name__ == "__main__":
    unittest.main()
