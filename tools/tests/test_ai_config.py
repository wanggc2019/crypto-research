# -*- coding: utf-8 -*-
"""config/ai.yaml 必须合法，且代码里用到的每个 AI 功能名都已登记（gate 自动跑）。"""
import os
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "libs" / "crypto_core" / "src"))

from crypto_core import llm  # noqa: E402

# llm.resolve("x") / llm.chat_for("x") / 模块里的 TASK = "x"（配合 llm.resolve(TASK) 使用）
TASK_RE = re.compile(r"""(?:\bresolve|\bchat_for)\(\s*["']([a-z0-9][a-z0-9.-]*)["']|^TASK\s*=\s*["']([a-z0-9][a-z0-9.-]*)["']""",
                     re.M)
# 脚本里的 tools/agent_run.py <agent 功能名> ...
AGENT_RE = re.compile(r"agent_run\.py\"?\s+([a-z0-9][a-z0-9.-]*)")
SKIP_DIRS = {".venv", ".git", "data", "__pycache__", "tests", "node_modules"}


def tasks_in_code():
    found = {}
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = Path(root) / name
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "llm" not in text:
                continue
            for m in TASK_RE.finditer(text):
                found.setdefault(m.group(1) or m.group(2), path.relative_to(REPO).as_posix())
    return found


def agents_in_scripts():
    found = {}
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".sh"):
                path = Path(root) / name
                for m in AGENT_RE.finditer(path.read_text(encoding="utf-8", errors="ignore")):
                    found.setdefault(m.group(1), path.relative_to(REPO).as_posix())
    return found


class AiConfigTest(unittest.TestCase):
    def test_config_valid_and_all_tasks_registered(self):
        cfg = llm.load_ai_config()
        self.assertTrue(cfg, "缺少 config/ai.yaml")
        used = tasks_in_code()
        self.assertGreaterEqual(len(used), 4, "扫描不到功能名，正则可能失效：%s" % used)
        problems = llm.validate_config(cfg, used, agents_in_scripts())
        self.assertEqual(problems, [], "\n".join(problems))

    def test_scanner_sees_known_callers(self):
        used = tasks_in_code()
        for task in ("x-kol.analyze", "news.translate", "policy.interpret", "ci.pr-review"):
            self.assertIn(task, used)


    def test_scanner_sees_agent_scripts(self):
        self.assertEqual(set(agents_in_scripts()),
                         {"news.inspect", "policy.interpret-push", "analyst.daily", "analyst.weekly", "analyst.period"})


if __name__ == "__main__":
    unittest.main()
