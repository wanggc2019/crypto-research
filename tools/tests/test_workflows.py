# -*- coding: utf-8 -*-
"""CI workflow 约束：拿得到订阅 token 的 AI 审查 job 只装锁定版本的最小依赖，且与 requirements.txt 一致。"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _pins(text):
    return {m.group(1).lower(): m.group(2) for m in re.finditer(r"([A-Za-z0-9_.-]+)==([0-9][^\s#]*)", text)}


class AiReviewWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.wf = (REPO / ".github" / "workflows" / "ai-review.yml").read_text(encoding="utf-8")
        self.req = _pins((REPO / "requirements.txt").read_text(encoding="utf-8"))

    def test_no_bulk_requirements_install(self):
        self.assertNotIn("-r requirements.txt", self.wf, "审查 job 能拿到 token，不要整份安装 requirements.txt")

    def test_pip_pins_match_requirements(self):
        pip_lines = [line for line in self.wf.splitlines() if "pip install" in line and not line.strip().startswith("#")]
        self.assertTrue(pip_lines)
        for line in pip_lines:
            pins = _pins(line)
            self.assertTrue(pins, "pip install 必须写死版本：%s" % line.strip())
            for name, version in pins.items():
                self.assertEqual(self.req.get(name), version, "%s 版本应与 requirements.txt 一致" % name)

    def test_claude_code_pinned(self):
        self.assertRegex(self.wf, r"@anthropic-ai/claude-code@\d+\.\d+\.\d+\b", "Claude Code 必须写死版本")


if __name__ == "__main__":
    unittest.main()
