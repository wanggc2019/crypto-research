#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""events / dedupe 单测。"""
import tempfile
import unittest
from pathlib import Path

from crypto_core import dedupe, events


class TestEvents(unittest.TestCase):
    def test_event_key_ignores_score_and_sources(self):
        a = {"titles": ["Binance <suspends> withdrawals"], "score": 80}
        b = dict(a, score=99.9, sources=["other"], count=9)
        self.assertEqual(events.event_key(a), events.event_key(b))

    def test_event_key_changes_with_title(self):
        a = {"titles": ["event A"]}
        b = {"titles": ["event B"]}
        self.assertNotEqual(events.event_key(a), events.event_key(b))

    def test_severity_mapping(self):
        self.assertEqual(events.severity_of({"level": "P0"}), "P0")
        self.assertEqual(events.severity_of({"severity": "P2"}), "P2")
        self.assertEqual(events.severity_of({"level": "critical"}), "P0")
        self.assertEqual(events.severity_of({"level": "warn"}), "P1")
        self.assertEqual(events.severity_of({"level": "info"}), "P2")

    def test_alert_roundtrip(self):
        alert = events.Alert(
            module="crypto-news",
            severity="P0",
            category="exchange_custody",
            title="Binance pauses withdrawals",
            assets=["BNB"],
            detail=["fact"],
            urls=["https://example.com/a"],
        )
        d = alert.to_dict()
        self.assertEqual(d["schema"], "alert/v1")
        self.assertEqual(d["module"], "crypto-news")
        self.assertEqual(len(d["dedupe_key"]), 24)
        self.assertTrue(d["ts"])
        again = events.Alert.from_dict(d)
        self.assertEqual(again.to_dict()["title"], alert.title)
        self.assertEqual(again.to_dict()["dedupe_key"], d["dedupe_key"])

    def test_alert_accepts_legacy_severity(self):
        alert = events.Alert(module="m", severity="critical", category="c", title="t")
        self.assertEqual(alert.severity, "P0")


class TestDedupe(unittest.TestCase):
    def test_daily_keys_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notified.json"
            store = dedupe.DailyKeys(p, "2026-11-08")
            self.assertFalse(store.seen("k1"))
            store.mark("k1", "2026-11-08T02:00:00")
            store.save()
            again = dedupe.DailyKeys(p, "2026-11-08")
            self.assertTrue(again.seen("k1"))
            self.assertEqual(again.keys["k1"], "2026-11-08T02:00:00")

    def test_daily_keys_rollover(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "notified.json"
            first = dedupe.DailyKeys(p, "2026-11-08")
            first.mark("k1")
            first.save()
            nxt = dedupe.DailyKeys(p, "2026-11-09")
            self.assertFalse(nxt.seen("k1"))

    def test_cooldown(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cooldown.json"
            cd = dedupe.Cooldown(p, minutes=30)
            self.assertTrue(cd.allow("BTC:newlow", now_ts=1000.0))
            self.assertFalse(cd.allow("BTC:newlow", now_ts=1000.0 + 60 * 10))
            self.assertTrue(cd.allow("BTC:newlow", now_ts=1000.0 + 60 * 31))
            cd.save()
            again = dedupe.Cooldown(p, minutes=30)
            self.assertFalse(again.allow("BTC:newlow", now_ts=1000.0 + 60 * 31))


if __name__ == "__main__":
    unittest.main(verbosity=2)
