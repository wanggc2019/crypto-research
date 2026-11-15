# -*- coding: utf-8 -*-
"""healthcheck --fix 不得从临时目录里的仓库副本自动安装 launchd 任务。

2026-11-15：在临时副本里跑脚手架模块的 collect.sh，--fix 把 crypto-zzz 装成了真实 launchd 任务，
指向随后被删除的临时目录（rc=127，进 make jobs 与 gate 报错）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "tools"))

import healthcheck  # noqa: E402


class TempCheckoutGuardTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(Path, "home", return_value=Path(self.home.name))]
        for p in self.patches:
            p.start()
        self.calls = []
        self.run_patch = mock.patch.object(healthcheck, "_run", side_effect=lambda cmd: self.calls.append(cmd))
        self.run_patch.start()

    def tearDown(self):
        self.run_patch.stop()
        for p in self.patches:
            p.stop()
        self.home.cleanup()

    def test_temp_checkout_detection(self):
        self.assertTrue(healthcheck.is_temp_checkout(tempfile.gettempdir() + "/x/repo"))
        self.assertTrue(healthcheck.is_temp_checkout("/private/tmp/r"))
        self.assertTrue(healthcheck.is_temp_checkout("/private/var/folders/ab/cd/T/tmp.1/r"))
        self.assertFalse(healthcheck.is_temp_checkout("/opt/crypto-research"))
        self.assertFalse(healthcheck.is_temp_checkout("/tmpfoo/repo"))

    def test_no_install_from_temp_checkout(self):
        fake_repo = Path(tempfile.gettempdir()) / "copy" / "repo"
        with mock.patch.object(healthcheck, "REPO", fake_repo):
            result = healthcheck.check_launchd("crypto-zzz", fix=True)
        self.assertEqual(self.calls, [], "临时副本不应调用 render_launchd --install")
        self.assertEqual(result["state"], healthcheck.WARN)
        self.assertIn("临时目录", result["detail"])


if __name__ == "__main__":
    unittest.main()
