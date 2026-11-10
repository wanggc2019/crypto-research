#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""healthcheck 纯函数单测（不联网、不依赖 launchd）。"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import healthcheck  # noqa: E402

LOG = """===== 2026-11-08 02:52:52 start =====
  binance_ann          OK   new=0    0.20s
采集完成：34 个源，成功 34，失败 0，新增 0 条
评分完成：原始 874 → 匹配 213 → 事件簇 176
今日待推送：0 条（P0/P1 合计 1 条，已过滤已推送）
===== 2026-11-08 02:53:16 done (rc=0) =====
"""

LOG_BAD = """===== 2026-11-08 02:40:00 start =====
采集完成：34 个源，成功 30，失败 4，新增 2 条
===== 2026-11-08 02:40:30 done (rc=1) =====
"""


class TestParseLastRun(unittest.TestCase):
    def test_ok_run(self):
        info = healthcheck.parse_last_run(LOG)
        self.assertTrue(info["found"])
        self.assertEqual(info["rc"], 0)
        self.assertEqual(info["finished"], "2026-11-08 02:53:16")
        self.assertEqual(info["total"], 34)
        self.assertEqual(info["ok"], 34)
        self.assertEqual(info["fail"], 0)
        self.assertIsNotNone(info["age_min"])

    def test_bad_run(self):
        info = healthcheck.parse_last_run(LOG_BAD)
        self.assertTrue(info["found"])
        self.assertEqual(info["rc"], 1)
        self.assertEqual(info["fail"], 4)

    def test_in_progress_uses_last_completed(self):
        log = LOG + "===== 2026-11-08 03:02:52 start =====\n  binance_ann  OK  new=0 0.2s\n"
        info = healthcheck.parse_last_run(log)
        self.assertTrue(info["found"])
        self.assertTrue(info["in_progress"])
        self.assertEqual(info["ok"], 34)  # 取上一个完整轮次的采集数

    def test_no_done(self):
        info = healthcheck.parse_last_run("===== 2026-11-08 02:40:00 start =====\n")
        self.assertFalse(info["found"])


class TestLastRunState(unittest.TestCase):
    def test_rc4_is_expected_lock_skip_not_failure(self):
        result = healthcheck.last_run_state({"found": True, "rc": 4, "finished": "2026-11-08 13:32:21"})
        self.assertEqual(result["state"], healthcheck.WARN)
        self.assertIn("rc=4", result["detail"])
        self.assertIn("防重叠", result["detail"])

    def test_partial_source_failure_is_warn(self):
        result = healthcheck.last_run_state({"found": True, "rc": 1, "finished": "2026-11-08 16:54:45"})
        self.assertEqual(result["state"], healthcheck.WARN)
        self.assertIn("部分源失败", result["detail"])

    def test_real_failure_still_fails(self):
        result = healthcheck.last_run_state({"found": True, "rc": 3, "finished": "2026-11-08 13:32:21"})
        self.assertEqual(result["state"], healthcheck.FAIL)

    def test_stale_run_fails(self):
        result = healthcheck.last_run_state({"found": True, "rc": 0, "age_min": 99})
        self.assertEqual(result["state"], healthcheck.FAIL)


