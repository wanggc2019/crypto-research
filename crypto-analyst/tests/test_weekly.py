#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weekly.py 单测：离线 fixtures 生成周报（不发送）。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MOD, "scripts"))
import daily_brief  # noqa: E402
import weekly  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


class TestWeekly(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = daily_brief.load_config(MOD)
        cls.label, cls.md, cls.tg = weekly.build(cfg, "2026-11-08", base_dir=FIX, repo_root=FIX)

    def test_label(self):
        self.assertEqual(self.label, "2026-W45")
        self.assertEqual(weekly.week_label("2025-12-29"), "2026-W01")

    def test_markdown_sections(self):
        for must in ("# crypto-analyst 周报", "## 一、行情（周）", "## 二、位置（日线状态）",
                     "## 三、BTC 结构", "## 四、本周事件", "## 五、BTC 场景",
                     "## 六、数据与复盘", "不构成投资建议"):
            self.assertIn(must, self.md)

    def test_telegram_compact(self):
        for must in ("【crypto-analyst】周报", "行情：", "场景（约", "🏷 任务：", "不构成投资建议"):
            self.assertIn(must, self.tg)
        self.assertLess(len(self.tg), 1500)

    def test_weekly_bar(self):
        import core
        bars = core.load_bars("BTC", FIX)
        w = weekly.weekly_bar(bars)
        self.assertEqual(w["days"], 7)
        self.assertAlmostEqual(w["open"], bars[-7]["open"])
        self.assertAlmostEqual(w["close"], bars[-1]["close"])
        self.assertGreaterEqual(w["high"], max(b["high"] for b in bars[-7:]) - 1e-9)
        self.assertLessEqual(w["low"], min(b["low"] for b in bars[-7:]) + 1e-9)

    def test_dry_run(self):
        self.assertEqual(weekly.main(["--fixtures", FIX, "--date", "2026-11-08", "--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
