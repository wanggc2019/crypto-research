#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_history.py 纯函数单测（离线、不联网）。"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MOD, "scripts"))
import fetch_history  # noqa: E402


def bar(date, close):
    return {"date": date, "open": close, "high": close, "low": close,
            "close": close, "volume": 1.0}


class TestMergeBars(unittest.TestCase):
    def test_appends_new_days(self):
        old = [bar("2026-11-07", 100), bar("2026-11-08", 101)]
        new = [bar("2026-11-09", 102)]
        merged = fetch_history.merge_bars(old, new)
        self.assertEqual([b["date"] for b in merged],
                         ["2026-11-07", "2026-11-08", "2026-11-09"])

    def test_new_bar_overrides_same_day(self):
        old = [bar("2026-11-08", 101)]
        new = [bar("2026-11-08", 555)]  # 当天未收盘 → 用最新数据覆盖
        merged = fetch_history.merge_bars(old, new)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["close"], 555)

    def test_sorted_and_deduped(self):
        old = [bar("2026-11-09", 1), bar("2026-11-07", 2)]
        new = [bar("2026-11-08", 3)]
        merged = fetch_history.merge_bars(old, new)
        self.assertEqual([b["date"] for b in merged],
                         ["2026-11-07", "2026-11-08", "2026-11-09"])

    def test_bad_entries_ignored(self):
        merged = fetch_history.merge_bars([None, {"no": "date"}], [bar("2026-11-08", 1)])
        self.assertEqual(len(merged), 1)

    def test_empty_inputs(self):
        self.assertEqual(fetch_history.merge_bars([], []), [])
        self.assertEqual(fetch_history.merge_bars(None, None), [])


class TestLoadCached(unittest.TestCase):
    def test_missing_file(self):
        self.assertEqual(fetch_history.load_cached("/nonexistent/x.json"), [])

    def test_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "k.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{broken")
            self.assertEqual(fetch_history.load_cached(path), [])

    def test_non_list_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "k.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"date": "2026-11-08"}, f)
            self.assertEqual(fetch_history.load_cached(path), [])

    def test_valid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "k.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump([bar("2026-11-08", 1)], f)
            self.assertEqual(len(fetch_history.load_cached(path)), 1)


class TestUtcMs(unittest.TestCase):
    def test_known_dates(self):
        self.assertEqual(fetch_history._utc_ms("2026-11-08"), 1794096000000)
        self.assertEqual(fetch_history._utc_ms("2019-01-01"), 1546300800000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
