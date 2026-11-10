#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""delivery 单元测试：分级/静默/冷却/升级/限额/重试/审计（全部离线，不读真实 .env）。"""
import datetime
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

import common  # noqa: E402

TOKEN = "123456789:" + "A" * 35


def make_event(rule="fast_move", asset="BTC", direction="down", severity="P1", magnitude=2.0,
               key=None, force_send=False):
    return {
        "rule": rule,
        "asset": asset,
        "direction": direction,
        "severity": severity,
        "title": "%s %s异动 %.2f%%" % (asset, "急跌" if direction == "down" else "急涨", magnitude),
        "detail": ["现价 75,412.00", "5m -2.10% · 15m -2.80%"],
        "metrics": {"window": "m5", "change_pct": -magnitude, "price": 75412.0,
                    "spread_bps": 3.1, "n_sources": 3},
        "dedupe_key": key or "price:%s:%s:%s" % (asset, rule, direction),
        "magnitude": magnitude,
        "urls": ["https://www.binance.com/zh-CN/trade/BTC_USDT?type=spot"],
        "why": "急跌通常伴随杠杆清算",
        "action": "检查杠杆仓位与止损",
        "force_send": force_send,
        "ts": "2026-11-08T12:00:00",
        "evidence": [{"source": "binance", "price": 75790.5},
                     {"source": "okx", "price": 75795.8},
                     {"source": "coinbase", "price": 75680.0}],
    }


DEFAULT_CFG = {
    "delivery": {
        "quiet_hours": "22:00-07:00",
        "quiet_exception": ["P0"],
        "cooldown_minutes": {"default": 30, "fast_move": 15, "day_move": 120},
        "escalate_ratio": 1.5,
        "daily_cap_p1": 20,
    }
}
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "123"}


class FakeSender:
    def __init__(self, fail_times=0):
        self.calls = []
        self.fail_times = fail_times

    def __call__(self, token, chat_id, text, silent, disable_preview=False):
        self.calls.append({"token": token, "chat_id": chat_id, "text": text,
                           "silent": silent, "disable_preview": disable_preview})
        if self.fail_times > 0:
            self.fail_times -= 1
            return False, "HTTP 429: too many requests"
        return True, ""


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.patcher = mock.patch.object(delivery.common, "log_line", return_value="")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def deliver(self, events, now=None, dry_run=False, sender=None, cfg=None):
        return delivery.deliver(
            events, cfg or DEFAULT_CFG, self.dir, env=ENV,
            now=now or datetime.datetime(2026, 11, 8, 12, 0, 0),
            dry_run=dry_run, send_fn=sender or FakeSender(),
        )

    def state(self):
        return common.safe_json_load(os.path.join(self.dir, "delivery.json"), {})


class TestEventToAlert(BaseCase):
    def test_schema_and_fields(self):
        alert = delivery.event_to_alert(make_event(), poll_seconds=60)
        self.assertEqual(alert["schema"], "alert/v1")
        self.assertEqual(alert["module"], "crypto-price")
        self.assertEqual(alert["severity"], "P1")
        self.assertEqual(alert["assets"], ["BTC"])
        self.assertEqual(alert["dedupe_key"], "price:BTC:fast_move:down")
        self.assertEqual(alert["summary"]["why"], "急跌通常伴随杠杆清算")
        self.assertEqual(alert["poll_seconds"], 60)

    def test_all_asset_has_no_assets(self):
        ev = make_event()
        ev["asset"] = "ALL"
        self.assertEqual(delivery.event_to_alert(ev)["assets"], [])


