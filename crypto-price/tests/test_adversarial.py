#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_adversarial.py —— 独立红队（adversarial）验证用例。

来源：task-3「crypto-price 独立红队验证」。本文件只新增测试，不改业务代码；
全部用例离线（不联网）、只写 tempfile 目录、不推送 Telegram。

约定：
- 普通用例 = 已实测通过、值得固化的对抗性回归（脏数据、去重、冷却、降级、锁、卡片转义）。
- 带 ``@unittest.expectedFailure`` 的用例 = **已确认 bug 的复现**，断言里写的是
  「期望行为」。修复对应 bug 后请删除装饰器，让它变成常规回归测试；
  未修复前 ``make test`` 仍然全绿（unittest 只把 expectedFailure 记为预期失败）。

已确认 bug（按严重度）见各 docstring 的 BUG-x 编号；复现命令在用例里。
"""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import cards  # noqa: E402
import delivery  # noqa: E402
import indicators  # noqa: E402
import report  # noqa: E402
import rules  # noqa: E402
import run  # noqa: E402

import common  # noqa: E402
from crypto_core import notify as notify_mod  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
DAY_NOW = datetime.datetime(2026, 11, 8, 12, 0, 0)
QUIET_NOW = datetime.datetime(2026, 11, 8, 23, 0, 0)
FAKE_ENV = {"TELEGRAM_BOT_TOKEN": "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "TELEGRAM_CHAT_ID": "123"}


def load_snapshot(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return json.load(f)


def make_event(**overrides):
    event = {
        "rule": "fast_move", "asset": "BTC", "direction": "down", "severity": "P1",
        "title": "BTC 5 分钟急跌 2.00%，现价 75,412", "detail": ["5m -2.00%"],
        "metrics": {"window": "m5", "change_pct": -2.0, "price": 75412.0,
                    "spread_bps": 3.1, "n_sources": 3},
        "dedupe_key": "price:BTC:fast_move:down", "magnitude": 2.0,
        "urls": ["https://www.binance.com/zh-CN/trade/BTC_USDT?type=spot"],
        "why": "急跌通常伴随杠杆清算", "action": "检查杠杆仓位与止损",
        "force_send": False, "ts": "2026-11-08T12:00:00",
        "evidence": [{"source": "binance", "price": 75412.0}],
    }
    event.update(overrides)
    return event


class BaseCase(unittest.TestCase):
    """临时 data 目录 + 屏蔽真实日志写入（--data-dir 不重定向 common.log_line）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = self._tmp.name
        os.makedirs(os.path.join(self.data, "state"), exist_ok=True)
        self._patch(common, "log_line", return_value="")

    def tearDown(self):
        self._tmp.cleanup()

    def _patch(self, target, attr, **kwargs):
        patcher = mock.patch.object(target, attr, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher

    def state_path(self, name):
        return os.path.join(self.data, "state", name)

    def write_state(self, name, content):
        path = self.state_path(name)
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(content, str):
                f.write(content)
            else:
                json.dump(content, f)
        return path

    def deliver(self, events, now=DAY_NOW, send_fn=None, cfg=None, env=FAKE_ENV):
        return delivery.deliver(
            events, cfg or common.load_all_configs()["rules"], self.state_path(""),
            env=env, now=now, dry_run=False, send_fn=send_fn or FakeSender(),
        )

    def run_main(self, args, now=DAY_NOW, env=None):
        self._patch(common, "now", return_value=now)
        if env is not None:
            self._patch(common, "load_env", return_value=env)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def enable_fake_telegram(self):
        """把两层 Telegram 出口都换成假实现（delivery 是 import 绑定，report 是调用时 import）。"""
        self.sent_messages = []
        fake_send = self._fake_send

        def fake_creds(env):
            return (FAKE_ENV["TELEGRAM_BOT_TOKEN"], FAKE_ENV["TELEGRAM_CHAT_ID"], [])

        self._patch(delivery, "send_message", new=fake_send)
        self._patch(delivery, "check_credentials", new=fake_creds)
        self._patch(notify_mod, "send_message", new=fake_send)
        self._patch(notify_mod, "check_credentials", new=fake_creds)

    def _fake_send(self, token, chat_id, text, silent=False, disable_preview=False):
        self.sent_messages.append(text)
        return True, ""


class FakeSender:
    def __init__(self, fail_times=0):
        self.calls = []
        self.fail_times = fail_times

    def __call__(self, token, chat_id, text, silent=False, disable_preview=False):
        self.calls.append(text)
        if self.fail_times > 0:
            self.fail_times -= 1
            return False, "network down"
        return True, ""


# ---------------------------------------------------------------- 已通过：卡片加固
class TestCardHardening(BaseCase):
    def test_escapes_html_and_caps_length(self):
        alert = delivery.event_to_alert({
            **make_event(),
            "title": '<script>alert("x")</script> & BTC',
            "detail": ['<img src=x onerror=alert(1)>', "a & b < c > d"],
            "urls": ['https://example.com/?a=1&b="><script>'],
            "evidence": [{"source": "binance", "price": 1.0},
                         {"source": "<b>evil</b>", "price": 2.0}],
            "summary": {"fact": "t", "why": "<b>why</b>", "action": "a & b"},
        })
        text = cards.format_price_alert(alert)
        self.assertNotIn("<script>", text)
        self.assertNotIn("<img", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("&amp;", text)
        self.assertNotIn('"><script>', text)
        self.assertLessEqual(len(text), 4096)

    def test_length_capped_with_huge_detail(self):
        alert = delivery.event_to_alert({
            **make_event(),
            "detail": ["x" * 2000 for _ in range(6)],
        })
        text = cards.format_price_alert(alert)
        self.assertLessEqual(len(text), 4096)
        self.assertTrue(text.endswith("…"))


# ---------------------------------------------------------------- 已通过：脏行情/脏快照
class TestDirtyInputs(BaseCase):
    def test_dirty_prices_blocked_without_crash(self):
        cfg = common.load_all_configs()
        for bad in (0, -1.0, None, "abc"):
            with self.subTest(price=bad):
                snapshot = load_snapshot("snapshot_shock.json")
                snapshot["quotes"]["BTC"]["price"] = bad
                events = rules.evaluate(snapshot, {}, cfg["rules"], cfg["watchlist"], now=DAY_NOW)
                kinds = [(e.get("rule"), e.get("asset")) for e in events]
                dq_assets = [str(e.get("asset") or "") for e in events if e.get("rule") == "data_quality"]
                self.assertTrue(any("BTC" in a for a in dq_assets), dq_assets)
                self.assertNotIn(("fast_move", "BTC"), kinds)
                # 只做 state 推进也不应抛异常（脏价格被 _positive 过滤）
                rules.advance_state({}, snapshot, now=DAY_NOW)

    def test_candles_future_unsorted_duplicates_ignored(self):
        clean = load_snapshot("snapshot_normal.json")["candles"]["BTC"]["rows"]
        windows = {"m1": 60, "m5": 300, "m15": 900, "h1": 3600}
        now_ts = 1794081600
        base = indicators.window_moves(clean, 75800.0, now_ts, windows)
        dirty = [dict(row) for row in clean]
        dirty.reverse()
        dirty.append({"close_time": 9999999999, "close": 1.0, "volume": 1.0})
        dirty.extend(clean[-5:])  # 重复 K 线
        self.assertEqual(indicators.window_moves(dirty, 75800.0, now_ts, windows), base)

    def test_inverted_stats_fall_back_to_price(self):
        snapshot = {"quotes": {"BTC": {"price": 100.0}},
                    "stats": {"BTC": {"high": 1.0, "low": 200.0}}}
        state = rules.advance_state({}, snapshot, now=DAY_NOW)
        self.assertEqual(state["extremes"]["BTC"]["high"], 100.0)
        self.assertEqual(state["extremes"]["BTC"]["low"], 100.0)
        # 上一轮极值在缺 stats 时保留
        prev = {"extremes": {"BTC": {"high": 200.0, "low": 50.0, "ts": 1}}}
        state2 = rules.advance_state(prev, {"quotes": {"BTC": {"price": 100.0}}}, now=DAY_NOW)
        self.assertEqual(state2["extremes"]["BTC"]["high"], 200.0)
        self.assertEqual(state2["extremes"]["BTC"]["low"], 50.0)


# ---------------------------------------------------------------- 已通过：损坏文件（非法 JSON）容错
class TestRobustState(BaseCase):
    def test_invalid_json_state_files_tolerated(self):
        self.write_state("delivery.json", "{not json")
        self.write_state("market_state.json", "{not json")
        self.write_state("report.json", "{not json")
        self.write_state("alerts-2026-11-08.json", "{not json")
        state = delivery.load_state(self.state_path(""), DAY_NOW)
        self.assertEqual(state["cooldowns"], {})
        self.assertEqual(state["counts"], {"P0": 0, "P1": 0, "P2": 0})
        self.assertEqual(common.safe_json_load(self.state_path("market_state.json"), {}), {})
        self.assertEqual(report.load_state(self.state_path(""))["last_sent_date"], None)

    def test_outage_event_and_state_preserved(self):
        cfg = common.load_all_configs()
        outage = {"ts": "2026-11-08T12:00:00", "ts_epoch": int(DAY_NOW.timestamp()),
                  "quotes": {}, "stats": {}, "candles": {}, "funding": {}, "oi": {},
                  "errors": [{"source": "binance", "asset": "BTC", "error": "TimeoutError"}],
                  "sources_ok": 0, "sources_total": 9}
        events = rules.evaluate(outage, {}, cfg["rules"], cfg["watchlist"], now=DAY_NOW)
        self.assertTrue(events)
        self.assertEqual(events[0]["severity"], "P0")
        self.assertTrue(events[0]["force_send"])
        self.assertEqual(events[0]["dedupe_key"], "price:ALL:data_quality:flat")
        # 全挂一轮不能把上一轮基线清空，否则恢复后首轮会静默
        state = {"last_prices": {"BTC": {"price": 75000.0, "ts": 1}},
                 "extremes": {"BTC": {"high": 76000.0, "low": 74000.0, "ts": 1}},
                 "oi_history": {"BTC": [{"ts": int(DAY_NOW.timestamp()) - 60, "oi": 1.0}]}}
        kept = rules.advance_state(state, outage, now=DAY_NOW)
        self.assertEqual(kept["last_prices"], state["last_prices"])
        self.assertEqual(kept["extremes"], state["extremes"])
        self.assertTrue(kept["oi_history"]["BTC"])


# ---------------------------------------------------------------- 已通过：数据质量阻断
class TestDataQuality(BaseCase):
    def test_spread_blocks_price_rules(self):
        cfg = common.load_all_configs()
        snapshot = load_snapshot("snapshot_shock.json")
        for quote in snapshot["quotes"].values():
            quote["spread_bps"] = 200.0
        events = rules.evaluate(snapshot, {}, cfg["rules"], cfg["watchlist"], now=DAY_NOW)
        price_rules = {"fast_move", "day_move", "new_extreme", "volume_spike", "level_break"}
        self.assertFalse([e for e in events if e.get("rule") in price_rules])
        quality = [e for e in events if e.get("rule") == "data_quality"]
        self.assertEqual({e["asset"] for e in quality}, {"BTC/ETH/BNB"})


# ---------------------------------------------------------------- 已通过：静默/跨重启去重（run 全链路）
class TestDeliveryIntegration(BaseCase):
    def test_night_alerts_are_sent_immediately(self):
        # 当前策略：夜间不静默，P0/P1 都即时发送。
        rc, out, _ = self.run_main([
            "--snapshot", os.path.join(FIXTURES, "snapshot_shock.json"),
            "--dry-run", "--json", "--data-dir", self.data,
        ], now=QUIET_NOW)
        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        self.assertFalse(payload["delivery"]["quiet"])
        self.assertEqual(len(payload["delivery"]["held"]), 0)
        self.assertEqual(len(payload["delivery"]["sent"]), 2)   # BTC P0 + ETH P1
        self.assertTrue(payload["delivery"]["sent"][0].startswith("BTC"))
        self.assertTrue(payload["delivery"]["sent"][1].startswith("ETH"))

    def test_cross_restart_no_duplicate_push(self):
        self.enable_fake_telegram()
        args = ["--snapshot", os.path.join(FIXTURES, "snapshot_shock.json"),
                "--notify", "--json", "--data-dir", self.data, "--no-lock"]
        rc1, out1, _ = self.run_main(args, now=DAY_NOW, env=FAKE_ENV)
        rc2, out2, _ = self.run_main(args, now=DAY_NOW, env=FAKE_ENV)
        self.assertEqual(rc1, 0, out1)
        self.assertEqual(rc2, 0, out2)
        first, second = json.loads(out1), json.loads(out2)
        self.assertEqual(len(first["delivery"]["sent"]), 2)     # BTC P0 + ETH P1
        self.assertEqual(second["delivery"]["sent"], [])        # 第二轮全部冷却
        state = delivery.load_state(self.state_path(""), DAY_NOW)
        self.assertEqual(state["counts"]["P0"], 1)
        self.assertEqual(state["counts"]["P1"], 1)
        self.assertEqual(len(state["cooldowns"]), 2)


# ---------------------------------------------------------------- 已通过：锁/降级
class TestLockAndDegrade(BaseCase):
    def test_stale_lock_recovered_by_run(self):
        lock_dir = os.path.join(self.data, "state", "collect.lock")
        os.makedirs(lock_dir)
        with open(os.path.join(lock_dir, "pid"), "w", encoding="utf-8") as f:
            f.write("99999")
        old = 1000
        os.utime(lock_dir, (old, old))
        rc, out, err = self.run_main([
            "--snapshot", os.path.join(FIXTURES, "snapshot_normal.json"),
            "--json", "--data-dir", self.data,
        ], now=DAY_NOW, env=FAKE_ENV)
        self.assertEqual(rc, 0, out + err)
        self.assertFalse(os.path.exists(lock_dir))

    def test_broken_fixture_json_degrades_to_outage(self):
        fixture = os.path.join(self.data, "fixtures")
        for source, kind in (("binance", "ticker24h"), ("binance", "klines"),
                             ("okx", "ticker"), ("okx", "candles"), ("coinbase", "stats")):
            os.makedirs(os.path.join(fixture, source), exist_ok=True)
            with open(os.path.join(fixture, source, "%s_BTC.json" % kind), "wb") as f:
                f.write(b"{broken json")
        rc, out, _ = self.run_main([
            "--fixtures", fixture, "--dry-run", "--json", "--data-dir", self.data,
        ], now=DAY_NOW)
        self.assertEqual(rc, 3, out)
        payload = json.loads(out)
        self.assertEqual(payload["source_health"]["ok"], 0)
        self.assertEqual(payload["events"][0]["severity"], "P0")
        self.assertTrue(payload["events"][0]["force_send"])


# ---------------------------------------------------------------- 已确认 bug 的复现
class TestKnownBugs(BaseCase):
    def test_bug_quiet_held_events_not_deduplicated(self):
        """BUG-1（P1）：静默/限额压制的事件每轮重复 append 到 held，早报与 --status 重复计数。

        复现：23:00 同一 P1 事件连续 3 轮 → held_events 应去重为 1 条（当前 3 条），
        早报「夜间压制明细」应 1 行（当前 3 行）。修复建议：held 按 dedupe_key 更新而不是追加；
        或压制态也写入 cooldowns（标记 held），避免每轮重复入队。
        """
        sender = FakeSender()
        quiet_cfg = {"delivery": {
            "quiet_hours": "22:00-07:00", "quiet_exception": ["P0"],
            "cooldown_minutes": {"default": 30}, "daily_cap_p1": 20,
        }}
        for _ in range(3):
            self.deliver([make_event()], now=QUIET_NOW, cfg=quiet_cfg, send_fn=sender)
        self.assertEqual(sender.calls, [])
        held = delivery.held_events(self.state_path(""), QUIET_NOW)
        self.assertEqual(len(held), 1)
        markdown = report.build_markdown(
            "2026-11-08", {}, self.state_path(""),
            common.load_all_configs()["watchlist"], now=QUIET_NOW)
        self.assertEqual(sum(1 for line in markdown.splitlines() if "急跌" in line), 1)

    def test_bug_corrupt_delivery_state_types_crash(self):
        """BUG-2a（P1）：delivery.json 是合法 JSON 但字段类型损坏时直接抛异常，不会回退空状态。

        需求 §8 承诺「状态损坏 → 容错读（坏文件回退空状态）」；当前只有非法 JSON 才容错，
        合法 JSON + 错误类型（ts/held_ts 非数字、counts 非数字）会让每轮 run 都 traceback rc=1，
        监控会一直中断到人工修文件。复现：写 delivery.json={"cooldowns":{"k":{"ts":"junk"}}} 后
        `python scripts/run.py --snapshot tests/fixtures/snapshot_normal.json` → ValueError。
        """
        self.write_state("delivery.json", {"cooldowns": {"price:BTC:fast_move:down": {"ts": "junk"}}})
        state = delivery.load_state(self.state_path(""), DAY_NOW)
        self.assertEqual(state["cooldowns"], {})
        self.write_state("delivery.json", {"counts": {"P1": "junk"},
                                           "held": [{"event": {"title": "t"}, "held_ts": "junk"}]})
        state = delivery.load_state(self.state_path(""), DAY_NOW)
        self.assertIsInstance(state["counts"]["P1"], int)

    def test_bug_corrupt_market_state_types_crash_run(self):
        """BUG-2b（P1）：market_state.json 合法 JSON 但结构错误（extremes/last_prices 为 list）
        时 rules.evaluate/advance_state 抛 AttributeError，每轮 run 直接失败（同样违反 §8）。"""
        self.write_state("market_state.json", {"extremes": [1, 2, 3]})
        rc, out, err = self.run_main([
            "--snapshot", os.path.join(FIXTURES, "snapshot_shock.json"),
            "--json", "--data-dir", self.data, "--no-lock",
        ], now=DAY_NOW, env=FAKE_ENV)
        self.assertNotIn("Traceback", err)
        self.assertIn(rc, (0, 1), out)

    def test_bug_missing_snapshot_file_should_be_friendly(self):
        """BUG-3（P2）：--snapshot 指向不存在文件/坏 JSON 时抛出原始 traceback，rc=1；
        仓库约定「配置/输入错误 → rc=2 + 可读错误」，监控脚本会把它当网络故障。"""
        rc, out, err = self.run_main([
            "--snapshot", os.path.join(self.data, "nope.json"), "--dry-run", "--json",
            "--data-dir", os.path.join(self.data, "out"),
        ], now=DAY_NOW)
        self.assertEqual(rc, 2, out + err)
        self.assertNotIn("Traceback", err)

    def test_bug_malformed_snapshot_json_should_be_friendly(self):
        """BUG-3b（P2）：--snapshot 是坏 JSON 或多层结构类型错误（如 quotes.BTC 为数字）同样 traceback。"""
        self.write_state("bad.json", "{not json")
        rc, out, err = self.run_main([
            "--snapshot", self.state_path("bad.json"), "--dry-run", "--json",
            "--data-dir", os.path.join(self.data, "out2"),
        ], now=DAY_NOW)
        self.assertEqual(rc, 2, out + err)
        self.assertNotIn("Traceback", err)

    def test_bug_retry_attempts_not_capped(self):
        """BUG-4（P2）：发送失败重试队列 attempts 永远停在 1，MAX_PENDING_ATTEMPTS=5 失效，
        网络长期不通时每 60s 无限重试（pending.created_ts 每轮重置，1h TTL 也永不生效）。

        原因：重试事件先从 state["pending"] 取出，失败后再查 prev_attempts 时列表已清空，
        prev_attempts 恒为 0。复现：8 轮持续失败 → FakeSender 被调用 8 次（应 ≤5）。
        """
        sender = FakeSender(fail_times=10 ** 6)
        for i in range(8):
            self.deliver([make_event()], now=DAY_NOW + datetime.timedelta(minutes=2 * i),
                         send_fn=sender)
        self.assertLessEqual(len(sender.calls), delivery.MAX_PENDING_ATTEMPTS)
        state = delivery.load_state(self.state_path(""), DAY_NOW)
        self.assertTrue(all(p.get("attempts", 0) <= delivery.MAX_PENDING_ATTEMPTS
                            for p in state["pending"]))

    def test_bug_report_time_before_window_never_due(self):
        """BUG-5（P2）：app.report.time 配成 08:00 之前（如 07:00）时，is_due 因硬编码
        REPORT_WINDOW=(8,22) 判定永不到点 → 日报永远不生成。配置项可配但部分取值无效。"""
        self.assertTrue(report.is_due(
            datetime.datetime(2026, 11, 8, 7, 30),
            {"report": {"enabled": True, "time": "07:00"}}, {}))

    def test_bug_report_send_failure_not_retried(self):
        """BUG-8（P2）：早报发送失败后 report.json 已记 last_generated_date，当天不再重试；
        run.py 也不把 report errors 计入 rc（rc 仍 0）→ 一次网络抖动会**永久丢掉当天早报**。
        复现：send_fn 第一次失败 → 第二轮 is_due=False（期望：失败可重试）。"""
        snapshot = load_snapshot("snapshot_normal.json")
        cfg = common.load_all_configs()
        cfg["app"]["report"]["send"] = True  # 本用例专门验证发送失败重试
        first = report.maybe_run(self.state_path(""), snapshot, cfg["watchlist"], cfg["app"],
                                 env=FAKE_ENV, now=DAY_NOW, force=True,
                                 send_fn=FakeSender(fail_times=1))
        self.assertTrue(first["generated"])
        self.assertFalse(first["sent"])
        self.assertTrue(first["errors"])
        # 失败后 10 分钟节流；+11 分钟应重新生成并发送
        second = report.maybe_run(self.state_path(""), snapshot, cfg["watchlist"], cfg["app"],
                                  env=FAKE_ENV, now=DAY_NOW + datetime.timedelta(minutes=11),
                                  force=False, send_fn=FakeSender())
        self.assertTrue(second["generated"])
        self.assertTrue(second["sent"])

    def test_bug_bad_telegram_credentials_should_return_rc2(self):
        """BUG-6（P2）：Telegram 凭据缺失/不合法时 --notify 返回 rc=1，
        而 README/约定写的是「2 = 配置或凭据错误」；且不会写 alerts 审计。"""
        rc, out, err = self.run_main([
            "--snapshot", os.path.join(FIXTURES, "snapshot_normal.json"),
            "--notify", "--json", "--data-dir", self.data, "--no-lock",
        ], now=DAY_NOW, env={})
        self.assertEqual(rc, 2, out + err)

    def test_bug_stale_lock_with_foreign_file_should_recover(self):
        """BUG-7（P3）：过期锁目录里若存在 pid 之外的残留文件，_remove 的 rmdir 失败后静默放弃，
        过期锁永远无法自愈 → 之后每轮 rc=4，直到人工删目录。"""
        paths = common.data_paths(self.data)
        lock_dir = os.path.join(self.data, "state", "collect.lock")
        os.makedirs(lock_dir)
        with open(os.path.join(lock_dir, "stray"), "w") as f:
            f.write("x")
        old = 1000.0
        os.utime(lock_dir, (old, old))
        lock = common.acquire_lock(paths, stale_seconds=300)
        self.assertIsNotNone(lock)
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    unittest.main(verbosity=2)
