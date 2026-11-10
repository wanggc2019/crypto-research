#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""common.py 单测：配置加载、目录、单实例锁（离线）。"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import common  # noqa: E402


class TestConfigs(unittest.TestCase):
    def test_all_configs_have_short_keys(self):
        cfg = common.load_all_configs()
        self.assertEqual(set(cfg.keys()), {"watchlist", "rules", "app"})
        for key, value in cfg.items():
            self.assertIsInstance(value, dict, key)
            self.assertEqual(value.get("schema_version"), 1, key)

    def test_watchlist_assets(self):
        cfg = common.load_all_configs()
        self.assertEqual(list(cfg["watchlist"]["assets"].keys()), ["BTC", "ETH", "BNB"])
        for symbol, asset in cfg["watchlist"]["assets"].items():
            self.assertTrue(asset.get("binance"), symbol)
            self.assertTrue(asset.get("okx"), symbol)
            self.assertTrue(asset.get("coinbase"), symbol)

    def test_rules_has_all_eight_rules(self):
        rules = common.load_all_configs()["rules"]["rules"]
        expected = {"fast_move", "day_move", "new_extreme", "volume_spike",
                    "funding", "open_interest", "level_break"}
        self.assertTrue(expected.issubset(set(rules.keys())))
        self.assertIn("delivery", common.load_all_configs()["rules"])

    def test_app_sources_and_poll(self):
        app = common.load_all_configs()["app"]
        self.assertEqual(app["poll_seconds"], 60)
        for src in ("binance", "okx", "coinbase"):
            self.assertIn(src, app["sources"])


class TestPaths(unittest.TestCase):
    def test_data_paths_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = common.data_paths(tmp)
            self.assertEqual(paths["state"], os.path.join(tmp, "state"))
            self.assertTrue(common.ensure_dirs(tmp))
            self.assertTrue(os.path.isdir(paths["exports"]))

    def test_raw_file_date(self):
        paths = common.data_paths("/tmp/x")
        self.assertTrue(common.raw_file(paths, "a.jsonl", "2026-11-08").endswith(
            os.path.join("raw", "2026-11-08", "a.jsonl")))


class TestLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = common.data_paths(self.tmp.name)
        common.ensure_dirs(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_acquire_release(self):
        lock = common.acquire_lock(self.paths)
        self.assertIsNotNone(lock)
        self.assertIsNone(common.acquire_lock(self.paths))  # 第二个实例拿不到
        lock.release()
        lock2 = common.acquire_lock(self.paths)
        self.assertIsNotNone(lock2)
        lock2.release()

    def test_stale_lock_can_be_taken(self):
        lock = common.acquire_lock(self.paths)
        self.assertIsNotNone(lock)
        lock_path = os.path.join(self.paths["state"], "collect.lock")
        old = time.time() - 3600
        os.utime(lock_path, (old, old))
        fresh = common.acquire_lock(self.paths, stale_seconds=900)
        self.assertIsNotNone(fresh)
        fresh.release()


if __name__ == "__main__":
    unittest.main(verbosity=2)