class TestCoreSourcePolicy(unittest.TestCase):
    """只要 Binance/OKX 有报价，Coinbase 等非核心源失败不应告警。"""

    def _run(self, primary_ok, complete=True, missing=None):
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data", "state")
            os.makedirs(data)
            log = os.path.join(tmp, "collect.log")
            with open(log, "w", encoding="utf-8") as f:
                f.write(LOG_BAD.replace("rc=1", "rc=1"))  # 6/9 源失败 rc=1
            with open(os.path.join(data, "last_run.json"), "w", encoding="utf-8") as f:
                json.dump({"primary_ok": primary_ok, "primary_complete": complete,
                           "primary_missing": missing or {}}, f)
            with mock.patch.object(healthcheck, "log_path", return_value=Path(log)), \
                    mock.patch.object(healthcheck, "module_dir", return_value=Path(tmp)):
                return healthcheck.check_last_run("crypto-price")

    def test_partial_primary_degraded_is_warn(self):
        result = self._run(True, complete=False)
        self.assertEqual(result["state"], healthcheck.WARN)
        self.assertIn("主源降级", result["detail"])

    def test_partial_primary_degraded_warn_even_when_rc0(self):
        # 健康检查里"进行中/rc0"不应掩盖主源降级；WARN 不推 Telegram
        with tempfile.TemporaryDirectory() as tmp:
            data = os.path.join(tmp, "data", "state")
            os.makedirs(data)
            log = os.path.join(tmp, "collect.log")
            with open(log, "w", encoding="utf-8") as f:
                f.write(LOG.replace("rc=0", "rc=0"))
            with open(os.path.join(data, "last_run.json"), "w", encoding="utf-8") as f:
                json.dump({"primary_ok": True, "primary_complete": False,
                           "primary_missing": {"BTC": ["coinbase"]}}, f)
            with mock.patch.object(healthcheck, "log_path", return_value=Path(log)), \
                    mock.patch.object(healthcheck, "module_dir", return_value=Path(tmp)):
                result = healthcheck.check_last_run("crypto-price")
        self.assertEqual(result["state"], healthcheck.WARN)
        self.assertIn("主源降级", result["detail"])

    def test_missing_primary_is_fail(self):
        result = self._run(False, complete=False, missing={"BTC": ["binance"]})
        self.assertEqual(result["state"], healthcheck.FAIL)
        self.assertIn("主源报价不足", result["detail"])


class TestSourceSummary(unittest.TestCase):
    def test_source_summary_2_of_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "data", "state")
            os.makedirs(state)
            with open(os.path.join(state, "last_run.json"), "w", encoding="utf-8") as f:
                json.dump({"primary_sources": ["binance", "okx", "coinbase"],
                           "source_health": {"binance": {"ok": True},
                                             "okx": {"ok": True},
                                             "coinbase": {"ok": False}}}, f)
            with mock.patch.object(healthcheck, "module_dir", return_value=Path(tmp)):
                text = healthcheck._source_summary("crypto-price")
        self.assertIn("2/3 binance/okx/coinbase", text)
        self.assertIn("coinbase 获取异常", text)


class TestDuplicateNotifySuppression(unittest.TestCase):
    def test_skip_health_notify_when_data_quality_alert_already_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = os.path.join(tmp, "data", "state")
            os.makedirs(state)
            with open(os.path.join(state, "delivery.json"), "w", encoding="utf-8") as f:
                json.dump({"cooldowns": {"price:data_quality:multi:BTC-ETH-BNB": {"ts": time.time()}}}, f)
            checks = [{"name": "last_run", "state": "fail", "detail": "主源报价不足"}]
            with mock.patch.object(healthcheck, "module_dir", return_value=Path(tmp)):
                self.assertFalse(healthcheck.maybe_notify("crypto-price", checks, []))


class TestHelpers(unittest.TestCase):
    def test_label(self):
        self.assertEqual(healthcheck.label_for("crypto-news"), "com.crypto-research.crypto-news")

    def test_rank(self):
        self.assertLess(healthcheck.RANK[healthcheck.OK], healthcheck.RANK[healthcheck.WARN])
        self.assertLess(healthcheck.RANK[healthcheck.WARN], healthcheck.RANK[healthcheck.FAIL])

    def test_notify_text(self):
        checks = [
            {"name": "launchd", "state": "fail", "detail": "任务未加载"},
            {"name": "disk", "state": "warn", "detail": "data 接近预算"},
            {"name": "lock", "state": "ok", "detail": "无残留锁"},
        ]
        text = healthcheck._notify_text("crypto-news", checks, ["已自动重载"])
        self.assertIn("健康检查", text)
        self.assertIn("任务未加载", text)
        self.assertIn("已自动修复", text)
        self.assertNotIn("无残留锁", text)


class TestAgentTrigger(unittest.TestCase):
    def test_ok_never_runs(self):
        self.assertFalse(healthcheck.should_run_agent(healthcheck.OK, 0, now_ts=10 ** 12))

    def test_cooldown_blocks(self):
        now = 10 ** 12
        self.assertFalse(healthcheck.should_run_agent(healthcheck.FAIL, now - 60, now_ts=now))
        self.assertTrue(healthcheck.should_run_agent(healthcheck.FAIL, now - 7 * 3600, now_ts=now))

    def test_first_time_runs(self):
        self.assertTrue(healthcheck.should_run_agent(healthcheck.FAIL, 0, now_ts=10 ** 12))


