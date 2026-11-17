#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""治理：冷却（含背离 12h）、加剧（1.5 倍且 ≥1h）、同点合并、P0 每本地日上限、按接口过期、只回看 1 小时。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import engine  # noqa: E402
import helpers  # noqa: E402
from helpers import STEP, T0  # noqa: E402

import common  # noqa: E402

CFG = helpers.rules_cfg()
H = 3600


def hit(rule="retail_shift", direction="up", magnitude=20.0, severity="P1"):
    return {"rule": rule, "severity": severity, "direction": direction, "magnitude": magnitude, "metrics": {}}


class TestAdmit(unittest.TestCase):
    def setUp(self):
        self.state = engine.new_state()

    def admit(self, h, ts):
        return engine._admit(h, "BTC", ts, self.state, CFG)

    def test_cooldown_4h(self):
        self.assertIsNotNone(self.admit(hit(), T0))
        self.assertIsNone(self.admit(hit(), T0 + 3 * H))
        self.assertIsNotNone(self.admit(hit(), T0 + 4 * H))
        self.assertIsNotNone(self.admit(hit(direction="down"), T0 + 4 * H + STEP))   # 方向不同，不受冷却

    def test_divergence_cooldown_12h(self):
        h = hit("smart_divergence", "retail_long", 16.0)
        self.assertIsNotNone(self.admit(h, T0))
        self.assertIsNone(self.admit(h, T0 + 8 * H))
        self.assertIsNotNone(self.admit(h, T0 + 12 * H))

    def test_escalation_needs_size_and_gap(self):
        self.assertIsNotNone(self.admit(hit(magnitude=20.0), T0))
        self.assertIsNone(self.admit(hit(magnitude=40.0), T0 + 30 * 60))      # 够大但不到 1 小时
        self.assertIsNone(self.admit(hit(magnitude=29.0), T0 + 2 * H))        # 够久但不到 1.5 倍
        verdict = self.admit(hit(magnitude=30.0), T0 + 2 * H)
        self.assertEqual(verdict, {"escalated": True})

    def test_escalation_exact_boundaries(self):
        self.assertIsNotNone(self.admit(hit(magnitude=20.0), T0))
        self.assertEqual(self.admit(hit(magnitude=30.0), T0 + H), {"escalated": True})   # 恰好 1.5 倍、恰好 1h

    def test_p0_cap_resets_next_local_day(self):
        p0 = hit("crowding_build", "long", 6.0, "P0")
        day = common.local_date(T0)
        t = T0
        while common.local_date(t) == day:                 # 找到下一个本地日的第一个 5m 点
            t += STEP
        self.state["p0"]["BTC|%s" % day] = 2
        self.assertIsNone(self.admit(p0, t - STEP))
        self.assertIsNotNone(self.admit(p0, t))

    def test_prune_keeps_state_for_longest_cooldown(self):
        cfg = helpers.rules_cfg(smart_divergence={"cooldown_hours": 72})
        state = engine.new_state()
        state["last"]["smart_divergence|BTC|retail_long"] = [T0, 16.0]
        engine._prune_state(state, T0 + 60 * H, cfg)
        self.assertIn("smart_divergence|BTC|retail_long", state["last"])
        engine._prune_state(state, T0 + 80 * H, cfg)
        self.assertEqual(state["last"], {})

    def test_p0_daily_cap_by_local_date(self):
        day_start = T0 - T0 % 86400   # 只用来找三个同一本地日、彼此间隔 > 4h 的时点
        times = [t for t in range(day_start, day_start + 2 * 86400, 5 * H)]
        by_date = {}
        for t in times:
            by_date.setdefault(common.local_date(t), []).append(t)
        same_day = next(v for v in by_date.values() if len(v) >= 3)
        p0 = hit("crowding_build", "long", 6.0, "P0")
        results = [self.admit(p0, t) for t in same_day[:3]]
        self.assertEqual([r is not None for r in results], [True, True, False])


class TestStep(unittest.TestCase):
    def test_same_point_hits_merge_into_one_card(self):
        s = helpers.flat_series(T0, 300, g=2.0, t=2.0)
        s["G"][T0] = (2.6, 0.72)        # 1h +30% → 急转；24h +30%
        s["T"][T0] = (1.6, 0.6)         # 24h −20% → 背离
        cards, _ = engine.step({"BTC": s}, engine.new_state(), CFG, T0, catchup_s=STEP)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["rule"], "smart_divergence")
        self.assertEqual(cards[0]["also"], ["retail_shift"])

    def test_stale_metric_only_stops_its_rules(self):
        s = helpers.flat_series(T0, 300, g=2.0)
        s["G"][T0] = (2.6, 0.72)
        cards, _ = engine.step({"BTC": s}, engine.new_state(), CFG, T0, stale={"BTC": {"K"}}, catchup_s=STEP)
        self.assertEqual([c["rule"] for c in cards], ["retail_shift"])
        cards, _ = engine.step({"BTC": s}, engine.new_state(), CFG, T0, stale={"BTC": {"G"}}, catchup_s=STEP)
        self.assertEqual(cards, [])

    def test_catchup_window_and_cursor(self):
        s = helpers.flat_series(T0, 300, g=2.0)
        s["G"][T0 - 3 * H] = (3.0, 0.75)          # 3 小时前的急转（及 2 小时前的回落）：超出 1 小时回看，不补发
        state = engine.new_state()
        cards, _ = engine.step({"BTC": s}, state, CFG, T0, catchup_s=H)
        self.assertEqual(cards, [])
        self.assertEqual(state["cursor"]["BTC"]["retail_shift"], T0)
        s["G"][T0 + STEP] = (3.0, 0.75)
        cards, _ = engine.step({"BTC": s}, state, CFG, T0 + STEP, catchup_s=H)
        self.assertEqual(len(cards), 1)            # 只评估新点
        cards, _ = engine.step({"BTC": s}, state, CFG, T0 + STEP, catchup_s=H)
        self.assertEqual(cards, [])                # 同一点不重复评估

    def test_p2_hits_are_not_cards(self):
        s = helpers.flat_series(T0, 2100, g=2.0)
        for k in range(1, 2100):
            s["G"][T0 - k * STEP] = (2.0 + (k % 10) / 100.0, 0.67)
        s["G"][T0] = (2.15, 0.68)                  # 7 天新高，但 1h 只 +7%
        cards, p2 = engine.step({"BTC": s}, engine.new_state(), CFG, T0, catchup_s=STEP)
        self.assertEqual(cards, [])
        self.assertEqual([h["rule"] for h in p2], ["extreme_level"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
