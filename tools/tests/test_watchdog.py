#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watchdog.py 纯函数单测（离线，不调 launchd/网络）。"""
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import watchdog  # noqa: E402


class TestDiscovery(unittest.TestCase):
    def test_only_crypto_modules_with_scripts_or_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("crypto-news", "crypto-price", "other"):
                os.makedirs(os.path.join(tmp, name, "scripts"))
            os.makedirs(os.path.join(tmp, "crypto-empty"))          # 无 scripts/tests → 不算
            os.makedirs(os.path.join(tmp, "crypto-tests-only", "tests"))
            found = watchdog.discover_modules(tmp)
        self.assertEqual(found, ["crypto-news", "crypto-price", "crypto-tests-only"])

    def test_module_supports_healthcheck_needs_collect_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "crypto-a", "data", "logs"))
            self.assertFalse(watchdog.module_supports_healthcheck(tmp, "crypto-a"))
            open(os.path.join(tmp, "crypto-a", "data", "logs", "collect.log"), "w").close()
            self.assertTrue(watchdog.module_supports_healthcheck(tmp, "crypto-a"))


class TestLocks(unittest.TestCase):
    def test_missing_lock(self):
        self.assertIsNone(watchdog.lock_age_min("/nonexistent/collect.lock"))

    def test_age_and_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = os.path.join(tmp, "collect.lock")
            os.makedirs(lock)
            self.assertLess(watchdog.lock_age_min(lock), 1)
            self.assertTrue(watchdog.should_skip_running(watchdog.lock_age_min(lock)))
            old = time.time() - 31 * 60
            os.utime(lock, (old, old))
            self.assertGreater(watchdog.lock_age_min(lock), 30)
            self.assertFalse(watchdog.should_skip_running(watchdog.lock_age_min(lock)))


class TestParseOutput(unittest.TestCase):
    def test_plain_json(self):
        payload = watchdog.parse_json_output('{"module": "crypto-news", "state": "ok"}')
        self.assertEqual(payload["state"], "ok")

    def test_trailing_ai_lines(self):
        text = ('{"module": "crypto-news", "state": "fail", "checks": []}\n'
                "  AI 深度修复：已异步触发（结束后会发修复报告）\n")
        self.assertEqual(watchdog.parse_json_output(text)["state"], "fail")

    def test_garbage(self):
        self.assertEqual(watchdog.parse_json_output("no json here"), {})
        self.assertEqual(watchdog.parse_json_output(""), {})


class TestDigestAndMessage(unittest.TestCase):
    def _states(self):
        return {
            "crypto-news": {"state": "fail", "rc": 2, "fixed": ["发现过期锁，已删除"],
                            "causes": ["锁/并发问题"], "ai": True},
            "crypto-price": {"state": "ok", "rc": 0},
        }

    def test_digest_stable_and_order_independent(self):
        a = watchdog.digest_of(self._states(), [])
        b = watchdog.digest_of(dict(reversed(list(self._states().items()))), [])
        self.assertEqual(a, b)
        changed = self._states()
        changed["crypto-price"]["state"] = "fail"
        self.assertNotEqual(a, watchdog.digest_of(changed, []))

    def test_message_lists_failures_and_actions(self):
        jobs = [{"module": "crypto-price", "label": "x", "status": "🔴 失败(rc=1)",
                 "last_exit_code": 1, "last_run_ago": "3 分钟前"}]
        text = watchdog.build_message(self._states(), jobs)
        self.assertIn("看门狗发现 2 个问题", text)
        self.assertIn("crypto-news", text)
        self.assertIn("已自愈：发现过期锁", text)
        self.assertIn("原因推断：锁/并发问题", text)
        self.assertIn("AI 深度分析：已触发", text)
        self.assertIn("launchd", text)
        self.assertIn(watchdog.LABEL, text)

    def test_digest_ignores_ok_and_running(self):
        fail = {"crypto-policy": {"state": "fail", "rc": 2}}
        a = dict(fail, **{"crypto-x-kol": {"state": "running", "skipped": True},
                          "crypto-news": {"state": "ok"}})
        b = dict(fail, **{"crypto-onchain": {"state": "running", "skipped": True},
                          "crypto-price": {"state": "ok"}})
        self.assertEqual(watchdog.digest_of(a, []), watchdog.digest_of(b, []))

    def test_message_empty_when_all_ok(self):
        states = {"crypto-news": {"state": "ok", "rc": 0}}
        self.assertEqual(watchdog.build_message(states, []), "")

    def test_message_skips_running_modules(self):
        states = {"crypto-news": {"state": "running", "rc": 0, "skipped": True}}
        self.assertEqual(watchdog.build_message(states, []), "")