class TestAgentBackoff(unittest.TestCase):
    """多级退避：首次立即，之后 15/30/60/120/240/360 分钟（上限）。"""

    def test_schedule_levels(self):
        self.assertEqual(healthcheck.agent_cooldown_min(0), 15)
        self.assertEqual(healthcheck.agent_cooldown_min(1), 15)   # 第 1 次后 15 分钟
        self.assertEqual(healthcheck.agent_cooldown_min(2), 30)   # 第 2 次后 30 分钟
        self.assertEqual(healthcheck.agent_cooldown_min(3), 60)
        self.assertEqual(healthcheck.agent_cooldown_min(4), 120)
        self.assertEqual(healthcheck.agent_cooldown_min(5), 240)
        self.assertEqual(healthcheck.agent_cooldown_min(6), 360)
        self.assertEqual(healthcheck.agent_cooldown_min(99), 360)

    def test_cap_can_shrink(self):
        self.assertEqual(healthcheck.agent_cooldown_min(3, cap=10), 10)
        self.assertEqual(healthcheck.agent_cooldown_min(0, cap=0), 15)

    def test_attempts_gate_retry(self):
        now = 10 ** 12
        last = now - 20 * 60
        # 已调用 0/1 次 → 冷却 15 分钟：20 分钟前调用过，可以再调
        self.assertTrue(healthcheck.should_run_agent(healthcheck.FAIL, last, now_ts=now, attempts=0))
        self.assertTrue(healthcheck.should_run_agent(healthcheck.FAIL, last, now_ts=now, attempts=1))
        # 已调用 2 次 → 冷却 30 分钟：20 分钟前调用过，仍要等
        self.assertFalse(healthcheck.should_run_agent(healthcheck.FAIL, last, now_ts=now, attempts=2))
        # 已调用 5 次 → 冷却 240 分钟
        self.assertFalse(healthcheck.should_run_agent(healthcheck.FAIL, last, now_ts=now, attempts=5))
        self.assertTrue(healthcheck.should_run_agent(
            healthcheck.FAIL, now - 241 * 60, now_ts=now, attempts=5))


class TestStatePersistence(unittest.TestCase):
    def test_merge_preserves_old_keys(self):
        old = {"last_agent_ts": 1, "last_notify_hash": "abc", "agent_attempts": 2}
        merged = healthcheck.merge_state(old, {"state": "fail"})
        self.assertEqual(merged["last_agent_ts"], 1)
        self.assertEqual(merged["last_notify_hash"], "abc")
        self.assertEqual(merged["agent_attempts"], 2)
        self.assertEqual(merged["state"], "fail")

    def test_bad_state_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "health.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertEqual(healthcheck.load_state(path), {})

    def test_digest_is_order_independent(self):
        checks = [{"name": "lock", "state": "fail", "detail": "锁残留"},
                  {"name": "disk", "state": "fail", "detail": "超预算"}]
        self.assertEqual(healthcheck.notify_digest(checks),
                         healthcheck.notify_digest(list(reversed(checks))))

    def test_digest_is_stable_across_processes(self):
        # 锁死契约：sha256 稳定值（旧实现用内置 hash()，跨进程随机 → 去重失效）
        checks = [{"name": "lock", "state": "fail", "detail": "锁残留"}]
        self.assertEqual(healthcheck.notify_digest(checks), "a62dea0d2c69a043")

    def test_digest_differs_when_detail_changes(self):
        one = [{"name": "lock", "state": "fail", "detail": "锁残留"}]
        two = [{"name": "lock", "state": "fail", "detail": "锁残留 40 分钟"}]
        self.assertNotEqual(healthcheck.notify_digest(one), healthcheck.notify_digest(two))


