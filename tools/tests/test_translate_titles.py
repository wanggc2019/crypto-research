# -*- coding: utf-8 -*-
"""translate_titles.py：模型输出解析与错误回退（不联网）。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

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
        # 没有 key：直接返回错误（调用方回退规则翻译），不发请求
        with mock.patch.object(tt.llm, "key_for", return_value=""), \
                mock.patch.object(tt.llm, "complete", side_effect=AssertionError("不应联网")):
            out = tt.translate(["hello"])
        self.assertTrue(out["error"].startswith("missing "), out)

    def test_bad_ai_config_returns_error(self):
        with mock.patch.object(tt.llm, "resolve", side_effect=tt.llm.LLMError("tasks.news.translate：没有登记")):
            self.assertIn("没有登记", tt.translate(["hello"])["error"])


if __name__ == "__main__":
    unittest.main()
