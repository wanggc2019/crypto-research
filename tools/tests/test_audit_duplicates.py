#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_duplicates 纯函数/文件审计单测（离线）。"""
import datetime
import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit_duplicates  # noqa: E402

T0 = datetime.datetime(2026, 11, 8, 12, 0, 0)


class TestDuplicateSends(unittest.TestCase):
    def test_same_key_within_window_is_duplicate(self):
        records = [
            ("price:BTC:fast_move:down", T0, "BTC 急跌", "a.json", "", False),
            ("price:BTC:fast_move:down", T0 + datetime.timedelta(minutes=3), "BTC 急跌", "a.json", "", False),
        ]
        dups = audit_duplicates.find_duplicate_sends(records, window_min=10)
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["gap_seconds"], 180)

    def test_outside_window_is_not_duplicate(self):
        records = [
            ("price:BTC:fast_move:down", T0, "BTC 急跌", "a.json", "", False),
            ("price:BTC:fast_move:down", T0 + datetime.timedelta(minutes=20), "BTC 急跌", "a.json", "", False),
        ]
        self.assertEqual(audit_duplicates.find_duplicate_sends(records, window_min=10), [])

    def test_different_keys_not_duplicate(self):
        records = [
            ("price:BTC:fast_move:down", T0, "BTC 急跌", "a.json", "", False),
            ("price:ETH:fast_move:down", T0, "ETH 急跌", "a.json", "", False),
        ]
        self.assertEqual(audit_duplicates.find_duplicate_sends(records, window_min=10), [])

    def test_sent_records_accept_send_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = "crypto-price"
            state = Path(tmp) / module / "data" / "state"
            state.mkdir(parents=True)
            rows = [
                {"dedupe_key": "price:BTC:fast_move:down", "title": "x",
                 "ts": "2026-11-08T12:00:00", "delivery": {"decision": "send"}},
                {"dedupe_key": "price:BNB:new_extreme:up", "title": "y",
                 "ts": "2026-11-08T12:01:00", "delivery": {"decision": "suppress"}},
            ]
            (state / "alerts-2026-11-08.json").write_text(json.dumps(rows), encoding="utf-8")
            with mock.patch.object(audit_duplicates, "REPO", tmp):
                records = audit_duplicates.sent_records(module)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][0], "price:BTC:fast_move:down")

    def test_escalation_is_not_duplicate(self):
        records = [
            ("price:ETH:volume_spike:down", T0, "9x", "a.json", "", False),
            ("price:ETH:volume_spike:down", T0 + datetime.timedelta(seconds=64), "31x", "a.json",
             "冷却中，但幅度扩大 ≥1.5 倍，允许升级推送", True),
        ]
        self.assertEqual(audit_duplicates.find_duplicate_sends(records, window_min=10), [])


class TestLaunchdDuplicates(unittest.TestCase):
    def test_same_script_in_two_plists(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "Library" / "LaunchAgents"
            folder.mkdir(parents=True)
            for label in ("a", "b"):
                data = {
                    "Label": "com.crypto-research.%s" % label,
                    "ProgramArguments": ["/bin/bash", "/repo/crypto-price/scripts/collect.sh"],
                }
                with open(folder / ("%s.plist" % label), "wb") as f:
                    plistlib.dump(data, f)
            labels, dups = audit_duplicates.launchd_duplicates(home)
        self.assertEqual(len(labels), 2)
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["labels"], ["com.crypto-research.a", "com.crypto-research.b"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
