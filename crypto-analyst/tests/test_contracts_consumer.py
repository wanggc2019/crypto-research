# -*- coding: utf-8 -*-
"""消费方契约测试：用 crypto_core 的标准样例铺一个假仓库，确认 analyst 真能读到各模块 exports。

（2026-11-16 前 weekly/archive_events 读的是不存在的 crypto-kol 目录，KOL 一直为空却没有测试发现。）"""
import os
import sys
import tempfile
import unittest
from unittest import mock

MOD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(MOD, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(MOD), "libs", "crypto_core", "src"))
import archive_events  # noqa: E402
import daily_brief  # noqa: E402
import weekly  # noqa: E402

from crypto_core import contracts  # noqa: E402

DATE = "2026-11-08"


class AnalystConsumesContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = contracts.write_example_repo(self.tmp.name, DATE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_daily_brief_reads_news_and_price(self):
        events = daily_brief.load_events(self.repo, DATE)
        self.assertEqual([e["severity"] for e in events], ["P0", "P1"])
        snap = daily_brief.load_price_snapshot(self.repo)
        self.assertIn("BTC", snap["assets"])

    def test_weekly_reads_every_source(self):
        dates = [DATE]
        self.assertEqual(len(weekly.load_week_events(self.repo, dates)), 2)
        self.assertEqual(len(weekly.load_policy(self.repo, dates)), 1)
        self.assertEqual(len(weekly.load_kol(self.repo, dates)), 1, "KOL 必须从 crypto-x-kol 读到")
        sigs = weekly.load_week_signals(self.repo, dates)
        self.assertEqual([s["rule"] for s in sigs], ["move_1h"], "data_quality 不进周报")
        self.assertEqual(weekly.news_counts(self.repo, dates), {"P0": 1, "P1": 1, "P2": 1})
        self.assertEqual(weekly.export_coverage(self.repo, dates), {"price": 1, "news": 1})

    def test_archive_events_reads_news_policy_kol(self):
        hist = os.path.join(self.tmp.name, "hist")
        with mock.patch.object(archive_events, "REPO", self.repo), \
                mock.patch.object(archive_events, "HERE", hist):
            archive_events.main(["--date", DATE])
        records = archive_events.load(os.path.join(hist, "data", "history", DATE[:7] + ".json"))
        self.assertEqual(sorted({r["source"] for r in records}), ["kol", "news", "policy"])


if __name__ == "__main__":
    unittest.main()
