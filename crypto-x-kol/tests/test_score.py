#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_score.py —— KOL 推文价值评分（离线纯函数）。"""
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import score  # noqa: E402

RULES = {
    "alert": {"thresholds": {"P0": 45, "P1": 28, "P2": 10}},
    "topics": {
        "security": {"label": "安全事件", "kind": "critical",
                     "keywords": ["被盗", "hack", "hacked"]},
        "regulation": {"label": "监管/ETF", "kind": "critical", "keywords": ["ETF", "SEC"]},
        "listing": {"label": "上币/下架", "kind": "strong", "keywords": ["上币", "下架"]},
        "action": {"label": "操作观点", "kind": "action", "keywords": ["做多"]},
        "breaking": {"label": "突发", "kind": "breaking", "keywords": ["突发"]},
        "rumor": {"label": "传闻", "kind": "rumor", "keywords": ["传闻"]},
        "promo": {"label": "推广", "kind": "promo", "keywords": ["抽奖"]},
        "gm": {"label": "日常", "kind": "gm", "keywords": ["gm"]},
    },
    "high_signal": {"keywords": ["ETF", "hack", "上币"]},
    "assets": ["BTC", "ETH", "BNB"],
}
NOW = datetime.datetime(2026, 11, 8, 22, 0, 0)


def post(handle="tester", text="", tier="T2", pid=None, seen=NOW, quote=""):
    return {
        "id": pid or ("id-" + handle + "-" + str(abs(hash(text)) % 10000)),
        "handle": handle, "name": handle, "tier": tier, "tags": [],
        "text": text, "quote": quote,
        "first_seen": seen.isoformat(timespec="seconds"),
    }


def hist(handle, text, hours_ago=1):
    return post(handle=handle, text=text, seen=NOW - datetime.timedelta(hours=hours_ago))


class TestScoring(unittest.TestCase):
    def test_critical_t1_is_p0(self):
        out = score.evaluate_post(post("VitalikButerin", "A major hack drained the bridge.", "T1"),
                                  RULES, history=[], now=NOW)
        self.assertEqual(out["level"], "P0")
        self.assertEqual(out["topic"], "security")
        self.assertIn("安全事件", " ".join(out["reasons"]))

    def test_strong_keyword_is_p1_not_p0(self):
        out = score.evaluate_post(post("WuBlockchain", "币安将下架某代币", "T1"),
                                  RULES, history=[], now=NOW)
        self.assertEqual(out["level"], "P1")
        self.assertEqual(out["topic"], "listing")

    def test_consensus_boosts_to_p0(self):
        h = [hist("a", "ETF flows are wild"), hist("b", "ETF approved?")]
        out = score.evaluate_post(post("c", "ETF update", "T1"), RULES, history=h, now=NOW)
        self.assertGreaterEqual(out["consensus"], 2)
        self.assertEqual(out["level"], "P0")

    def test_promo_is_muted(self):
        out = score.evaluate_post(post("Pentosh1", "抽奖 上币 活动！", "T2"),
                                  RULES, history=[], now=NOW)
        self.assertEqual(out["level"], "MUTE")

    def test_gm_is_muted(self):
        out = score.evaluate_post(post("Pentosh1", "gm gm good morning", "T2"),
                                  RULES, history=[], now=NOW)
        self.assertEqual(out["level"], "MUTE")

    def test_rumor_downgrades(self):
        out = score.evaluate_post(post("WuBlockchain", "传闻 ETF 今晚通过", "T2"),
                                  RULES, history=[], now=NOW)
        self.assertEqual(out["level"], "P1")  # critical 但被传闻扣分，够不到 P0 阈值

    def test_ascii_boundary(self):
        out = score.evaluate_post(post("tester", "in the second quarter of the year", "T2"),
                                  RULES, history=[], now=NOW)
        self.assertIsNone(out["topic"])       # SEC 不应命中 second
        self.assertEqual(out["topic_hits"], [])

    def test_novelty_bonus_only_once(self):
        text = "ETF update again"
        fresh = score.evaluate_post(post("a", text), RULES, history=[], now=NOW)
        repeat = score.evaluate_post(post("a", text), RULES,
                                     history=[hist("a", "ETF last week", hours_ago=48)], now=NOW)
        self.assertEqual(fresh["score"] - repeat["score"], 8)
        self.assertTrue(any("首次" in r for r in fresh["reasons"]))
        self.assertFalse(any("首次" in r for r in repeat["reasons"]))

    def test_evaluate_sorts_by_level(self):
        posts = [post("a", "ETF"), post("b", "gm"), post("c", "hack attack", "T1")]
        out = score.evaluate(posts, RULES, history=[], now=NOW)
        self.assertEqual(out[0]["level"], "P0")
        self.assertEqual(out[-1]["level"], "MUTE")


class TestPolicy(unittest.TestCase):
    def test_defaults(self):
        cfg = score.policy({})
        self.assertEqual(cfg["thresholds"]["P0"], 45)
        self.assertEqual(cfg["caps"]["per_day"], 8)
        self.assertTrue(cfg["caps"]["p0_bypass_cap"])

    def test_override(self):
        cfg = score.policy({"alert": {"thresholds": {"P0": 60}}})
        self.assertEqual(cfg["thresholds"]["P0"], 60)
        self.assertEqual(cfg["thresholds"]["P1"], 28)


if __name__ == "__main__":
    unittest.main(verbosity=2)
