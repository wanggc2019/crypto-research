#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py 端到端单测：离线快照回放，验证事件/落盘/退出码/锁。

全部写临时 data 目录，不碰模块 data/，不联网、不推送。
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import run  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def call_main(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = run.main(args)
    return rc, out.getvalue(), err.getvalue()


class TestReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _json(self, args):
        rc, out, _ = call_main(args + ["--dry-run", "--json", "--data-dir", self.data])
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def test_normal_snapshot_no_events(self):
        payload = self._json(["--snapshot", os.path.join(FIXTURES, "snapshot_normal.json")])
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["source_health"]["ok"], 9)
        self.assertEqual(payload["source_health"]["total"], 9)

    def test_shock_snapshot_triggers_p0_fast_move(self):
        payload = self._json(["--snapshot", os.path.join(FIXTURES, "snapshot_shock.json")])
        events = payload["events"]
        self.assertTrue(events)
        self.assertEqual(events[0]["rule"], "fast_move")
        self.assertEqual(events[0]["asset"], "BTC")
        self.assertEqual(events[0]["severity"], "P0")
        self.assertEqual(events[0]["dedupe_key"], "price:BTC:fast_move:down")
        titles = payload["delivery"]["sent"]
        self.assertTrue(any("BTC" in t and "3.50%" in t for t in titles), titles)

    def test_dry_run_writes_no_files(self):
        self._json(["--snapshot", os.path.join(FIXTURES, "snapshot_normal.json")])
        files = [os.path.join(d, f) for d, _, fs in os.walk(self.data) for f in fs]
        self.assertEqual(files, [])

    def test_write_cycle_outputs(self):
        rc, out, _ = call_main([
            "--snapshot", os.path.join(FIXTURES, "snapshot_shock.json"),
            "--data-dir", self.data, "--json", "--no-lock",
        ])
        self.assertEqual(rc, 0, out)
        for rel in ("exports/latest.json", "state/last_run.json", "state/market_state.json"):
            self.assertTrue(os.path.exists(os.path.join(self.data, rel)), rel)
        with open(os.path.join(self.data, "exports", "latest.json"), encoding="utf-8") as f:
            export = json.load(f)
        self.assertEqual(export["schema"], "price/snapshot/v1")
        self.assertEqual(export["source_module"], "crypto-price")
        self.assertIn("BTC", export["assets"])
        self.assertTrue(any(s["rule"] == "fast_move" for s in export["signals"]))
        # raw 快照按日期落盘
        raw_dirs = os.listdir(os.path.join(self.data, "raw"))
        self.assertEqual(len(raw_dirs), 1)

    def test_lock_conflict_returns_4(self):
        state = os.path.join(self.data, "state")
        os.makedirs(state)
        lock = os.path.join(state, "collect.lock")
        os.makedirs(lock)
        rc, out, _ = call_main([
            "--snapshot", os.path.join(FIXTURES, "snapshot_normal.json"),
            "--data-dir", self.data, "--json",
        ])
        self.assertEqual(rc, 4, out)
        self.assertIn("上一轮", out)

    def test_status_json(self):
        rc, out, _ = call_main(["--status", "--json", "--data-dir", self.data])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["module"], "crypto-price")
        self.assertEqual(set(payload["assets"]), {"BTC", "ETH", "BNB"})

    def _snapshot_with_sources(self, keep):
        with open(os.path.join(FIXTURES, "snapshot_normal.json"), encoding="utf-8") as f:
            snap = json.load(f)
        all_primary = {"binance", "okx", "coinbase"}
        for quote in snap["quotes"].values():
            quote["sources"] = {k: v for k, v in quote["sources"].items() if k in keep}
            quote["n_sources"] = len(quote["sources"])
            quote["primary_missing"] = sorted(all_primary - keep)
            vals = [v["price"] for v in quote["sources"].values()]
            if vals:
                quote["price"] = sorted(vals)[len(vals) // 2]
        snap["sources_ok"] = sum(len(q["sources"]) for q in snap["quotes"].values())
        snap["errors"] = []
        path = os.path.join(self.data, "snap.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snap, f)
        return path

    def test_non_core_source_failure_is_ignored(self):
        # 只有 Binance/OKX，Coinbase 失败：核心源有价 → rc=0，不告警
        path = self._snapshot_with_sources({"binance", "okx"})
        rc, out, _ = call_main(["--snapshot", path, "--data-dir", self.data,
                                "--dry-run", "--json"])
        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        self.assertTrue(payload["primary_ok"])
        self.assertFalse(payload["primary_complete"])
        self.assertTrue(payload["primary_missing"])

    def test_single_primary_source_still_no_alert(self):
        # 只剩 Coinbase：1 个主源也能出价 → rc=0，不告警（只标记降级）
        path = self._snapshot_with_sources({"coinbase"})
        rc, out, _ = call_main(["--snapshot", path, "--data-dir", self.data,
                                "--dry-run", "--json"])
        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        self.assertTrue(payload["primary_ok"])
        self.assertFalse(payload["primary_complete"])
        self.assertEqual(set(payload["primary_missing"]), {"BTC", "ETH", "BNB"})
        self.assertEqual(payload["events"], [])

    def test_outage_when_all_sources_down(self):
        snapshot = {
            "ts": "2026-11-08T00:00:00", "ts_epoch": 1794081600,
            "quotes": {}, "stats": {}, "candles": {}, "funding": {}, "oi": {},
            "errors": [{"source": "binance", "asset": "BTC", "error": "TimeoutError"}],
            "sources_ok": 0, "sources_total": 9,
        }
        path = os.path.join(self.data, "outage.json")
        os.makedirs(self.data, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
        rc, out, _ = call_main(["--snapshot", path, "--data-dir", self.data, "--dry-run", "--json"])
        self.assertEqual(rc, 3, out)
        payload = json.loads(out)
        self.assertEqual(payload["events"][0]["dedupe_key"], "price:ALL:data_quality:flat")
        self.assertEqual(payload["events"][0]["severity"], "P0")
        self.assertTrue(payload["events"][0]["force_send"])


class TestSimulatedAlert(unittest.TestCase):
    def test_test_alert_dry_run_preview_has_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = call_main(["--test-alert", "--dry-run", "--data-dir", tmp])
            self.assertEqual(rc, 0, out)
            self.assertIn("【模拟测试】", out)
            self.assertIn("不是真实行情", out)
            self.assertIn("急涨急跌（fast_move）", out)
            self.assertIn("dry-run", out)


class TestReportCommand(unittest.TestCase):
    def test_report_dry_run_prints_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = call_main([
                "--report", "--dry-run", "--data-dir", tmp,
                "--snapshot", os.path.join(FIXTURES, "snapshot_normal.json"),
            ])
            self.assertEqual(rc, 0, out)
            self.assertIn("crypto-price 日报", out)
            self.assertIn("Telegram", out)


class TestHealthcheckLine(unittest.TestCase):
    def test_healthcheck_line_format(self):
        out = run._healthcheck_line({"source_health": {"ok": 8, "total": 9},
                                     "events": [1, 2]})
        self.assertRegex(out, r"^采集完成：9 个源，成功 8，失败 1，新增 2 条$")

    def test_prices_brief(self):
        text = run._prices_brief({"quotes": {"BTC": {"price": 75790.5}},
                                  "stats": {"BTC": {"change_pct": -0.18}}})
        self.assertIn("BTC 75,790.50", text)
        self.assertIn("-0.18%", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
