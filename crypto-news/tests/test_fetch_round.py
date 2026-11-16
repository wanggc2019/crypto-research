# -*- coding: utf-8 -*-
"""fetch.py 一轮采集：源分派 + 单源失败隔离 + 落盘/去重状态（不联网，桩采集函数）。"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fetch  # noqa: E402


class CollectorDispatchTest(unittest.TestCase):
    def test_dispatch_by_id_and_type(self):
        cases = [
            ({"id": "binance_ann", "type": "html_diff", "url": "x"}, fetch.collect_binance, False),
            ({"id": "a", "type": "rss", "url": "x"}, fetch.collect_rss, False),
            ({"id": "a", "type": "market_api", "url": "x"}, fetch.collect_market, False),
            ({"id": "a", "type": "warn", "url": "x"}, fetch.collect_warn, False),
            ({"id": "a", "type": "html_diff", "url": "https://t.me/s/foo"}, fetch.collect_tme, False),
            ({"id": "a", "type": "html_diff", "url": "https://r.jina.ai/https://x"}, fetch.collect_jina, True),
            ({"id": "a", "type": "html_diff", "url": "https://example.com"}, fetch.collect_html, True),
            ({"id": "a", "type": "unknown"}, None, False),
        ]
        for src, fn, with_hash in cases:
            with self.subTest(src=src):
                self.assertEqual(fetch._collector(src), (fn, with_hash))


class RunSourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="news-fetch-")
        self.out = os.path.join(self.tmp, "items.jsonl")
        self.patch = mock.patch.object(fetch, "STATE_DIR", self.tmp)
        self.patch.start()
        self.seen = {"date": "d", "ids": {}}
        self.stats = {"sources": [], "errors": []}

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_items_are_persisted_and_marked_seen(self):
        src = {"id": "s1", "type": "rss", "url": "x", "name": "S1", "tier": "T1"}
        with mock.patch.object(fetch, "collect_rss", return_value=[{"id": "i1", "title": "t"}]):
            n = fetch._run_source(src, self.out, self.seen, self.stats)
        self.assertEqual(n, 1)
        with open(self.out) as f:
            rec = json.loads(f.readline())
        self.assertEqual((rec["source_id"], rec["source_name"], rec["tier"]), ("s1", "S1", "T1"))
        self.assertIn("i1", self.seen["ids"])
        with open(os.path.join(self.tmp, "s1.json")) as f:
            self.assertIn("i1", json.load(f)["seen"])
        self.assertEqual(self.stats["sources"][0]["ok"], True)

    def test_source_error_is_recorded_not_raised(self):
        src = {"id": "s2", "type": "html_diff", "url": "https://example.com"}
        with mock.patch.object(fetch, "collect_html", side_effect=OSError("boom")):
            n = fetch._run_source(src, self.out, self.seen, self.stats)
        self.assertEqual(n, 0)
        self.assertEqual(self.stats["errors"][0]["id"], "s2")
        self.assertIn("OSError: boom", self.stats["errors"][0]["error"])
        self.assertFalse(os.path.exists(self.out))

    def test_malformed_source_config_only_fails_that_source(self):
        n = fetch._run_source({"id": "s4"}, self.out, self.seen, self.stats)
        self.assertEqual(n, 0)
        self.assertEqual(self.stats["errors"][0]["id"], "s4")
        self.assertIn("KeyError", self.stats["errors"][0]["error"])

    def test_unknown_type_is_skipped_without_state_write(self):
        n = fetch._run_source({"id": "s3", "type": "carrier_pigeon"}, self.out, self.seen, self.stats)
        self.assertEqual(n, 0)
        self.assertEqual(self.stats["sources"], [{"id": "s3", "status": "skipped", "type": "carrier_pigeon"}])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "s3.json")))


if __name__ == "__main__":
    unittest.main()