class TestStateRoundTrip(unittest.TestCase):
    def test_write_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_state, old_path = watchdog.STATE_PATH, watchdog.STATE_DIR
            try:
                watchdog.STATE_DIR = Path(tmp)
                watchdog.STATE_PATH = Path(tmp) / "watchdog.json"
                watchdog.write_state({"ts": "x", "modules": {}})
                self.assertEqual(watchdog.load_state()["ts"], "x")
                watchdog.STATE_PATH = Path(tmp) / "missing.json"
                self.assertEqual(watchdog.load_state(), {})
            finally:
                watchdog.STATE_PATH, watchdog.STATE_DIR = old_state, old_path


class TestDailySchedules(unittest.TestCase):
    def test_network_due(self):
        now = 10 ** 12
        self.assertTrue(watchdog.should_run_network(0, now_ts=now))
        self.assertFalse(watchdog.should_run_network(now - 2 * 3600, now_ts=now))
        self.assertTrue(watchdog.should_run_network(now - 25 * 3600, now_ts=now))
        self.assertFalse(watchdog.should_run_network(0, now_ts=now, every_hours=0))

    def test_warn_digest_date(self):
        self.assertIsNone(watchdog.warn_digest_date(
            datetime(2026, 11, 9, 8, 0).timestamp(), hour=9))
        self.assertEqual(watchdog.warn_digest_date(
            datetime(2026, 11, 9, 10, 0).timestamp(), hour=9), "2026-11-09")
        self.assertEqual(watchdog.warn_digest_date(
            datetime(2026, 11, 9, 1, 0).timestamp(), hour=0), "2026-11-09")

    def test_should_send_warn_digest_once_per_day(self):
        at = datetime(2026, 11, 9, 10, 0).timestamp()
        self.assertTrue(watchdog.should_send_warn_digest("2026-11-08", now_ts=at, hour=9))
        self.assertFalse(watchdog.should_send_warn_digest("2026-11-09", now_ts=at, hour=9))
        before = datetime(2026, 11, 9, 8, 0).timestamp()
        self.assertFalse(watchdog.should_send_warn_digest("2026-11-08", now_ts=before, hour=9))

    def test_build_warn_digest_includes_launchd_warn(self):
        jobs = [{"module": "crypto-policy", "status": "🟠 降级(rc=1 部分源失败)",
                 "last_exit_code": 1, "level": "warn"}]
        text = watchdog.build_warn_digest({}, jobs,
                                          now=datetime(2026, 11, 9, 10, 0).timestamp())
        self.assertIn("launchd crypto-policy", text)
        self.assertIn("rc=1", text)

    def test_build_warn_digest(self):
        states = {
            "crypto-price": {"state": "warn",
                             "warn_details": ["last_run：部分主源降级", "disk：接近预算"]},
            "crypto-news": {"state": "ok"},
            "crypto-x-kol": {"state": "running", "skipped": True},
        }
        text = watchdog.build_warn_digest(states, now=datetime(2026, 11, 9, 10, 0).timestamp())
        self.assertIn("每日降级汇总 2026-11-09（1 项）", text)
        self.assertIn("crypto-price", text)
        self.assertIn("部分主源降级", text)
        self.assertNotIn("crypto-news", text)
        self.assertEqual(watchdog.build_warn_digest({"crypto-news": {"state": "ok"}}), "")


class TestSleepRecovery(unittest.TestCase):
    def test_parse_iso_ts(self):
        self.assertGreater(watchdog.parse_iso_ts("2026-11-09T16:00:00"), 0)
        self.assertEqual(watchdog.parse_iso_ts("bad"), 0)
        self.assertEqual(watchdog.parse_iso_ts(None), 0)

    def test_looks_like_sleep(self):
        now = 10 ** 12
        self.assertFalse(watchdog.looks_like_sleep(0, now_ts=now))
        self.assertFalse(watchdog.looks_like_sleep(now - 200, now_ts=now))   # 3 分钟前 → 正常
        self.assertTrue(watchdog.looks_like_sleep(now - 1000, now_ts=now))  # >15 分钟 → 休眠
        self.assertTrue(watchdog.looks_like_sleep(now - 1000, now_ts=now,
                                                   interval=60, factor=2))  # 阈值下限 600s



NET = {"state": "fail", "rc": 2, "causes": ["网络不可用/被限流"]}
OTHER = {"state": "fail", "rc": 2, "causes": ["配置错误"]}