class TestDecisions(BaseCase):
    def test_p2_is_archived_not_sent(self):
        sender = FakeSender()
        res = self.deliver([make_event(severity="P2")], sender=sender)
        self.assertEqual(res["archived"], 1)
        self.assertEqual(len(res["sent"]), 0)
        self.assertEqual(sender.calls, [])

    def test_p1_daytime_sent(self):
        sender = FakeSender()
        res = self.deliver([make_event()], sender=sender)
        self.assertEqual(len(res["sent"]), 1)
        self.assertEqual(len(sender.calls), 1)
        self.assertIn("P1", sender.calls[0]["text"])
        self.assertEqual(self.state()["counts"]["P1"], 1)

    def test_p1_quiet_is_held(self):
        sender = FakeSender()
        res = self.deliver([make_event()], now=datetime.datetime(2026, 11, 8, 23, 30), sender=sender)
        self.assertEqual(len(res["held"]), 1)
        self.assertEqual(len(sender.calls), 0)
        self.assertIn("静默", (res["held"][0].get("delivery") or {}).get("reason", ""))
        self.assertEqual(len(delivery.held_events(self.dir, datetime.datetime(2026, 11, 8, 23, 35))), 1)

    def test_quiet_off_sends_at_night(self):
        cfg = {"delivery": dict(DEFAULT_CFG["delivery"], quiet_hours="off", daily_cap_p1=0)}
        sender = FakeSender()
        res = self.deliver([make_event()], now=datetime.datetime(2026, 11, 8, 23, 30),
                           cfg=cfg, sender=sender)
        self.assertEqual(len(res["sent"]), 1)
        self.assertEqual(len(sender.calls), 1)

    def test_p0_pierces_quiet(self):
        sender = FakeSender()
        res = self.deliver([make_event(severity="P0")], now=datetime.datetime(2026, 11, 8, 23, 30),
                           sender=sender)
        self.assertEqual(len(res["sent"]), 1)
        self.assertEqual(len(sender.calls), 1)

    def test_force_send_bypasses_quiet_and_cap(self):
        cfg = {"delivery": dict(DEFAULT_CFG["delivery"], daily_cap_p1=0)}
        sender = FakeSender()
        res = self.deliver([make_event(severity="P1", force_send=True)],
                           now=datetime.datetime(2026, 11, 8, 23, 30), sender=sender, cfg=cfg)
        self.assertEqual(len(res["sent"]), 1)

    def test_cooldown_suppresses_same_key(self):
        sender = FakeSender()
        now = datetime.datetime(2026, 11, 8, 12, 0)
        self.deliver([make_event()], now=now, sender=sender)
        res = self.deliver([make_event()], now=now + datetime.timedelta(minutes=1), sender=sender)
        self.assertEqual(len(res["suppressed"]), 1)

    def test_escalation_bypasses_cooldown(self):
        sender = FakeSender()
        now = datetime.datetime(2026, 11, 8, 12, 0)
        self.deliver([make_event(magnitude=2.0)], now=now, sender=sender)
        res = self.deliver([make_event(magnitude=3.5)], now=now + datetime.timedelta(minutes=1),
                           sender=sender)
        self.assertEqual(len(res["sent"]), 1)
        self.assertTrue(res["sent"][0].get("escalated"))
        self.assertIn("加剧", cards.format_price_alert(res["sent"][0]))

    def test_daily_cap_holds_p1(self):
        cfg = {"delivery": dict(DEFAULT_CFG["delivery"], daily_cap_p1=1)}
        sender = FakeSender()
        now = datetime.datetime(2026, 11, 8, 12, 0)
        self.deliver([make_event()], now=now, sender=sender, cfg=cfg)
        res = self.deliver([make_event(key="price:ETH:fast_move:down", asset="ETH")],
                           now=now, sender=sender, cfg=cfg)
        self.assertEqual(res["held"][0]["severity"], "P1")
        self.assertIn("上限", (res["held"][0].get("delivery") or {}).get("reason", ""))


class TestRetryAndAudit(BaseCase):
    def test_send_failure_goes_pending_then_retries(self):
        now = datetime.datetime(2026, 11, 8, 12, 0)
        flaky = FakeSender(fail_times=1)
        res = self.deliver([make_event()], now=now, sender=flaky)
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(len(self.state()["pending"]), 1)
        # 60 秒后：重试队列里的同一事件应被重新发送
        sender = FakeSender()
        res2 = self.deliver([], now=now + datetime.timedelta(seconds=61), sender=sender)
        self.assertEqual(len(res2["sent"]), 1)
        self.assertEqual(len(self.state()["pending"]), 0)

    def test_dry_run_does_not_write_state(self):
        sender = FakeSender()
        self.deliver([make_event()], dry_run=True, sender=sender)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "delivery.json")))
        self.assertEqual(sender.calls, [])

    def test_alerts_audit_written(self):
        self.deliver([make_event()])
        path = os.path.join(self.dir, "alerts-2026-11-08.json")
        self.assertTrue(os.path.exists(path))
        rows = common.safe_json_load(path, [])
        self.assertEqual(rows[0]["rule"], "fast_move")
        self.assertEqual(rows[0]["delivery"]["decision"], "send")

    def test_clear_held(self):
        self.deliver([make_event()], now=datetime.datetime(2026, 11, 8, 23, 30))
        self.assertEqual(len(delivery.held_events(self.dir, datetime.datetime(2026, 11, 8, 23, 35))), 1)
        delivery.clear_held(self.dir, datetime.datetime(2026, 11, 8, 23, 40))
        self.assertEqual(len(delivery.held_events(self.dir, datetime.datetime(2026, 11, 8, 23, 35))), 0)


class TestCredentials(BaseCase):
    def test_bad_credentials_returns_error_without_sending(self):
        sender = FakeSender()
        res = delivery.deliver([make_event()], DEFAULT_CFG, self.dir,
                               env={"TELEGRAM_BOT_TOKEN": "bad", "TELEGRAM_CHAT_ID": "x"},
                               now=datetime.datetime(2026, 11, 8, 12, 0), send_fn=sender)
        self.assertTrue(res["errors"])
        self.assertEqual(sender.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
