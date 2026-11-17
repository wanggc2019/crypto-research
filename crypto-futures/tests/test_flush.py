#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""long_flush（多头出清：资金费率转负 + 账户多空比回落）的规则边界、治理、卡片与回测口径。

docs/crypto-futures/requirement-flush.md §3、§5。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import backtest  # noqa: E402
import cards  # noqa: E402
import engine  # noqa: E402
import helpers  # noqa: E402
import rules as R  # noqa: E402
from helpers import STEP, T0  # noqa: E402

import common  # noqa: E402

CFG = helpers.rules_cfg()
FORBIDDEN = ("多源核验", "价格异动", "开多", "抄底", "减仓", "见底了")
H = 3600


def flush_series(g_now=2.0, g_24h=2.5, rates=(-0.00003, -0.00003), end=T0, points=300):
    """G 平稳在 g_24h，最后一个点降到 g_now；RT 从 end 往前每 5m 一个资金费率（rates[0] 为最新）。"""
    s = helpers.flat_series(end, points, g=g_24h)
    s["G"][end] = (g_now, g_now / (1 + g_now))
    for k, rate in enumerate(rates):
        s["RT"][end - k * STEP] = (rate, 100.0, 1000.0, 8)
    return s


class TestRule(unittest.TestCase):
    def hit(self, cfg=CFG, **kw):
        return R.long_flush(flush_series(**kw), T0, cfg)

    def test_trigger(self):
        h = self.hit()
        self.assertEqual((h["rule"], h["severity"], h["direction"]), ("long_flush", "P1", "down"))
        self.assertAlmostEqual(h["metrics"]["g_change_24h_pct"], -20.0)
        self.assertAlmostEqual(h["magnitude"], 20.0)
        self.assertAlmostEqual(h["metrics"]["rate_pct"], -0.003)

    def test_funding_threshold_boundary(self):
        self.assertIsNotNone(self.hit(rates=(-0.00002, -0.00002)))      # = −0.002% 算满足
        self.assertIsNone(self.hit(rates=(-0.000019, -0.00003)))       # 贴零噪声不算转负
        self.assertIsNone(self.hit(rates=(0.0001, 0.0001)))

    def test_needs_consecutive_rounds(self):
        self.assertIsNone(self.hit(rates=(-0.00003, 0.00005)))         # 上一轮仍为正：未确认
        self.assertIsNone(self.hit(rates=(-0.00003,)))                  # 只有 1 个样本
        self.assertIsNotNone(self.hit(rates=(-0.00003, None, -0.00003)))  # 中间缺 1 轮仍可确认
        one = helpers.rules_cfg(long_flush={"confirm_rounds": 1})
        self.assertIsNotNone(self.hit(cfg=one, rates=(-0.00003,)))

    def test_gap_too_long_is_unconfirmed(self):
        s = flush_series(rates=(-0.00003,))
        s["RT"][T0 - 4 * STEP] = (-0.00003, 100.0, 1000.0, 8)          # 20 分钟前：超出 N+1 个桶
        self.assertIsNone(R.long_flush(s, T0, CFG))

    def test_g_drop_boundary(self):
        self.assertIsNotNone(self.hit(g_now=2.24, g_24h=2.5))           # −10.4%
        self.assertIsNone(self.hit(g_now=2.26, g_24h=2.5))
        self.assertIsNone(self.hit(g_now=3.0, g_24h=2.5))               # 账户比在升

    def test_missing_data_or_disabled(self):
        s = flush_series()
        del s["G"][T0 - 24 * H]
        self.assertIsNone(R.long_flush(s, T0, CFG))
        s = flush_series(rates=())
        self.assertIsNone(R.long_flush(s, T0, CFG))
        self.assertIsNone(self.hit(cfg=helpers.rules_cfg(long_flush={"enabled": False})))

    def test_rt_bucket_lagging_g_within_10min(self):
        """G 收盘点 = T0，但实时样本只到 T0−5m（本轮还没采）：就近取 10 分钟内的 RT 桶。"""
        s = flush_series(end=T0)
        rt = dict(s["RT"])
        s["RT"].clear()
        for ts, v in rt.items():
            s["RT"][ts - STEP] = v
        self.assertIsNotNone(R.long_flush(s, T0, CFG))


class TestEngineAndCard(unittest.TestCase):
    def test_cooldown_12h(self):
        s = flush_series()
        for k in range(1, 4):
            s["RT"][T0 + k * STEP] = (-0.00003, 100.0, 1000.0, 8)
            s["G"][T0 + k * STEP] = (2.0, 2.0 / 3)
        state = engine.new_state()
        found, _ = engine.step({"ETH": s}, state, CFG, T0, catchup_s=STEP)
        self.assertEqual([c["rule"] for c in found], ["long_flush"])
        again, _ = engine.step({"ETH": s}, state, CFG, T0 + 3 * STEP, catchup_s=3 * STEP)
        self.assertEqual(again, [])

    def test_card(self):
        s = flush_series()
        found, _ = engine.step({"ETH": s}, engine.new_state(), CFG, T0, catchup_s=STEP)
        card = found[0]
        ctx = cards.context(s, T0, -0.00003)
        text = cards.format_card(card, ctx, {"n": 0, "enough": False}, job="com.crypto-research.x")
        self.assertIn("多头出清", text)
        self.assertIn("资金费率转负（−0.0030%），24h 内账户多空比下降 20%", text)
        self.assertIn("连续 2 轮 ≤ −0.0020%", text)
        self.assertIn("账户多空比 2.00（24h 前 2.50，−20%）", text)
        self.assertIn("不代表已经见底", text)
        self.assertIn("历史参照", text)
        self.assertIn("口径：Binance USD-M 单所 · 5m 数据", text)
        self.assertLessEqual(cards.visible_len(text), cards.MAX_CHARS)
        for word in FORBIDDEN:
            self.assertNotIn(word, text)


class TestBacktest(unittest.TestCase):
    def test_settlement_records_use_one_round(self):
        s = helpers.flat_series(T0, 400, g=2.5, oi=1000.0, px=100.0)
        s["G"][T0] = (2.0, 2.0 / 3)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "ETHUSDT_fundingRate.json"), "w") as fh:
                json.dump([{"fundingTime": T0 * 1000 + 3, "fundingRate": "-0.00005"}], fh)
            backtest.attach_rt({"ETH": s}, {"ETH": "ETHUSDT"}, d)
        live, _, _ = backtest.replay({"ETH": s}, CFG, start=T0 - H, end=T0)
        self.assertNotIn("long_flush", [c["rule"] for c in live])          # 线上口径：结算值只 1 个样本
        bt_cfg = backtest.backtest_cfg(CFG)
        found, _, _ = backtest.replay({"ETH": s}, bt_cfg, start=T0 - H, end=T0)
        self.assertIn("long_flush", [c["rule"] for c in found])
        self.assertEqual(CFG["rules"]["long_flush"]["confirm_rounds"], 2)   # 不改原配置


class TestLiveConfig(unittest.TestCase):
    def test_rules_yaml_has_long_flush(self):
        rc = common.load_config("rules.yaml")["rules"]["long_flush"]
        self.assertTrue(rc["enabled"])
        self.assertEqual((rc["severity"], rc["funding_max_pct"], rc["confirm_rounds"], rc["g_drop_24h_pct"],
                          rc["cooldown_hours"]), ("P1", -0.002, 2, 10.0, 12))


if __name__ == "__main__":
    unittest.main()
