#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_brief.py 单元测试：离线 fixtures 回放。"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MOD, "scripts"))
import daily_brief  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


class TestBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = daily_brief.load_config(MOD)
        cls.text = daily_brief.build(cfg, FIX, "2026-11-08")

    def test_sections(self):
        for must in ("# 每日行情简报", "## 一、一句话状态", "## 二、四币状态",
                     "## 三、BTC 结构", "## 四、事件", "## 五、BTC 场景预测",
                     "## 六、待打分", "## 数据与口径"):
            self.assertIn(must, self.text)

    def test_all_assets(self):
        for sym in ("BTC", "ETH", "BNB", "SOL"):
            self.assertIn(sym, self.text)

    def test_probabilities_sum(self):
        # 场景概率应为 100%
        cfg = daily_brief.load_config(MOD)
        total = sum(s.get("prob", 0) for s in cfg["scenarios"]["set"].values())
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_disclaimer(self):
        self.assertIn("不构成投资建议", self.text)

    def test_dry_run_returns_zero(self):
        rc = daily_brief.main(["--fixtures", FIX, "--date", "2026-11-08", "--dry-run"])
        self.assertEqual(rc, 0)


class TestOneLiner(unittest.TestCase):
    def test_shape(self):
        rows = [
            {"sym": "BTC", "vs_ma200_pct": 3.9, "vs_ma50_pct": -7.1,
             "vol_ratio_30_90": 0.89, "vs_52w_low_pct": 31.6, "vs_90d_high_pct": -13.0},
            {"sym": "ETH", "vs_ma200_pct": 4.0, "vs_ma50_pct": -9.8,
             "vol_ratio_30_90": 0.83, "vs_52w_low_pct": 48.9, "vs_90d_high_pct": -20.1},
        ]
        text = daily_brief.one_liner(rows, rows[0])
        self.assertIn("BTC/ETH：2/2 在 MA200", text)
        self.assertIn("2/2 在 MA50", text)


if __name__ == "__main__":
    unittest.main()