class TestHealthStateWritten(unittest.TestCase):
    """main() 写 health.json 时必须保留冷却/去重状态（本轮修复的核心 bug）。"""

    def _run_main(self, last_run_state, seed):
        tmp = tempfile.mkdtemp(prefix="hc-state-")
        self.addCleanup(shutil.rmtree, tmp, True)
        state_dir = Path(tmp) / "data" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "health.json").write_text(json.dumps(seed), encoding="utf-8")
        with mock.patch.object(healthcheck, "module_dir", return_value=Path(tmp)), \
                mock.patch.object(healthcheck, "check_launchd",
                                  return_value={"name": "launchd", "state": "ok", "detail": "已加载"}), \
                mock.patch.object(healthcheck, "check_last_run", return_value=last_run_state), \
                mock.patch.object(healthcheck, "check_lock",
                                  return_value={"name": "lock", "state": "ok", "detail": "无残留锁"}), \
                mock.patch.object(healthcheck, "check_disk",
                                  return_value={"name": "disk", "state": "ok", "detail": "data 正常"}), \
                mock.patch.object(healthcheck, "check_creds",
                                  return_value={"name": "creds", "state": "ok", "detail": "凭据正常"}):
            rc = healthcheck.main(["--module", "crypto-news", "--json"])
        data = json.loads((state_dir / "health.json").read_text(encoding="utf-8"))
        return rc, data

    def test_fail_keeps_agent_cooldown(self):
        seed = {"last_agent_ts": 123.0, "last_notify_ts": 456.0,
                "last_notify_hash": "abc", "agent_attempts": 2}
        rc, data = self._run_main(
            {"name": "last_run", "state": "fail", "detail": "最近一轮 rc=3"}, seed)
        self.assertEqual(rc, 2)
        self.assertEqual(data["last_agent_ts"], 123.0)
        self.assertEqual(data["last_notify_hash"], "abc")
        self.assertEqual(data["agent_attempts"], 2)

    def test_ok_resets_attempts(self):
        seed = {"last_agent_ts": 123.0, "agent_attempts": 2}
        rc, data = self._run_main(
            {"name": "last_run", "state": "ok", "detail": "正常"}, seed)
        self.assertEqual(rc, 0)
        self.assertEqual(data["agent_attempts"], 0)
        self.assertEqual(data["last_agent_ts"], 123.0)

    def test_agent_spawn_increments_attempts(self):
        seed = {"last_agent_ts": 0, "agent_attempts": 2}
        tmp = tempfile.mkdtemp(prefix="hc-agent-")
        self.addCleanup(shutil.rmtree, tmp, True)
        state_dir = Path(tmp) / "data" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "health.json").write_text(json.dumps(seed), encoding="utf-8")
        with mock.patch.object(healthcheck, "module_dir", return_value=Path(tmp)), \
                mock.patch.object(healthcheck, "check_launchd",
                                  return_value={"name": "launchd", "state": "ok", "detail": "已加载"}), \
                mock.patch.object(healthcheck, "check_last_run",
                                  return_value={"name": "last_run", "state": "fail", "detail": "rc=3"}), \
                mock.patch.object(healthcheck, "check_lock",
                                  return_value={"name": "lock", "state": "ok", "detail": "ok"}), \
                mock.patch.object(healthcheck, "check_disk",
                                  return_value={"name": "disk", "state": "ok", "detail": "ok"}), \
                mock.patch.object(healthcheck, "check_creds",
                                  return_value={"name": "creds", "state": "ok", "detail": "ok"}), \
                mock.patch.object(healthcheck, "run_agent",
                                  return_value={"ran": True, "async": True}):
            rc = healthcheck.main(["--module", "crypto-news", "--json", "--agent"])
        data = json.loads((state_dir / "health.json").read_text(encoding="utf-8"))
        self.assertEqual(rc, 2)
        self.assertEqual(data["agent_attempts"], 3)
        self.assertGreater(data["last_agent_ts"], 0)


