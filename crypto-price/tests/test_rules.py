#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rules.py 的离线单测：八类规则边界 / P0-P1 / 多窗口合并 / 数据质量阻断 /
首轮不报新极值 / level_break 用 last_prices / advance_state 不改入参且 OI 保留 24h。

运行：cd crypto-price && ../.venv/bin/python -m unittest tests.test_rules -v
"""
import copy
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import rules  # noqa: E402

import common  # noqa: E402

NOW = 1794096000
WINDOWS = {"m1": 60, "m5": 300, "m15": 900, "h1": 3600}
RULES_CFG = common.load_config("rules.yaml")
WATCHLIST = common.load_config("watchlist.yaml")


# ---------------------------------------------------------------- 构造工具
def candle(close_time, close, volume=10.0):
    return {"open_time": close_time - 59, "open": close, "high": close * 1.001,
            "low": close * 0.999, "close": close, "volume": volume, "close_time": close_time}


def fast_candles(refs):
    """按窗口锚点造 K 线：{"m5": 100.0} → close_time = NOW-300 的参照 K 线。"""
    return [candle(NOW - WINDOWS[name], close) for name, close in refs.items()]


def volume_candles(baseline_volume=10.0, recent_volume=30.0, baseline_close=100.0):
    """60 根已完成 1m K 线：前 55 根基线量，最近 5 根为放量窗口。"""
    rows = [candle(NOW - (3601 - index * 60), baseline_close, baseline_volume) for index in range(55)]
    rows += [candle(NOW - (301 - index * 60), baseline_close, recent_volume) for index in range(5)]
    return rows


def quote(price, n_sources=3, spread_bps=2.0, source_ts=NOW):
    return {"price": price, "spread_bps": spread_bps, "n_sources": n_sources, "agree": True,
            "sources": {"binance": {"price": price, "ts": source_ts},
                        "okx": {"price": price, "ts": source_ts},
                        "coinbase": {"price": price, "ts": source_ts}}}


def snapshot(quotes=None, stats=None, candles=None, funding=None, oi=None, errors=None,
             sources_ok=9, sources_total=9, now_ts=NOW):
    base_quotes = {"BTC": quote(100.0), "ETH": quote(100.0), "BNB": quote(100.0)}
    base_quotes.update(quotes or {})
    return {"ts_epoch": now_ts, "quotes": base_quotes, "stats": stats or {},
            "candles": candles or {}, "funding": funding or {}, "oi": oi or {},
            "errors": errors or [], "sources_ok": sources_ok, "sources_total": sources_total}


class RuleTestCase(unittest.TestCase):
    """公共夹具：默认行情无任何事件，各用例只改需要触发的字段。"""

    def setUp(self):
        self.rules = copy.deepcopy(RULES_CFG)
        self.watchlist = WATCHLIST

    def evaluate(self, snap, state=None, now=NOW, rules_cfg=None):
        return rules.evaluate(snap, state or {}, rules_cfg or self.rules, self.watchlist, now=now)

    def by_rule(self, events, rule):
        return [event for event in events if event["rule"] == rule]

    def one(self, events, rule):
        matched = self.by_rule(events, rule)
        self.assertEqual(len(matched), 1, "期望恰好 1 条 %s，实际 %d 条" % (rule, len(matched)))
        return matched[0]

    def assert_no_rule(self, events, rule):
        self.assertEqual(self.by_rule(events, rule), [], "不应触发 %s" % rule)


# ---------------------------------------------------------------- 事件契约 / 基线
class TestEventContract(RuleTestCase):
    def test_quiet_market_produces_no_event(self):
        self.assertEqual(self.evaluate(snapshot()), [])

    def test_event_fields_follow_contract(self):
        snap = snapshot(quotes={"BTC": quote(101.0)}, candles={"BTC": {"rows": fast_candles({"m5": 100.0})}})
        event = self.one(self.evaluate(snap), "fast_move")
        for field in ("rule", "asset", "direction", "severity", "title", "detail", "metrics",
                      "dedupe_key", "magnitude", "urls", "why", "action", "force_send", "ts"):
            self.assertIn(field, event)
        self.assertEqual(event["rule"], "fast_move")
        self.assertEqual(event["asset"], "BTC")
        self.assertEqual(event["direction"], "up")
        self.assertIn(event["severity"], ("P0", "P1", "P2"))
        self.assertIsInstance(event["detail"], list)
        self.assertIsInstance(event["metrics"], dict)
        self.assertIsInstance(event["urls"], list)
        self.assertIsInstance(event["force_send"], bool)
        self.assertEqual(event["dedupe_key"], "price:BTC:fast_move:up")
        self.assertEqual(event["ts"], datetime.datetime.fromtimestamp(NOW).isoformat(timespec="seconds"))
        self.assertIn("BTC", event["title"])
        self.assertTrue(event["why"] and event["action"])
        self.assertTrue(event["urls"][0].startswith("https://"))

    def test_evaluate_does_not_mutate_inputs(self):
        state = {"extremes": {"BTC": {"high": 105.0, "low": 95.0}},
                 "last_prices": {"BTC": {"price": 99.0}},
                 "oi_history": {"BTC": [{"ts": NOW - 900, "oi": 100.0}]}}
        snap = snapshot(quotes={"BTC": quote(101.0)}, candles={"BTC": {"rows": fast_candles({"m5": 100.0})}})
        state_before, snap_before = copy.deepcopy(state), copy.deepcopy(snap)
        self.evaluate(snap, state)
        self.assertEqual(state, state_before)
        self.assertEqual(snap, snap_before)


# ---------------------------------------------------------------- fast_move
class TestFastMove(RuleTestCase):
    def test_below_threshold_no_event(self):
        snap = snapshot(quotes={"BTC": quote(100.5)}, candles={"BTC": {"rows": fast_candles({"m5": 100.0})}})
        self.assert_no_rule(self.evaluate(snap), "fast_move")

    def test_threshold_boundary_triggers_p1(self):
        snap = snapshot(quotes={"BTC": quote(101.0)}, candles={"BTC": {"rows": fast_candles({"m5": 100.0})}})
        event = self.one(self.evaluate(snap), "fast_move")
        self.assertEqual(event["severity"], "P1")
        self.assertEqual(event["direction"], "up")
        self.assertAlmostEqual(event["metrics"]["change_pct"], 1.0, places=2)

    def test_p0_floor(self):
        # BTC h1 阈值 2.0%，P0 = max(2.0*2, 3.0) = 4.0%；-4.2% → P0。
        snap = snapshot(quotes={"BTC": quote(95.8)}, candles={"BTC": {"rows": fast_candles({"h1": 100.0})}})
        event = self.one(self.evaluate(snap), "fast_move")
        self.assertEqual(event["severity"], "P0")
        self.assertEqual(event["direction"], "down")
        self.assertEqual(event["dedupe_key"], "price:BTC:fast_move:down")

    def test_multiple_windows_merge_into_one_and_prefer_p0(self):
        # m1 -2%（P1，超阈值倍数高）与 h1 -4.2%（P0）同轮：只出 1 条，且取 P0 的 h1。
        snap = snapshot(quotes={"BTC": quote(95.8)},
                        candles={"BTC": {"rows": fast_candles({"m1": 97.7, "m5": 97.7, "m15": 97.7, "h1": 100.0})}})
        events = self.evaluate(snap)
        event = self.one(events, "fast_move")
        self.assertEqual(event["severity"], "P0")
        self.assertEqual(event["metrics"]["window"], "h1")
        self.assertAlmostEqual(event["metrics"]["change_pct"], -4.2, places=1)

    def test_disabled_in_config(self):
        self.rules["rules"]["fast_move"]["enabled"] = False
        snap = snapshot(quotes={"BTC": quote(101.0)}, candles={"BTC": {"rows": fast_candles({"m5": 100.0})}})
        self.assert_no_rule(self.evaluate(snap), "fast_move")


# ---------------------------------------------------------------- day_move
class TestDayMove(RuleTestCase):
    def day_stats(self, change):
        return {"BTC": {"change_pct": change, "high": 110.0, "low": 90.0, "open": 100.0}}

    def test_below_threshold(self):
        self.assert_no_rule(self.evaluate(snapshot(stats=self.day_stats(-4.9))), "day_move")

    def test_p1_boundary(self):
        event = self.one(self.evaluate(snapshot(stats=self.day_stats(5.0))), "day_move")
        self.assertEqual(event["severity"], "P1")
        self.assertEqual(event["direction"], "up")

    def test_p0_boundary(self):
        event = self.one(self.evaluate(snapshot(stats=self.day_stats(-10.0))), "day_move")
        self.assertEqual(event["severity"], "P0")
        self.assertEqual(event["direction"], "down")
        self.assertEqual(event["dedupe_key"], "price:BTC:day_move:down")


# ---------------------------------------------------------------- new_extreme
class TestNewExtreme(RuleTestCase):
    def state(self, high=None, low=None):
        extremes = {}
        if high is not None and low is not None:
            extremes["BTC"] = {"high": high, "low": low, "ts": NOW - 60}
        return {"extremes": extremes}

    def test_first_round_initializes_without_event(self):
        snap = snapshot(quotes={"BTC": quote(100.06)}, stats={"BTC": {"high": 100.0, "low": 99.0}})
        self.assertEqual(self.evaluate(snap, {}), [])
        new_state = rules.advance_state({}, snap, now=NOW)
        self.assertEqual(new_state["extremes"]["BTC"]["high"], 100.06)
        self.assertEqual(new_state["extremes"]["BTC"]["low"], 99.0)

    def test_min_bps_filter(self):
        snap = snapshot(quotes={"BTC": quote(100.02)})
        self.assert_no_rule(self.evaluate(snap, self.state(100.0, 99.0)), "new_extreme")

    def test_new_high_p1(self):
        snap = snapshot(quotes={"BTC": quote(100.06)})
        event = self.one(self.evaluate(snap, self.state(100.0, 99.0)), "new_extreme")
        self.assertEqual(event["severity"], "P1")
        self.assertEqual(event["direction"], "up")
        self.assertEqual(event["dedupe_key"], "price:BTC:new_extreme:up")
        self.assertAlmostEqual(event["metrics"]["exceed_bps"], 6.0, places=1)

    def test_new_high_p0_requires_same_direction_h1(self):
        snap = snapshot(quotes={"BTC": quote(103.1)},
                        candles={"BTC": {"rows": fast_candles({"h1": 100.0})}})
        event = self.one(self.evaluate(snap, self.state(103.0, 99.0)), "new_extreme")
        self.assertEqual(event["severity"], "P0")
        self.assertEqual(event["direction"], "up")

    def test_opposite_h1_keeps_p1(self):
        snap = snapshot(quotes={"BTC": quote(103.1)},
                        candles={"BTC": {"rows": fast_candles({"h1": 110.0})}})
        event = self.one(self.evaluate(snap, self.state(103.0, 99.0)), "new_extreme")
        self.assertEqual(event["severity"], "P1")

    def test_new_low(self):
        snap = snapshot(quotes={"BTC": quote(98.9)})
        event = self.one(self.evaluate(snap, self.state(105.0, 99.0)), "new_extreme")
        self.assertEqual(event["direction"], "down")
        self.assertEqual(event["dedupe_key"], "price:BTC:new_extreme:down")


# ---------------------------------------------------------------- volume_spike
class TestVolumeSpike(RuleTestCase):
    def test_p1_boundary(self):
        snap = snapshot(quotes={"BTC": quote(101.0)}, candles={"BTC": {"rows": volume_candles()}})
        event = self.one(self.evaluate(snap), "volume_spike")
        self.assertEqual(event["severity"], "P1")
        self.assertAlmostEqual(event["metrics"]["ratio"], 3.0, places=2)
        self.assertEqual(event["direction"], "up")

    def test_p0(self):
        snap = snapshot(quotes={"BTC": quote(101.0)},
                        candles={"BTC": {"rows": volume_candles(recent_volume=60.0)}})
        event = self.one(self.evaluate(snap), "volume_spike")
        self.assertEqual(event["severity"], "P0")
        self.assertAlmostEqual(event["metrics"]["ratio"], 6.0, places=2)

    def test_needs_price_move(self):
        snap = snapshot(quotes={"BTC": quote(100.0)},
                        candles={"BTC": {"rows": volume_candles(recent_volume=60.0)}})
        self.assert_no_rule(self.evaluate(snap), "volume_spike")

    def test_move_below_min(self):
        snap = snapshot(quotes={"BTC": quote(100.3)}, candles={"BTC": {"rows": volume_candles()}})
        self.assert_no_rule(self.evaluate(snap), "volume_spike")

    def test_insufficient_candles(self):
        rows = volume_candles()[:30]
        snap = snapshot(quotes={"BTC": quote(101.0)}, candles={"BTC": {"rows": rows}})
        self.assert_no_rule(self.evaluate(snap), "volume_spike")


# ---------------------------------------------------------------- funding
class TestFunding(RuleTestCase):
    def funding_snap(self, rate):
        return snapshot(funding={"BTC": {"rate": rate, "mark_price": 100.0, "ts": NOW}})

    def test_below_threshold(self):
        self.assert_no_rule(self.evaluate(self.funding_snap(0.0009)), "funding")

    def test_p1_boundary(self):
        event = self.one(self.evaluate(self.funding_snap(0.001)), "funding")
        self.assertEqual(event["severity"], "P1")
        self.assertEqual(event["direction"], "up")
        self.assertEqual(event["dedupe_key"], "price:BTC:funding:up")

    def test_p0_positive(self):
        event = self.one(self.evaluate(self.funding_snap(0.002)), "funding")
        self.assertEqual(event["severity"], "P0")

    def test_p0_negative(self):
        event = self.one(self.evaluate(self.funding_snap(-0.0025)), "funding")
        self.assertEqual(event["severity"], "P0")
        self.assertEqual(event["direction"], "down")
        self.assertEqual(event["dedupe_key"], "price:BTC:funding:down")

    def test_survives_bad_data_quality(self):
        snap = self.funding_snap(0.002)
        snap["quotes"]["BTC"]["spread_bps"] = 60.0
        snap["candles"] = {"BTC": {"rows": fast_candles({"m5": 10.0})}}  # 价格类规则本会触发
        events = self.evaluate(snap)
        self.assert_no_rule(events, "fast_move")
        self.one(events, "funding")
        self.one(events, "data_quality")


# ---------------------------------------------------------------- open_interest
class TestOpenInterest(RuleTestCase):
    def oi_snap(self, price, oi):
        return snapshot(quotes={"BTC": quote(price)},
                        candles={"BTC": {"rows": fast_candles({"m15": 100.0})}},
                        oi={"BTC": {"oi": oi, "ts": NOW}})

    def oi_state(self, oi=100.0):
        return {"oi_history": {"BTC": [{"ts": NOW - 900, "oi": oi}]}}

    def test_p1_boundary_same_direction(self):
        # 15m 价格 +1%（同向）+ OI +5% → P1。
        event = self.one(self.evaluate(self.oi_snap(101.0, 105.0), self.oi_state()), "open_interest")
        self.assertEqual(event["severity"], "P1")
        self.assertEqual(event["direction"], "up")
        self.assertAlmostEqual(event["metrics"]["oi_change_pct"], 5.0, places=2)
        self.assertEqual(event["dedupe_key"], "price:BTC:open_interest:up")

    def test_p0(self):
        event = self.one(self.evaluate(self.oi_snap(101.0, 110.0), self.oi_state()), "open_interest")
        self.assertEqual(event["severity"], "P0")

    def test_requires_same_direction_price_move(self):
        # OI 增加但价格下跌 → 不同向，不报。
        events = self.evaluate(self.oi_snap(99.0, 110.0), self.oi_state())
        self.assert_no_rule(events, "open_interest")

    def test_price_move_below_min(self):
        events = self.evaluate(self.oi_snap(100.4, 110.0), self.oi_state())
        self.assert_no_rule(events, "open_interest")

    def test_no_history(self):
        events = self.evaluate(self.oi_snap(101.0, 110.0), {})
        self.assert_no_rule(events, "open_interest")


# ---------------------------------------------------------------- level_break
class TestLevelBreak(RuleTestCase):
    def eth_snap(self, price):
        return snapshot(quotes={"ETH": quote(price)})

    def state(self, price=2490.0):
        return {"last_prices": {"ETH": {"price": price, "ts": NOW - 60}}}

    def test_break_up_confirmed(self):
        event = self.one(self.evaluate(self.eth_snap(2512.3), self.state()), "level_break")
        self.assertEqual(event["direction"], "up")
        self.assertEqual(event["severity"], "P1")
        self.assertEqual(event["dedupe_key"], "price:ETH:level:2500:up")
        self.assertIn("2,500", event["title"])

    def test_break_up_not_confirmed(self):
        self.assert_no_rule(self.evaluate(self.eth_snap(2500.5), self.state()), "level_break")

    def test_break_down_confirmed(self):
        event = self.one(self.evaluate(self.eth_snap(2488.1), self.state(2510.0)), "level_break")
        self.assertEqual(event["direction"], "down")
        self.assertEqual(event["dedupe_key"], "price:ETH:level:2500:down")
        self.assertIn("USDT", event["title"])
        detail = "\n".join(event["detail"])
        self.assertIn("确认幅度", detail)
        self.assertIn("0.476%", detail)
        self.assertIn("较关口低", detail)
        self.assertIn("USDT", detail)

    def test_first_round_uses_no_last_price(self):
        self.assert_no_rule(self.evaluate(self.eth_snap(2488.1), {}), "level_break")


# ---------------------------------------------------------------- 数据质量
class TestDataQuality(RuleTestCase):
    def test_spread_blocks_price_rules(self):
        snap = snapshot(quotes={"BTC": quote(101.0, spread_bps=51.0)},
                        candles={"BTC": {"rows": fast_candles({"m5": 100.0})}})
        events = self.evaluate(snap)
        dq = self.one(events, "data_quality")
        self.assertEqual(dq["severity"], "P1")
        self.assertEqual(dq["dedupe_key"], "price:BTC:data_quality:flat")
        self.assert_no_rule(events, "fast_move")

    def test_spread_at_limit_is_ok(self):
        snap = snapshot(quotes={"BTC": quote(101.0, spread_bps=50.0)},
                        candles={"BTC": {"rows": fast_candles({"m5": 100.0})}})
        events = self.evaluate(snap)
        self.assert_no_rule(events, "data_quality")
        self.one(events, "fast_move")

    def test_too_few_sources(self):
        # 现在允许单主源（min_sources=1），0 个主源才算数据质量异常
        snap = snapshot(quotes={"BTC": quote(101.0, n_sources=0)})
        self.one(self.evaluate(snap), "data_quality")

    def test_stale_quotes(self):
        snap = snapshot(quotes={"BTC": quote(101.0, source_ts=NOW - 400)})
        self.one(self.evaluate(snap), "data_quality")

    def test_multi_asset_same_reason_aggregated_into_one(self):
        snap = snapshot(quotes={
            "BTC": quote(101.0, n_sources=0),
            "ETH": quote(2237.0, n_sources=0),
            "BNB": quote(696.0, n_sources=0),
        })
        events = self.evaluate(snap)
        dqs = [e for e in events if e["rule"] == "data_quality"]
        self.assertEqual(len(dqs), 1, dqs)
        self.assertEqual(dqs[0]["asset"], "BTC/ETH/BNB")
        self.assertIn("受影响", "".join(dqs[0]["detail"]))
        self.assertTrue(dqs[0]["dedupe_key"].startswith("price:data_quality:multi:"))

    def test_multi_asset_different_reasons_aggregated_into_one(self):
        # 三个资产原因不同（价差/过期/无报价）也只发一条，避免三连刷屏
        snap = snapshot(quotes={
            "BTC": quote(101.0, spread_bps=60.0),
            "ETH": quote(2237.0, source_ts=NOW - 400),
            "BNB": quote(0),
        })
        events = self.evaluate(snap)
        dqs = [e for e in events if e["rule"] == "data_quality"]
        self.assertEqual(len(dqs), 1, dqs)
        self.assertEqual(dqs[0]["asset"], "BTC/ETH/BNB")
        self.assertTrue(dqs[0]["dedupe_key"].startswith("price:data_quality:multi:"))
        joined = " ".join(dqs[0]["detail"])
        self.assertIn("受影响", joined)
        for asset in ("BTC", "ETH", "BNB"):
            self.assertIn(asset, joined)

    def test_all_sources_down_is_p0_force_send(self):
        snap = snapshot(quotes={}, funding={"BTC": {"rate": 0.002, "mark_price": 100.0, "ts": NOW}},
                        errors=[{"source": "binance", "error": "Timeout"}],
                        sources_ok=0, sources_total=9)
        events = self.evaluate(snap)
        dq = self.one(events, "data_quality")
        self.assertEqual(dq["severity"], "P0")
        self.assertTrue(dq["force_send"])
        self.assertEqual(dq["asset"], "ALL")
        self.assertEqual(dq["dedupe_key"], "price:ALL:data_quality:flat")
        self.assert_no_rule(events, "fast_move")
        # 全源不可用时仍评估衍生品规则。
        self.one(events, "funding")


# ---------------------------------------------------------------- advance_state
class TestAdvanceState(unittest.TestCase):
    def test_updates_copy_without_mutating_input(self):
        state = {
            "extremes": {"BTC": {"high": 105.0, "low": 95.0, "ts": NOW - 60}},
            "last_prices": {"BTC": {"price": 99.0, "ts": NOW - 60}},
            "oi_history": {"BTC": [{"ts": NOW - 90000, "oi": 1.0},   # 25h 前，应被裁掉
                                    {"ts": NOW - 3600, "oi": 2.0}]},
            "extra": {"keep": True},
        }
        state_before = copy.deepcopy(state)
        snap = snapshot(stats={"BTC": {"high": 106.0, "low": 97.0}},
                        oi={"BTC": {"oi": 3.0, "ts": NOW}})
        new_state = rules.advance_state(state, snap, now=NOW)
        self.assertEqual(state, state_before, "advance_state 不得修改入参")
        self.assertIsNot(new_state, state)
        self.assertEqual(new_state["last_prices"]["BTC"], {"price": 100.0, "ts": NOW})
        self.assertEqual(new_state["extremes"]["BTC"], {"high": 106.0, "low": 97.0, "ts": NOW})
        self.assertEqual(new_state["oi_history"]["BTC"],
                         [{"ts": NOW - 3600, "oi": 2.0}, {"ts": NOW, "oi": 3.0}])
        self.assertEqual(new_state["extra"], {"keep": True})

    def test_first_round_initializes_from_stats(self):
        snap = snapshot(stats={"BTC": {"high": 106.0, "low": 97.0}},
                        oi={"BTC": {"oi": 3.0, "ts": NOW}})
        new_state = rules.advance_state({}, snap, now=NOW)
        self.assertEqual(sorted(new_state), ["extremes", "last_prices", "oi_history"])
        self.assertEqual(new_state["extremes"]["BTC"]["high"], 106.0)
        self.assertEqual(new_state["last_prices"]["BTC"]["price"], 100.0)
        self.assertEqual(len(new_state["oi_history"]["BTC"]), 1)

    def test_extreme_fallback_without_stats(self):
        state = {"extremes": {"BTC": {"high": 105.0, "low": 95.0}}}
        new_state = rules.advance_state(state, snapshot(quotes={"BTC": quote(110.0)}), now=NOW)
        self.assertEqual(new_state["extremes"]["BTC"]["high"], 110.0)
        self.assertEqual(new_state["extremes"]["BTC"]["low"], 95.0)

    def test_oi_same_timestamp_replaced_not_duplicated(self):
        state = {"oi_history": {"BTC": [{"ts": NOW, "oi": 1.0}]}}
        new_state = rules.advance_state(state, snapshot(oi={"BTC": {"oi": 2.0, "ts": NOW}}), now=NOW)
        self.assertEqual(new_state["oi_history"]["BTC"], [{"ts": NOW, "oi": 2.0}])

    def test_oi_history_pruned_to_24h(self):
        state = {"oi_history": {"BTC": [{"ts": NOW - 86400, "oi": 1.0}, {"ts": NOW - 900, "oi": 2.0}]}}
        new_state = rules.advance_state(state, snapshot(oi={"BTC": {"oi": 3.0, "ts": NOW}}), now=NOW)
        timestamps = [row["ts"] for row in new_state["oi_history"]["BTC"]]
        self.assertEqual(timestamps, [NOW - 86400, NOW - 900, NOW])  # 恰好 24h 的样本保留
        self.assertNotIn(False, [ts >= NOW - 86400 for ts in timestamps])


if __name__ == "__main__":
    unittest.main(verbosity=2)
