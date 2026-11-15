# -*- coding: utf-8 -*-
"""tools/gate.py：质量门只靠自动发现，不内置任何模块的回放命令。"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "tools"))

import gate  # noqa: E402


class TestGateDiscovery(unittest.TestCase):
    def test_every_module_with_e2e_hook_is_a_step(self):
        steps, _ = gate.steps_offline()
        names = {name for name, _, _ in steps}
        for module in gate.discover_modules():
            if os.path.isfile(os.path.join(REPO, module, "scripts", "e2e.py")):
                self.assertIn("%s e2e" % module, names)
            if os.path.isdir(os.path.join(REPO, module, "tests")):
                self.assertIn("%s 单测" % module, names)

    def test_no_hardcoded_module_commands(self):
        steps, _ = gate.steps_offline()
        for name, cmd, _ in steps:
            if name.startswith("crypto-") and name.endswith(" e2e"):
                self.assertTrue(cmd[-1].endswith(os.path.join("scripts", "e2e.py")), name)

    def test_all_runtime_modules_are_gated(self):
        # 每个有 launchd 模板的生产模块都必须进质量门（防止像 crypto-policy 一样被长期排除）
        gated = set(gate.discover_modules())
        for name in os.listdir(REPO):
            if name.startswith("crypto-") and os.path.isdir(os.path.join(REPO, name, "deploy")):
                self.assertIn(name, gated)


if __name__ == "__main__":
    unittest.main()
