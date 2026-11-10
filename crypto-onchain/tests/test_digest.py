#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""digest.py 的离线单测：到点判断、摘要统计、dry-run 不写状态。

运行：cd crypto-onchain && ../.venv/bin/python -m unittest tests.test_digest -v
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import digest  # noqa: E402

CFG = {
    "app": {
        "timezone": "UTC",
        "digest": {"enabled": True, "time": "21:00", "start_date": "2026-11-09", "calibration_days": 14},
    },
}


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class TestDue(unittest.TestCase):
    def test_before_time(self):
        import datetime
        now = datetime.datetime(2026, 11, 9, 20, 0, 0)
        due, reason = digest.is_due(CFG, {}, now=now)
        self.assertFalse(due)
        self.assertIn("未到", reason)

    def test_after_time_not_sent(self):
        import datetime
        now = datetime.datetime(2026, 11, 9, 21, 5, 0)
        due, reason = digest.is_due(CFG, {}, now=now)
        self.assertTrue(due)

    def test_already_sent_today(self):
        import datetime
        now = datetime.datetime(2026, 11, 9, 22, 0, 0)
        due, reason = digest.is_due(CFG, {"last_sent_date": "2026-11-09"}, now=now)
        self.assertFalse(due)
        self.assertIn("已发", reason)

    def test_disabled(self):
        cfg = {"app": {"digest": {"enabled": False}}}
        due, _reason = digest.is_due(cfg, {})
        self.assertFalse(due)


class TestSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onchain-digest-")
        self.paths = {
            "root": self.tmp,
            "state": os.path.join(self.tmp, "state"),
            "raw": os.path.join(self.tmp, "raw"),
            "logs": os.path.join(self.tmp, "logs"),
            "exports": os.path.join(self.tmp, "exports"),
            "reports": os.path.join(self.tmp, "reports"),
        }
        self.today = digest.common.today_str()
        _write(os.path.join(self.paths["state"], "shadow-latest.json"), {
            "generated_at": self.today + "T19:30:00",
            "assets": {
                "BTC": {"price": 75000.0, "change_24h_pct": -1.2},
                "ETH": {"price": 2130.0, "change_24h_pct": -4.9},
                "BNB": {"price": 688.0, "change_24h_pct": -1.3},
            },
            "cex_flows": {"net_24h_bps": -12.6, "net_24h_usd": -227000000.0},
            "stablecoins": {"net_issue_24h_usd": -278000000.0},
        })
        _write_jsonl(os.path.join(self.paths["state"], "shadow-candidates.jsonl"), [
            {"generated_at": self.today + "T18:00:00", "severity": "P1", "would_alert": True},
            {"generated_at": self.today + "T19:00:00", "severity": "P0", "would_alert": False},
            {"generated_at": "2026-01-01T00:00:00", "severity": "P0", "would_alert": True},  # 非今天
        ])
        _write_jsonl(os.path.join(self.paths["raw"], self.today, "walletflow.jsonl"), [
            {"ts": 1794177000, "symbol": "USDT", "usd": 8_000_000.0,
             "from": "0x1111111111111111111111111111111111111111",
             "to": "0x2222222222222222222222222222222222222222", "direction": "unknown"},
        ])
        _write_jsonl(os.path.join(self.paths["raw"], self.today, "attributions.jsonl"), [
            {"generated_at": self.today + "T18:08:00", "asset": "ETH",
             "category": "空头加仓 / 主动砸盘", "confidence": "medium"},
            {"generated_at": self.today + "T18:30:00", "asset": None, "scope": "market",
             "category": "增量资金撤离（卖压背景）", "confidence": "medium"},
        ])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_counts_and_tone(self):
        text, data = digest.build_summary(CFG, self.paths, date=self.today)
        self.assertEqual(data["candidates"], 2)
        self.assertEqual(data["by_severity"]["P0"], 1)
        self.assertEqual(data["by_severity"]["P1"], 1)
        self.assertEqual(data["would_alert"], 1)
        self.assertEqual(data["attributions"], 2)
        self.assertIn("空头加仓", text)
        self.assertIn("每日链上情报", text)
        self.assertIn("链上大额转账", text)
        self.assertEqual(data["walletflow"]["n"], 1)
        self.assertAlmostEqual(data["walletflow"]["usd_total"], 8_000_000)
        self.assertIn("Day", text)
        self.assertNotIn("谁在卖", text.replace("不回答“谁在卖”", ""))  # 只在口径说明里出现

    def test_dry_run_prints_and_no_state(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = digest.main(["--data-dir", self.tmp, "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("影子日报", buf.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "state", "digest.json")))

    def test_json_output(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = digest.main(["--data-dir", self.tmp, "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertEqual(data["candidates"], 2)
        self.assertEqual(data["calibration_total"], 14)


if __name__ == "__main__":
    unittest.main()
