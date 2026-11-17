#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""new_module 脚手架单测（离线，写临时目录）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import new_module  # noqa: E402


class TestScaffoldDocs(unittest.TestCase):
    def _fake_repo(self, tmp):
        repo = Path(tmp)
        tmpl = repo / "docs" / "_template"
        tmpl.mkdir(parents=True)
        for name in ("README.md", "requirement.md", "design.md"):
            (tmpl / name).write_text("# {{name}} {{desc}} " + name, encoding="utf-8")
        return repo

    def test_keeps_existing_doc_and_fills_missing(self):
        """先评审需求、后建模块：已有的 requirement.md 不能被模板覆盖。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fake_repo(tmp)
            docs = repo / "docs" / "crypto-demo"
            docs.mkdir(parents=True)
            (docs / "requirement.md").write_text("已评审的需求", encoding="utf-8")
            with mock.patch.object(new_module, "REPO", repo):
                new_module.scaffold("crypto-demo", "演示")
            self.assertEqual((docs / "requirement.md").read_text(encoding="utf-8"), "已评审的需求")
            self.assertIn("crypto-demo 演示", (docs / "design.md").read_text(encoding="utf-8"))
            self.assertTrue((docs / "README.md").exists())

    def test_fresh_module_gets_all_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._fake_repo(tmp)
            with mock.patch.object(new_module, "REPO", repo):
                new_module.scaffold("crypto-demo", "演示")
            names = sorted(p.name for p in (repo / "docs" / "crypto-demo").glob("*.md"))
            self.assertEqual(names, ["README.md", "design.md", "requirement.md"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
