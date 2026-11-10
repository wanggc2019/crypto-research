#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""job_label 的调度标签/手动回退逻辑（离线单测）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from crypto_core.notify import job_label  # noqa: E402
from crypto_core.notify.templates import format_alert  # noqa: E402


class TestJobLabel(unittest.TestCase):
    def setUp(self):
        self._old = {k: os.environ.get(k) for k in ("XPC_SERVICE_NAME", "CRYPTO_JOB_LABEL")}
        os.environ.pop("CRYPTO_JOB_LABEL", None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_plist_label_takes_priority(self):
        # launchd 会把 XPC_SERVICE_NAME 覆盖成 0，所以 plist 注入的 CRYPTO_JOB_LABEL 优先
        os.environ["XPC_SERVICE_NAME"] = "0"
        os.environ["CRYPTO_JOB_LABEL"] = "com.crypto-research.crypto-analyst.daily"
        self.assertEqual(job_label("crypto-analyst"),
                         "com.crypto-research.crypto-analyst.daily")

    def test_launchd_label(self):
        os.environ["XPC_SERVICE_NAME"] = "com.crypto-research.crypto-analyst.daily"
        self.assertEqual(job_label("crypto-analyst"),
                         "com.crypto-research.crypto-analyst.daily")

    def test_manual_fallback(self):
        os.environ["XPC_SERVICE_NAME"] = "0"
        self.assertEqual(job_label("crypto-analyst"), "crypto-analyst（手动运行）")

    def test_format_alert_uses_plist_label(self):
        os.environ["XPC_SERVICE_NAME"] = "0"
        os.environ["CRYPTO_JOB_LABEL"] = "com.crypto-research.crypto-news"
        text = format_alert({"module": "crypto-news", "level": "P1", "category": "macro",
                             "title": "t", "titles": ["t"], "sources": ["x"], "count": 1})
        self.assertIn("任务：com.crypto-research.crypto-news", text)
        self.assertNotIn("（手动运行）", text)

    def test_format_alert_manual_fallback(self):
        os.environ["XPC_SERVICE_NAME"] = "0"
        os.environ.pop("CRYPTO_JOB_LABEL", None)
        text = format_alert({"module": "crypto-news", "level": "P1", "category": "macro",
                             "title": "t", "titles": ["t"], "sources": ["x"], "count": 1})
        self.assertIn("任务：crypto-news（手动运行）", text)

    def test_missing_env(self):
        os.environ.pop("XPC_SERVICE_NAME", None)
        self.assertEqual(job_label("crypto-analyst"), "crypto-analyst（手动运行）")


if __name__ == "__main__":
    unittest.main()
