#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""keep_awake plist 构造单测（离线）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keep_awake  # noqa: E402


class TestKeepAwake(unittest.TestCase):
    def test_plist_uses_caffeinate_s_only(self):
        data = keep_awake.build_plist("/tmp/repo")
        self.assertEqual(data["Label"], keep_awake.LABEL)
        self.assertEqual(data["ProgramArguments"], ["/usr/bin/caffeinate", "-s"])
        self.assertTrue(data["RunAtLoad"])
        self.assertTrue(data["KeepAlive"])
        self.assertEqual(data["StandardOutPath"], "/tmp/repo/data/logs/caffeinate.out.log")


if __name__ == "__main__":
    unittest.main(verbosity=2)
