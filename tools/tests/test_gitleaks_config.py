#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gitleaks 本地 hook / GitHub workflow 配置自检（离线）。"""
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


class TestGitleaksConfig(unittest.TestCase):
    def test_pre_commit_hook_is_real_and_executable(self):
        hook = REPO / ".githooks" / "pre-commit"
        text = hook.read_text(encoding="utf-8")
        self.assertGreater(len(text.strip()), 0, "pre-commit 不能是空文件")
        self.assertTrue(os.access(hook, os.X_OK), "pre-commit 必须可执行")
        self.assertIn("gitleaks git --pre-commit --staged", text)

    def test_workflow_is_real_and_pinned(self):
        # gitleaks 已并入 ci.yml 的 checks job（docs/adr/0012）
        workflow = REPO / ".github" / "workflows" / "ci.yml"
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("gitleaks/gitleaks-action@", text)
        self.assertRegex(text, r"gitleaks-action@[0-9a-f]{40}")
        self.assertIn("pull_request", text)
        self.assertIn("fetch-depth: 0", text)

    def test_make_hooks_enables_pre_commit(self):
        text = (REPO / "Makefile").read_text(encoding="utf-8")
        self.assertIn(".githooks/pre-commit", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
