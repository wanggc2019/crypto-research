# -*- coding: utf-8 -*-
"""crypto_core.ratelimit：共享滑动窗口（假时钟，不真等）+ http 按主机挂钩。"""
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from crypto_core import http, ratelimit


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()

    def tearDown(self):
        self.tmp.cleanup()

    def _acq(self, rpm, max_wait=90):
        return ratelimit.acquire("t", rpm, max_wait=max_wait, directory=self.tmp.name,
                                 clock=self.clock, sleep=self.clock.sleep)

    def test_within_budget_no_wait(self):
        for _ in range(3):
            self.assertEqual(self._acq(3), 0)
        self.assertEqual(self.clock.slept, [])

    def test_waits_for_oldest_to_expire(self):
        self._acq(2)
        self.clock.t += 10
        self._acq(2)
        waited = self._acq(2)          # 第 3 个：等第 1 个过期（还差 50s）
        self.assertAlmostEqual(waited, 50, places=3)
        with open(os.path.join(self.tmp.name, "ratelimit-t.json")) as f:
            self.assertEqual(len(json.load(f)), 2, "过期时间戳会被清理")

    def test_over_max_wait_raises_with_skip_marker(self):
        self._acq(1)
        with self.assertRaises(ratelimit.RateLimited) as ctx:
            self._acq(1, max_wait=5)
        self.assertIn("Too Many Requests", str(ctx.exception))
        self.assertIsInstance(ctx.exception, OSError)

    def test_clock_rewind_keeps_window(self):
        self._acq(1)
        self.clock.t -= 30                 # 时钟回拨 30s：已有的时间戳在「未来」
        with self.assertRaises(ratelimit.RateLimited):
            self._acq(1, max_wait=5)       # 仍计入窗口，不会被清空后超发

    def test_corrupt_state_recovers(self):
        with open(os.path.join(self.tmp.name, "ratelimit-t.json"), "w") as f:
            f.write("not json")
        self.assertEqual(self._acq(1), 0)

    def test_shared_across_threads(self):
        """多个并发调用者共用一个窗口：总放行数不超过配额。"""
        got, errors = [], []

        def worker():
            try:
                ratelimit.acquire("p", 5, max_wait=0, directory=self.tmp.name)
                got.append(1)
            except ratelimit.RateLimited:
                errors.append(1)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(got), 5)
        self.assertEqual(len(errors), 7)


class HttpHookTest(unittest.TestCase):
    def test_jina_throttled_with_anon_and_keyed_rpm(self):
        calls = []
        with mock.patch.object(http.ratelimit, "acquire", side_effect=lambda n, r: calls.append((n, r))), \
                mock.patch.object(http._OPENER, "open", side_effect=OSError("offline")), \
                mock.patch.dict(os.environ, {"CRYPTO_RATELIMIT": "1", "JINA_RPM": "", "JINA_RPM_KEYED": ""}):
            with self.assertRaises(OSError):
                http.get("https://r.jina.ai/https://x.com/a", retries=1, backoff=0)
            with self.assertRaises(OSError):
                http.get("https://r.jina.ai/x", headers={"Authorization": "Bearer k"})
            with self.assertRaises(OSError):
                http.get("https://api.binance.com/x")
        self.assertEqual(calls, [("jina", 18), ("jina", 18), ("jina", 90)], "重试也占配额；其它主机不限速")

    def test_bad_rpm_env_falls_back(self):
        calls = []
        with mock.patch.object(http.ratelimit, "acquire", side_effect=lambda n, r: calls.append(r)), \
                mock.patch.object(http._OPENER, "open", side_effect=OSError("offline")), \
                mock.patch.dict(os.environ, {"CRYPTO_RATELIMIT": "1", "JINA_RPM": "abc"}):
            with self.assertRaises(OSError):
                http.get("https://r.jina.ai/x")
        self.assertEqual(calls, [18])

    def test_rate_limited_not_retried(self):
        with mock.patch.object(http.ratelimit, "acquire", side_effect=ratelimit.RateLimited("Too Many Requests")), \
                mock.patch.object(http._OPENER, "open") as opener, \
                mock.patch.dict(os.environ, {"CRYPTO_RATELIMIT": "1"}):
            with self.assertRaises(ratelimit.RateLimited):
                http.get("https://r.jina.ai/x", retries=3, backoff=0)
        opener.assert_not_called()

    def test_disabled(self):
        with mock.patch.object(http.ratelimit, "acquire") as acq, \
                mock.patch.object(http._OPENER, "open", side_effect=OSError("offline")), \
                mock.patch.dict(os.environ, {"CRYPTO_RATELIMIT": "0"}):
            with self.assertRaises(OSError):
                http.get("https://r.jina.ai/x")
        acq.assert_not_called()


if __name__ == "__main__":
    unittest.main()