class TestNotifyDedupePersisted(unittest.TestCase):
    """同一问题 60 分钟内只发一次：去重指纹要落盘且跨进程稳定。"""

    def test_second_round_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / "data" / "state"
            state_dir.mkdir(parents=True)
            checks = [{"name": "last_run", "state": "fail", "detail": "rc=3 网络全挂"}]
            env = {"TELEGRAM_BOT_TOKEN": "123456:" + "A" * 35,
                   "TELEGRAM_CHAT_ID": "123456"}
            sent = []
            with mock.patch.object(healthcheck, "module_dir", return_value=Path(tmp)), \
                    mock.patch.object(healthcheck.envlib, "load_env", return_value=env), \
                    mock.patch.object(healthcheck, "send_message",
                                      side_effect=lambda *a, **k: (sent.append(1), (True, ""))[1]):
                first = healthcheck.maybe_notify("crypto-news", checks, [])
                second = healthcheck.maybe_notify("crypto-news", checks, [])
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(len(sent), 1)
            data = json.loads((state_dir / "health.json").read_text(encoding="utf-8"))
            self.assertEqual(data["last_notify_hash"], healthcheck.notify_digest(checks))


class TestAsyncAgent(unittest.TestCase):
    def test_dry_run_uses_async_runner(self):
        # 冷却占用时 dry-run 不会打印；这里用 last_ts=0 保证可触发
        result = healthcheck.run_agent("crypto-news", healthcheck.FAIL, 0, dry_run=True)
        self.assertEqual(result["reason"], "dry-run")
        self.assertIn("agent_repair.py", " ".join(result["cmd"]))

    def test_cooldown_blocks(self):
        now = 10 ** 12
        result = healthcheck.run_agent("crypto-news", healthcheck.FAIL, now - 60, dry_run=True,
                                       cooldown_min=360)
        self.assertFalse(result["ran"])
        self.assertIn("冷却", result["reason"])


class TestRepairText(unittest.TestCase):
    def test_recovered(self):
        before = [{"name": "launchd", "state": "fail", "detail": "任务未加载"}]
        after = [{"name": "launchd", "state": "ok", "detail": "已加载"}]
        diag = {"causes": [{"cause": "launchd 任务缺失/未加载", "hint": "重装"}]}
        text = healthcheck._repair_text("crypto-news", before, diag, {"rc": 0}, after)
        self.assertIn("自动修复报告", text)
        self.assertIn("已恢复正常", text)
        self.assertIn("launchd 任务缺失", text)

    def test_still_failing(self):
        before = [{"name": "last_run", "state": "fail", "detail": "rc=1"}]
        after = [{"name": "last_run", "state": "fail", "detail": "rc=1"}]
        text = healthcheck._repair_text("crypto-news", before, {"causes": []}, {"rc": 1}, after)
        self.assertIn("仍异常", text)
        self.assertIn("需人工", text)

    def test_timeout_message(self):
        before = [{"name": "last_run", "state": "fail", "detail": "rc=1"}]
        after = [{"name": "last_run", "state": "fail", "detail": "rc=1"}]
        text = healthcheck._repair_text("crypto-news", before, {"causes": []}, {"rc": "timeout"}, after)
        self.assertIn("超时", text)
        self.assertIn("仍异常", text)


class TestDiagnosis(unittest.TestCase):
    def _checks(self, detail, state="fail"):
        return [{"name": "last_run", "state": state, "detail": detail}]

    def test_credential_failure(self):
        diag = healthcheck.diagnose("crypto-news", None, self._checks("最近一轮 rc=2（2026-11-08 02:00:00）"), errors=[])
        causes = [c["cause"] for c in diag["causes"]]
        self.assertTrue(any("凭据" in c for c in causes))

    def test_launchd_missing(self):
        diag = healthcheck.diagnose("crypto-news", None, self._checks("任务未加载（可用 --fix 自动重载）"), errors=[])
        causes = [c["cause"] for c in diag["causes"]]
        self.assertTrue(any("launchd" in c for c in causes))

    def test_unknown_gets_hint(self):
        diag = healthcheck.diagnose("crypto-news", None, self._checks("某个说不清的问题"), errors=[])
        self.assertEqual(diag["causes"][0]["cause"], "未知异常，需要看日志")

    def test_log_error_lines_mapped(self):
        diag = healthcheck.diagnose(
            "crypto-news", None, self._checks("未知问题", state="warn"),
            errors=["Traceback (most recent call last):", "ModuleNotFoundError: No module named 'yaml'"],
        )
        causes = [c["cause"] for c in diag["causes"]]
        self.assertTrue(any("依赖" in c for c in causes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
