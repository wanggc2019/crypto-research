#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取数：fixtures 按 API 语义切片、30 天上限（-1130）与回退、HTTP 错误分 skip / error。"""
import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import helpers  # noqa: E402
import run  # noqa: E402
import sources  # noqa: E402
from sources import Fetcher, FetchError  # noqa: E402

import common  # noqa: E402

APP = common.load_config("app.yaml")


class TestFixtureFetcher(unittest.TestCase):
    def test_recent_keeps_only_closed_periods(self):
        f = Fetcher(APP, fixture_dir=helpers.FIXTURES, now=helpers.BNB_P0 + 60)
        pts = f.recent("BNB", "BNBUSDT", "G")
        self.assertEqual(len(pts), 11)                      # 12 个点里最后一个（01:10 周期）还没收盘
        self.assertEqual(max(pts), helpers.BNB_P0 - 300)
        f = Fetcher(APP, fixture_dir=helpers.FIXTURES, now=helpers.BNB_P0 + 300)
        self.assertEqual(max(f.recent("BNB", "BNBUSDT", "G")), helpers.BNB_P0)
        self.assertEqual(f.requests, 1)

    def test_history_paginates(self):
        app = {"binance": dict(APP["binance"], page_limit=50)}
        f = Fetcher(app, fixture_dir=helpers.FIXTURES, now=helpers.BNB_P0 + 300)
        pts = f.history("BNB", "BNBUSDT", "OI", helpers.BNB_P0 - 86400, helpers.BNB_P0)
        self.assertEqual(len(pts), 289)
        self.assertEqual(f.requests, 6)            # 289 点 / 每页 50

    def test_older_than_30_days_is_rejected(self):
        f = Fetcher(APP, fixture_dir=helpers.FIXTURES, now=helpers.BNB_P0)
        with self.assertRaises(FetchError) as ctx:
            f.history("BNB", "BNBUSDT", "G", helpers.BNB_P0 - 31 * 86400, helpers.BNB_P0)
        self.assertEqual(ctx.exception.code, -1130)

    def test_missing_fixture_is_skip(self):
        f = Fetcher({"binance": {}}, fixture_dir=helpers.FIXTURES, now=helpers.BNB_P0)
        self.assertIsNone(f.recent("XRP", "XRPUSDT", "G"))
        self.assertEqual(f.errors[0]["kind"], "skip")

    def test_funding_from_premium_index(self):
        f = Fetcher(APP, fixture_dir=helpers.FIXTURES, now=helpers.BNB_P0)
        self.assertAlmostEqual(f.funding("BNB", "BNBUSDT")["rate"], 0.000086)


class TestNetworkErrors(unittest.TestCase):
    def _fetch(self, exc):
        f = Fetcher(APP, now=helpers.BNB_P0)
        with mock.patch.object(sources.http, "get_json", side_effect=exc):
            pts = f.recent("BTC", "BTCUSDT", "G")
        self.assertIsNone(pts)
        return f.errors[0]

    def _http(self, code, body=b"{}"):
        return urllib.error.HTTPError("https://x", code, "x", {}, io.BytesIO(body))

    def test_rate_limit_and_network_are_skip(self):
        self.assertEqual(self._fetch(self._http(429))["kind"], "skip")
        self.assertEqual(self._fetch(self._http(418))["kind"], "skip")
        self.assertEqual(self._fetch(urllib.error.URLError("down"))["kind"], "skip")
        self.assertEqual(self._fetch(TimeoutError("slow"))["kind"], "skip")

    def test_bad_request_and_bad_json_are_errors(self):
        err = self._fetch(self._http(400, b'{"code":-1130,"msg":"bad"}'))
        self.assertEqual(err["kind"], "error")
        self.assertIn("-1130", err["error"])
        self.assertEqual(self._fetch(ValueError("not json"))["kind"], "error")


class TestCacheFallback(unittest.TestCase):
    def test_retry_with_29_days_on_1130(self):
        calls = []

        class Fake:
            def history(self, asset, symbol, metric, start, end):
                calls.append(end - start)
                if len(calls) == 1:
                    raise FetchError("error", "startTime invalid", code=-1130)
                return {1: (1.0, 0.5)}

        out = run._fetch_cache_metric(Fake(), "BTC", "BTCUSDT", "G", 10_000_000, APP["cache30d"])
        self.assertEqual(out, {1: (1.0, 0.5)})
        self.assertEqual(calls, [int(29.5 * 86400), 29 * 86400])


if __name__ == "__main__":
    unittest.main(verbosity=2)