class TestNetworkRerun(unittest.TestCase):
    """2026-11-17 16:03/16:08：断网恢复后才发出的过时告警 → 先重跑，下一轮仍失败才告警。"""

    def test_network_failed_modules(self):
        states = {"crypto-policy": NET, "crypto-news": OTHER, "crypto-price": {"state": "ok"},
                  "crypto-x-kol": {"state": "warn", "causes": ["网络不可用/被限流"]}}
        self.assertEqual(watchdog.network_failed_modules(states), ["crypto-policy"])

    def test_plan_reruns_once_then_alerts(self):
        to_rerun, retries = watchdog.plan_network_reruns({"crypto-policy": NET}, {})
        self.assertEqual(to_rerun, ["crypto-policy"])
        # 已重跑过、下一轮仍失败 → 不再重跑（照常告警）
        to_rerun, retries = watchdog.plan_network_reruns({"crypto-policy": NET}, {"crypto-policy": 1.0})
        self.assertEqual((to_rerun, retries), ([], {"crypto-policy": 1.0}))
        # 正在跑（可能就是重跑那一轮）→ 保留记录
        _, retries = watchdog.plan_network_reruns({"crypto-policy": {"state": "running"}}, {"crypto-policy": 1.0})
        self.assertIn("crypto-policy", retries)
        # 恢复 → 清记录，下次再断网可以再重跑
        _, retries = watchdog.plan_network_reruns({"crypto-policy": {"state": "ok"}}, {"crypto-policy": 1.0})
        self.assertEqual(retries, {})
        # 坏 state 容错
        self.assertEqual(watchdog.plan_network_reruns({}, {"x": "bad"})[1], {})

    def test_without_modules_drops_module_and_its_launchd_job(self):
        states = {"crypto-policy": NET, "crypto-news": OTHER}
        jobs = [{"module": "crypto-policy", "status": "失败"},
                {"label": "com.crypto-research.crypto-policy", "status": "失败"},
                {"module": "crypto-news", "status": "失败"}]
        s, j = watchdog.without_modules(states, jobs, ["crypto-policy"])
        self.assertEqual(list(s), ["crypto-news"])
        self.assertEqual(j, [{"module": "crypto-news", "status": "失败"}])

    def run_reruns(self, states, old, net=True, kick=True, muted=""):
        with mock.patch.object(watchdog, "network_ok", return_value=net) as probe, \
             mock.patch.object(watchdog, "kickstart", return_value=kick) as ks, \
             mock.patch.object(watchdog, "log"):
            out = watchdog._network_reruns(old, states, [{"module": "crypto-policy", "status": "失败"}], muted)
        return out, probe, ks

    def test_rerun_suppresses_alert_when_network_back(self):
        old = {}
        (states, jobs, rerun), _, ks = self.run_reruns({"crypto-policy": NET, "crypto-news": OTHER}, old)
        ks.assert_called_once_with("crypto-policy")
        self.assertEqual(rerun, ["crypto-policy"])
        self.assertEqual(list(states), ["crypto-news"], "非网络类故障照常告警")
        self.assertEqual(jobs, [])
        self.assertIn("crypto-policy", old["network_retries"])
        self.assertTrue(watchdog.build_message(states, jobs), "其余故障的告警不受影响")

    def test_second_round_still_failing_alerts(self):
        old = {"network_retries": {"crypto-policy": time.time() - 300}}
        (states, jobs, rerun), probe, ks = self.run_reruns({"crypto-policy": NET}, old)
        self.assertEqual(rerun, [])
        ks.assert_not_called()
        probe.assert_not_called()
        self.assertIn("crypto-policy", states)
        self.assertIn("crypto-policy", watchdog.build_message(states, jobs))

    def test_no_rerun_when_network_still_down_or_kickstart_fails_or_manual(self):
        for kwargs in ({"net": False}, {"kick": False}, {"muted": "--no-notify"}):
            old = {}
            (states, jobs, rerun), _, _ = self.run_reruns({"crypto-policy": NET}, old, **kwargs)
            self.assertEqual(rerun, [], kwargs)
            self.assertIn("crypto-policy", states, kwargs)
            self.assertEqual(old["network_retries"], {}, kwargs)

    def test_network_ok_treats_http_error_as_reachable(self):
        import urllib.error
        err = urllib.error.HTTPError(watchdog.NETWORK_PROBE_URL, 404, "nf", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertTrue(watchdog.network_ok())
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("dns")):
            self.assertFalse(watchdog.network_ok())


if __name__ == "__main__":
    unittest.main(verbosity=2)
