#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_digest.py —— 日报/告警消息/上限选择/推送状态（离线）。"""
import datetime
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import digest  # noqa: E402

import score  # noqa: E402

RULES = {
    "alert": {"thresholds": {"P0": 45, "P1": 28, "P2": 10}},
    "topics": {
        "security": {"label": "安全事件", "kind": "critical", "keywords": ["hack"]},
        "regulation": {"label": "监管/ETF", "kind": "critical", "keywords": ["ETF"]},
        "listing": {"label": "上币/下架", "kind": "strong", "keywords": ["上币"]},
    },
    "high_signal": {"keywords": ["ETF", "hack"]},
    "assets": ["BTC", "ETH", "BNB"],
}
NOW = datetime.datetime(2026, 11, 8, 22, 0, 0)


def post(handle="saylor", text="", tier="T2", pid=None, role=""):
    return {"id": pid or ("id-" + handle + "-" + str(len(text))),
            "handle": handle, "name": handle, "role": role, "tier": tier, "tags": [],
            "text": text, "quote": "", "first_seen": "2026-11-08T21:55:00"}


def evaluated(posts):
    return score.evaluate(posts, RULES, history=[], now=NOW)


class TestReport(unittest.TestCase):
    def test_sections_and_levels(self):
        posts = evaluated([post("a", "ETF hack", "T1"), post("b", "GM everyone", "T2")])
        body = digest.build_report(posts, "2026-11-08", RULES, "2026-11-08T22:00:00")
        self.assertIn("# KOL 推文日报 2026-11-08", body)
        self.assertIn("## 🔔 需关注", body)
        self.assertIn("P0 ·", body)
        self.assertIn("理由：", body)
        self.assertIn("## 📋 全部新帖", body)

    def test_report_uses_translation(self):
        evaluated_posts = evaluated([post("a", "ETF approved today", "T1")])
        cache = {evaluated_posts[0]["id"]: {"lang": "en", "zh": "ETF 今日获批", "summary": "ETF 获批，属利好。"}}
        body = digest.build_report(evaluated_posts, "2026-11-08", RULES, "2026-11-08T22:00:00", cache=cache)
        self.assertIn("ETF 今日获批", body)
        self.assertIn("一句话：ETF 获批，属利好。", body)
        self.assertNotIn("ETF approved today", body)

    def test_author_name_and_role(self):
        evaluated_posts = evaluated([post("saylor", "ETF approved today", "T1",
                                          role="Strategy 执行主席")])
        cache = {evaluated_posts[0]["id"]: {"lang": "en", "zh": "ETF 今日获批", "summary": "利好。"}}
        alert = digest.build_alert_message(evaluated_posts, RULES, 120, 5, "r.md", cache=cache)
        self.assertIn("（saylor · Strategy 执行主席）", alert)
        body = digest.build_report(evaluated_posts, "2026-11-08", RULES,
                                   "2026-11-08T22:00:00", cache=cache)
        self.assertIn("### @saylor（saylor · Strategy 执行主席）", body)
        self.assertIn("[翻译] ETF 今日获批", body)

    def test_no_alert_section(self):
        posts = evaluated([post("b", "good morning", "T2")])
        body = digest.build_report(posts, "2026-11-08", RULES, "2026-11-08T22:00:00")
        self.assertIn("_今日无达到告警阈值的推文_", body)


