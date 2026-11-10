#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日报生成单测（离线；用临时目录，不写模块 data/）。"""
import datetime
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import report  # noqa: E402

TOKEN = "123456789:" + "A" * 35
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "123"}

WATCHLIST = {
    "assets": {
        "BTC": {"name": "Bitcoin"},
        "ETH": {"name": "Ethereum"},
        "BNB": {"name": "BNB"},
    }
}

SNAPSHOT = {
    "ts": "2026-11-08T08:00:00",
    "sources_ok": 9,
    "sources_total": 9,
    "quotes": {
        "BTC": {"price": 75790.5, "n_sources": 3, "spread_bps": 3.1},
        "ETH": {"price": 2251.3, "n_sources": 3, "spread_bps": 4.0},
        "BNB": {"price": 695.9, "n_sources": 2, "spread_bps": 8.0},
    },
    "stats": {
        "BTC": {"high": 76251.1, "low": 75569.1, "open": 75928.4, "change_pct": -0.18, "volume": 2055.9},
        "ETH": {"high": 2300.0, "low": 2200.0, "open": 2270.0, "change_pct": -0.82, "volume": 1000.0},
        "BNB": {"high": 710.0, "low": 690.0, "open": 700.0, "change_pct": -0.59, "volume": 500.0},
    },
    "errors": [],
}

APP_CFG = {"report": {"enabled": True, "time": "08:00", "send": True}}


class TestReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = self.tmp.name
        self.state_dir = os.path.join(self.data, "state")
        os.makedirs(self.state_dir)
        self.patcher = mock.patch.object(report.common, "log_line", return_value="")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_is_due_window(self):
        state = {"last_generated_date": None}
        self.assertTrue(report.is_due(datetime.datetime(2026, 11, 8, 8, 0), APP_CFG, state))
        self.assertFalse(report.is_due(datetime.datetime(2026, 11, 8, 7, 59), APP_CFG, state))
        self.assertFalse(report.is_due(datetime.datetime(2026, 11, 8, 23, 0), APP_CFG, state))
        self.assertFalse(report.is_due(datetime.datetime(2026, 11, 8, 9, 0), APP_CFG,
                                      {"last_generated_date": "2026-11-08"}))

    def test_build_markdown(self):
        md = report.build_markdown("2026-11-08", SNAPSHOT, self.state_dir, WATCHLIST,
                                   datetime.datetime(2026, 11, 8, 8, 0))
        self.assertIn("# crypto-price 日报 2026-11-08", md)
        self.assertIn("| BTC | 75,790.50 |", md)
        self.assertIn("24h 高", md)
        self.assertIn("数据源健康", md)

    def test_build_telegram(self):
        text = report.build_telegram("2026-11-08", SNAPSHOT, self.state_dir, WATCHLIST,
                                     datetime.datetime(2026, 11, 8, 8, 0))
        self.assertIn("crypto-price", text)
        self.assertIn("早报", text)
        self.assertIn("75,790.50", text)
        self.assertIn("告警", text)

    def test_maybe_run_dry_run_writes_nothing(self):
        res = report.maybe_run(self.state_dir, SNAPSHOT, WATCHLIST, APP_CFG, env=ENV,
                               now=datetime.datetime(2026, 11, 8, 8, 0), dry_run=True)
        self.assertFalse(res["generated"])
        self.assertIn("preview", res)
        self.assertFalse(os.path.exists(os.path.join(self.data, "reports")))

    def test_maybe_run_generates_and_sends_once(self):
        sent = []

        def fake_send(token, chat, text, silent):
            sent.append(text)
            return True, ""

        now = datetime.datetime(2026, 11, 8, 8, 0)
        res = report.maybe_run(self.state_dir, SNAPSHOT, WATCHLIST, APP_CFG, env=ENV,
                               now=now, send_fn=fake_send)
        self.assertTrue(res["generated"])
        self.assertTrue(res["sent"])
        self.assertEqual(len(sent), 1)
        self.assertTrue(os.path.exists(os.path.join(self.data, "reports", "2026-11-08.md")))
        # 同一天再跑不重复生成
        res2 = report.maybe_run(self.state_dir, SNAPSHOT, WATCHLIST, APP_CFG, env=ENV,
                                now=now + datetime.timedelta(minutes=5), send_fn=fake_send)
        self.assertFalse(res2["generated"])
        self.assertEqual(len(sent), 1)

    def test_force_bypasses_due_check(self):
        res = report.maybe_run(self.state_dir, SNAPSHOT, WATCHLIST, APP_CFG, env=ENV,
                               now=datetime.datetime(2026, 11, 8, 3, 0), dry_run=True, force=True)
        self.assertIn("preview", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