class TestAlertMessage(unittest.TestCase):
    def test_content_and_cap(self):
        alerts = evaluated([post("a", "ETF hack", "T1"), post("b", "ETF approved", "T1"),
                            post("c", "上币 announcement", "T1")])
        alerts = [p for p in alerts if p["level"] in ("P0", "P1")]
        text = digest.build_alert_message(alerts, RULES, 60, 2, "/" + "Users/someone/crypto-x-kol/data/reports/x.md")
        self.assertIn("完整日报：data/reports/x.md", text)
        self.assertNotIn("Users/", text)
        self.assertIn("高价值推文", text)
        self.assertIn("@a", text)
        self.assertIn("P0", text)
        self.assertIn("另有", text)                       # 超过单条上限
        self.assertIn("data/reports/x.md", text)
        self.assertLessEqual(len(text), digest.MAX_LEN)

    def test_localized_chinese_and_summary(self):
        evaluated_posts = evaluated([post("a", "ETF approved today", "T1")])
        alerts = [p for p in evaluated_posts if p["level"] in ("P0", "P1")]
        cache = {alerts[0]["id"]: {"lang": "en", "zh": "ETF 今日获批", "summary": "ETF 获批，属利好。"}}
        text = digest.build_alert_message(alerts, RULES, 120, 5, "r.md", cache=cache)
        self.assertIn("[翻译] ETF 今日获批", text)
        self.assertIn("ETF 获批，属利好", text)
        self.assertNotIn("ETF approved today", text)

    def test_alert_uses_tweet_url_when_available(self):
        evaluated_posts = evaluated([post("a", "ETF approved", "T1")])
        alerts = [p for p in evaluated_posts if p["level"] in ("P0", "P1")]
        alerts[0]["tweet_url"] = "https://x.com/a/status/123456"
        text = digest.build_alert_message(alerts, RULES, 120, 5, "r.md")
        self.assertIn('href="https://x.com/a/status/123456"', text)
        self.assertNotIn('href="https://x.com/a"', text)
        self.assertIn("原帖", text)

    def test_html_escaped(self):
        alerts = evaluated([post("a", "ETF <script>alert(1)</script>", "T1")])
        alerts = [p for p in alerts if p["level"] in ("P0", "P1")]
        text = digest.build_alert_message(alerts, RULES, 200, 5, "r.md")
        self.assertNotIn("<script>", text)


class TestSelectAlerts(unittest.TestCase):
    def _posts(self, n, level="P1"):
        items = [{"id": "p%d" % i, "level": level, "score": 40} for i in range(n)]
        return items

    def test_daily_cap(self):
        caps = {"per_day": 2, "max_per_message": 5, "p0_bypass_cap": True}
        chosen = digest.select_alerts(self._posts(5), sent_today=1, caps=caps)
        self.assertEqual(len(chosen), 1)                  # 今日只剩 1 条额度

    def test_p0_bypasses_daily_cap(self):
        caps = {"per_day": 1, "max_per_message": 5, "p0_bypass_cap": True}
        chosen = digest.select_alerts(self._posts(2, "P0"), sent_today=1, caps=caps)
        self.assertEqual(len(chosen), 2)

    def test_message_cap(self):
        caps = {"per_day": 100, "max_per_message": 3}
        chosen = digest.select_alerts(self._posts(10), sent_today=0, caps=caps)
        self.assertEqual(len(chosen), 3)


class TestMarkAfterSend(unittest.TestCase):
    def _pending(self):
        return [{"id": "p0", "level": "P0", "score": 60},
                {"id": "p1", "level": "P1", "score": 35},
                {"id": "p2", "level": "P2", "score": 12}]

    def test_quiet_keeps_p1_pending(self):
        pushed = {}
        held = digest.mark_after_send(pushed, self._pending(), [self._pending()[0]], quiet=True)
        self.assertEqual(pushed["p0"]["decision"], "alert")
        self.assertEqual(pushed["p2"]["decision"], "digest")
        self.assertNotIn("p1", pushed)          # P1 留待次日，不能被吞掉
        self.assertEqual(held, 1)

    def test_non_quiet_marks_rest_digest(self):
        pushed = {}
        held = digest.mark_after_send(pushed, self._pending(), [self._pending()[0]], quiet=False)
        self.assertEqual(held, 0)
        self.assertEqual(pushed["p1"]["decision"], "digest")
        self.assertEqual(pushed["p2"]["decision"], "digest")


class TestState(unittest.TestCase):
    def test_pushed_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = digest.ensure_dirs(tmp)
            pushed = digest.load_pushed(paths, "2026-11-08")
            self.assertEqual(pushed, {})
            posts = [{"id": "a", "score": 50, "level": "P0"},
                     {"id": "b", "score": 12, "level": "P2"}]
            digest.mark(pushed, posts[:1], "alert")
            digest.mark(pushed, posts[1:], "digest")
            digest.save_pushed(paths, "2026-11-08", pushed)
            loaded = digest.load_pushed(paths, "2026-11-08")
            self.assertEqual(loaded["a"]["decision"], "alert")
            self.assertEqual(loaded["b"]["decision"], "digest")
            self.assertEqual(digest.sent_alerts_today(loaded), 1)
            self.assertEqual([p["id"] for p in digest.pending_posts(
                [{"id": "a"}, {"id": "b"}, {"id": "c"}], loaded)], ["c"])

    def test_legacy_state_is_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = digest.ensure_dirs(tmp)
            digest.save_pushed(paths, "2026-11-08", {"a": "2026-11-08T10:00:00"})
            loaded = digest.load_pushed(paths, "2026-11-08")
            self.assertEqual(loaded["a"]["decision"], "digest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
