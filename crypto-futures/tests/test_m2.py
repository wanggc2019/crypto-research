#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M2：funding_extreme / oi_spike（从 crypto-price 迁入）的规则边界、治理、卡片、采集与回测。

docs/crypto-futures/requirement-m2.md §3、§4。
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
import outputs  # noqa: E402
import reference  # noqa: E402
import rules as R  # noqa: E402
import run  # noqa: E402
import series as S  # noqa: E402
from helpers import STEP, T0  # noqa: E402
from sources import Fetcher  # noqa: E402

import common  # noqa: E402

CFG = helpers.rules_cfg()
FORBIDDEN = ("多源核验", "价格异动", "开多", "抄底", "减仓")
H = 3600


def rt(points):
    """{ts: (rate, mark, oi, hours)} → 只含 RT 的序列。"""
    s = S.empty()
    s["RT"].update(points)
    return s


def oi_series(oi_now, px_now, back=900, oi_ref=1000.0, px_ref=100.0):
    return rt({T0 - back: (None, px_ref, oi_ref, 8), T0: (None, px_now, oi_now, 8)})


# ---------------------------------------------------------------- funding_extreme
class TestFundingExtreme(unittest.TestCase):
    def hit(self, rate, hours=8, cfg=CFG):
        return R.funding_extreme(rt({T0: (rate, 100.0, 1000.0, hours)}), T0, cfg)

    def test_below_p1(self):
        self.assertIsNone(self.hit(0.000999))

    def test_p1_boundary_long(self):
        h = self.hit(0.001)
        self.assertEqual((h["severity"], h["direction"]), ("P1", "long"))
        self.assertAlmostEqual(h["magnitude"], 0.1)

    def test_p0_both_signs(self):
        self.assertEqual(self.hit(0.002)["severity"], "P0")
        h = self.hit(-0.0025)
        self.assertEqual((h["severity"], h["direction"]), ("P0", "short"))

    def test_annualized_uses_interval(self):
        m = self.hit(0.001, hours=4)["metrics"]
        self.assertTrue(m["interval_known"])
        self.assertAlmostEqual(m["annualized_pct"], 0.1 * 6 * 365)

    def test_unknown_interval_falls_back_to_8h(self):
        m = self.hit(0.001, hours=None)["metrics"]
        self.assertFalse(m["interval_known"])
        self.assertEqual(m["interval_hours"], 8)
        self.assertAlmostEqual(m["annualized_pct"], 0.1 * 3 * 365)

    def test_missing_rate_or_disabled(self):
        self.assertIsNone(self.hit(None))
        self.assertIsNone(self.hit(0.003, cfg=helpers.rules_cfg(funding_extreme={"enabled": False})))


# ---------------------------------------------------------------- oi_spike
class TestOiSpike(unittest.TestCase):
    def test_p1_up_same_direction(self):
        h = R.oi_spike(oi_series(1050.0, 101.0), T0, CFG)
        self.assertEqual((h["severity"], h["direction"]), ("P1", "up"))
        self.assertEqual(h["metrics"]["window_min"], 15)
        self.assertAlmostEqual(h["metrics"]["oi_change_pct"], 5.0)

    def test_p0_and_down(self):
        self.assertEqual(R.oi_spike(oi_series(1100.0, 101.0), T0, CFG)["severity"], "P0")
        h = R.oi_spike(oi_series(940.0, 99.0), T0, CFG)
        self.assertEqual((h["severity"], h["direction"]), ("P1", "down"))

    def test_no_trigger_cases(self):
        self.assertIsNone(R.oi_spike(oi_series(1049.0, 101.0), T0, CFG))   # OI 不到 5%
        self.assertIsNone(R.oi_spike(oi_series(1100.0, 99.0), T0, CFG))    # 价格反向
        self.assertIsNone(R.oi_spike(oi_series(1100.0, 100.4), T0, CFG))   # 价格不到 0.5%
        self.assertIsNone(R.oi_spike(oi_series(1100.0, 101.0),
                                     T0, helpers.rules_cfg(oi_spike={"enabled": False})))

    def test_falls_back_to_20min_bucket(self):
        h = R.oi_spike(oi_series(1060.0, 101.0, back=1200), T0, CFG)
        self.assertEqual(h["metrics"]["window_min"], 20)

    def test_older_than_20min_is_missing(self):
        """休眠空档：只有 −25 分钟的点 → 视为缺数据，不把 25 分钟的变化当 15 分钟触发。"""
        self.assertIsNone(R.oi_spike(oi_series(1100.0, 101.0, back=1500), T0, CFG))

    def test_oi_and_price_share_reference_bucket(self):
        """−15 分钟桶缺标记价时整桶不用，OI 与价格一起退到 −20 分钟桶。"""
        s = rt({T0 - 1200: (None, 100.0, 1000.0, 8), T0 - 900: (None, None, 1040.0, 8),
                T0: (None, 101.0, 1060.0, 8)})
        h = R.oi_spike(s, T0, CFG)
        self.assertEqual(h["metrics"]["window_min"], 20)
        self.assertAlmostEqual(h["metrics"]["oi_change_pct"], 6.0)


# ---------------------------------------------------------------- 治理（与 M1 共用）
class TestGovernance(unittest.TestCase):
    def test_merged_with_m1_hit_on_same_point(self):
        s = helpers.flat_series(T0, 20, g=2.0)
        s["G"][T0] = (2.42, 0.7)                       # retail_shift
        s["RT"][T0] = (0.0012, 100.0, 1000.0, 8)       # funding_extreme
        found, _ = engine.step({"BTC": s}, engine.new_state(), CFG, T0, catchup_s=STEP)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["rule"], "retail_shift")
        self.assertEqual(found[0]["also"], ["funding_extreme"])

    def test_p0_daily_cap_shared_with_m1(self):
        state = engine.new_state()
        state["p0"]["BTC|%s" % common.local_date(T0)] = 2
        p0 = {"rule": "funding_extreme", "severity": "P0", "direction": "long", "magnitude": 0.3, "metrics": {}}
        self.assertIsNone(engine._admit(p0, "BTC", T0, state, CFG))

    def test_cooldown_from_rule_config(self):
        state = engine.new_state()
        h = {"rule": "funding_extreme", "severity": "P1", "direction": "long", "magnitude": 0.12, "metrics": {}}
        self.assertIsNotNone(engine._admit(dict(h), "BTC", T0, state, CFG))
        self.assertIsNone(engine._admit(dict(h), "BTC", T0 + H, state, CFG))
        self.assertIsNotNone(engine._admit(dict(h), "BTC", T0 + 2 * H, state, CFG))

    def test_stale_rt_stops_only_rt_rules(self):
        s = rt({T0: (0.003, 100.0, 1000.0, 8)})
        found, _ = engine.step({"BTC": s}, engine.new_state(), CFG, T0, stale={"BTC": {"RT"}}, catchup_s=STEP)
        self.assertEqual(found, [])
        found, _ = engine.step({"BTC": s}, engine.new_state(), CFG, T0, catchup_s=STEP)
        self.assertEqual(found[0]["rule"], "funding_extreme")


# ---------------------------------------------------------------- 卡片
class TestCards(unittest.TestCase):
    def render(self, s, ts, funding=0.0012):
        found, _ = engine.step({"BTC": s}, engine.new_state(), CFG, ts, catchup_s=STEP)
        card = found[0]
        ctx = cards.context(s, ts, funding, realtime=card["rule"] in cards.RT_RULES)
        return card, ctx, cards.format_card(card, ctx, {"n": 0, "enough": False}, job="com.crypto-research.x")

    def base(self):
        """M1 指标只收盘到 T0−5m（当前桶未收盘），RT 落在 T0。"""
        return helpers.flat_series(T0 - STEP, 300, oi=1000.0, px=100.0)

    def check_common(self, text):
        self.assertLessEqual(cards.visible_len(text), cards.MAX_CHARS)
        for word in FORBIDDEN:
            self.assertNotIn(word, text)
        self.assertIn("实时快照", text)
        self.assertIn("背景：合约价 100.0", text)          # 当前桶未收盘时就近取 20 分钟内的已收盘点
        self.assertIn("历史参照", text)

    def test_funding_card(self):
        s = self.base()
        s["RT"][T0] = (0.0012, 100.0, 1000.0, 4)
        card, _, text = self.render(s, T0)
        self.assertIn("资金费率极端", text)
        self.assertIn("多头向空头付费", text)
        self.assertIn("每 4h 结算", text)
        self.assertIn("折算年化 +263%", text)
        self.check_common(text)

    def test_funding_card_unknown_interval(self):
        s = self.base()
        s["RT"][T0] = (-0.0012, 100.0, 1000.0, None)
        _, _, text = self.render(s, T0)
        self.assertIn("按 8h 折算", text)
        self.assertIn("空头向多头付费", text)

    def test_oi_card(self):
        s = self.base()
        s["RT"][T0 - 900] = (0.0001, 100.0, 1000.0, 8)
        s["RT"][T0] = (0.0001, 101.0, 1100.0, 8)
        card, _, text = self.render(s, T0, funding=0.0001)
        self.assertEqual(card["severity"], "P0")
        self.assertIn("持仓量急变", text)
        self.assertIn("15 分钟内 OI 增加 10.0%，合约价同步上涨 1.00%", text)
        self.assertIn("1,000 → 1,100 张", text)
        self.check_common(text)


# ---------------------------------------------------------------- 采集：RT 样本与结算间隔
class FakeFetcher:
    def __init__(self, info):
        self.info, self.calls = info, 0

    def funding_info(self):
        self.calls += 1
        return self.info


class TestSampling(unittest.TestCase):
    APP = {"assets": {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}}

    def st(self):
        return {"series": {a: S.empty() for a in self.APP["assets"]}, "meta": {}}

    def test_sample_rt_bucket_and_default_interval(self):
        st = self.st()
        now = T0 + 137
        run.sample_rt(st, self.APP, {"BTC": {"rate": 0.0001, "mark_price": "100.5"}},
                      {"BTC": {"oi": 1000.0}}, {"ETHUSDT": 4}, now)
        self.assertEqual(st["series"]["BTC"]["RT"], {T0: (0.0001, 100.5, 1000.0, 8)})
        self.assertEqual(st["series"]["ETH"]["RT"], {})       # 本轮两个接口都没取到 → 不写点

    def test_sample_rt_unknown_interval(self):
        st = self.st()
        run.sample_rt(st, self.APP, {}, {"BTC": {"oi": 1.0}}, None, T0)
        self.assertEqual(st["series"]["BTC"]["RT"][T0], (None, None, 1.0, None))

    def test_funding_info_daily_cache(self):
        st, f = self.st(), FakeFetcher({"BTCUSDT": 4})
        calm = {"BTC": {"rate": 0.0001}}
        self.assertEqual(run.funding_intervals(f, st, CFG, calm, T0), {"BTCUSDT": 4})
        self.assertEqual(run.funding_intervals(f, st, CFG, calm, T0 + 300), {"BTCUSDT": 4})
        self.assertEqual(f.calls, 1)

    def test_funding_info_forced_refresh_when_hot(self):
        st, f = self.st(), FakeFetcher({"BTCUSDT": 4})
        run.funding_intervals(f, st, CFG, {"BTC": {"rate": 0.0001}}, T0)
        f.info = {"BTCUSDT": 1}
        self.assertEqual(run.funding_intervals(f, st, CFG, {"BTC": {"rate": -0.0011}}, T0 + 300), {"BTCUSDT": 1})
        self.assertEqual(f.calls, 2)

    def test_funding_info_failure(self):
        st = self.st()
        self.assertIsNone(run.funding_intervals(FakeFetcher(None), st, CFG, {}, T0))
        st["meta"]["funding_info"] = {"date": "2000-01-01", "intervals": {"BTCUSDT": 4}}
        self.assertEqual(run.funding_intervals(FakeFetcher(None), st, CFG, {}, T0), {"BTCUSDT": 4})

    def test_live_oi_down_reported(self):
        now = T0
        s = helpers.flat_series(now, 50)
        down = outputs.endpoints_down({"BTC": s}, {"BTC": now}, now, {}, oi_ok={"BTC": now - 3 * H})
        self.assertEqual(down, {"BTC": ["实时持仓量"]})
        self.assertEqual(outputs.endpoints_down({"BTC": s}, {"BTC": now}, now, {}), {})


class TestReviewFixes(unittest.TestCase):
    """PR #45 评审：部分失败不覆盖完整样本、缺配置用默认值、rate 缺失不中断、M1 卡片口径不变、空缓存报错。"""

    def test_partial_sample_keeps_previous_fields(self):
        st = {"series": {"BTC": S.empty()}, "meta": {}}
        app = {"assets": {"BTC": "BTCUSDT"}}
        run.sample_rt(st, app, {"BTC": {"rate": 0.0002, "mark_price": "100"}}, {"BTC": {"oi": 10.0}}, {}, T0)
        run.sample_rt(st, app, {}, {"BTC": {"oi": 11.0}}, {}, T0 + 60)          # 同桶第二轮：资金费率失败
        self.assertEqual(st["series"]["BTC"]["RT"][T0], (0.0002, 100.0, 11.0, 8))

    def test_missing_threshold_keys_use_defaults(self):
        bare = helpers.rules_cfg()
        bare["rules"]["funding_extreme"] = {"enabled": True}
        bare["rules"]["oi_spike"] = {"enabled": True}
        self.assertEqual(R.funding_extreme(rt({T0: (0.001, 1.0, 1.0, 8)}), T0, bare)["severity"], "P1")
        self.assertEqual(R.oi_spike(oi_series(1050.0, 101.0), T0, bare)["severity"], "P1")

    def test_hot_check_tolerates_missing_rate(self):
        st = {"series": {}, "meta": {}}
        self.assertEqual(run.funding_intervals(FakeFetcher({"X": 4}), st, CFG, {"BTC": {"rate": None}}, T0), {"X": 4})

    def test_m1_context_does_not_fall_back(self):
        s = helpers.flat_series(T0 - STEP, 50)
        self.assertIsNone(cards.context(s, T0)["price"])
        self.assertEqual(cards.context(s, T0, realtime=True)["price"], 100.0)

    def test_legacy_history_without_rt_loads(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "history-BTC.json")
            with open(p, "w") as fh:
                json.dump({"G": {str(T0): [2.0, 0.67]}}, fh)
            s = S.load(p)
        self.assertEqual(s["RT"], {})
        self.assertEqual(s["G"][T0], (2.0, 0.67))

    def test_backtest_empty_cache_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(backtest.main(["--cache", d]), 2)


class TestFetcherFixtures(unittest.TestCase):
    def test_open_interest_and_funding_info(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "BTCUSDT_openInterest.json"), "w") as fh:
                json.dump({"symbol": "BTCUSDT", "openInterest": "123.4", "time": T0 * 1000}, fh)
            with open(os.path.join(d, "ALL_fundingInfo.json"), "w") as fh:
                json.dump([{"symbol": "BTCUSDT", "fundingIntervalHours": 4}], fh)
            f = Fetcher({}, fixture_dir=d, now=T0)
            self.assertEqual(f.open_interest("BTC", "BTCUSDT")["oi"], 123.4)
            self.assertEqual(f.funding_info(), {"BTCUSDT": 4})
            self.assertIsNone(f.open_interest("ETH", "ETHUSDT"))
            self.assertEqual(f.errors[-1]["kind"], "skip")


# ---------------------------------------------------------------- 回测与历史参照
class TestBacktestAndReference(unittest.TestCase):
    def test_attach_rt_and_replay(self):
        s = helpers.flat_series(T0, 400, oi=1000.0, px=100.0)
        s["OI"][T0] = (1100.0, 1100.0 * 101.0)
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "BTCUSDT_fundingRate.json"), "w") as fh:
                json.dump([{"fundingTime": (T0 - 3 * H) * 1000 + 5, "fundingRate": "0.0025"}], fh)
            backtest.attach_rt({"BTC": s}, {"BTC": "BTCUSDT"}, d)
        self.assertEqual(s["RT"][T0 - 3 * H][0], 0.0025)              # 结算记录落在所在 5m 桶
        self.assertAlmostEqual(s["RT"][T0][1], 101.0)
        found, _, _ = backtest.replay({"BTC": s}, CFG, start=T0 - 4 * H, end=T0)
        rules_hit = [(c["rule"], c["severity"]) for c in found]
        self.assertIn(("funding_extreme", "P0"), rules_hit)
        self.assertIn(("oi_spike", "P0"), rules_hit)

    def test_reference_price_falls_back_to_mark(self):
        s = rt({T0: (0.003, 99.5, 1000.0, 8)})
        records = []
        card = {"asset": "BTC", "ts": T0, "hits": [{"rule": "funding_extreme", "severity": "P0",
                                                    "direction": "long", "magnitude": 0.3}]}
        reference.add_cards(records, [card], {"BTC": s})
        self.assertEqual(records[0]["price"], 99.5)


class TestLiveConfig(unittest.TestCase):
    def test_rules_yaml_has_m2_rules(self):
        live = common.load_config("rules.yaml")["rules"]
        self.assertEqual(live["funding_extreme"]["p1_abs_pct"], 0.10)
        self.assertEqual(live["funding_extreme"]["p0_abs_pct"], 0.20)
        self.assertEqual(live["oi_spike"]["p1_change_pct"], 5.0)
        self.assertEqual(live["oi_spike"]["min_price_move_pct"], 0.5)
        self.assertTrue(live["funding_extreme"]["enabled"] and live["oi_spike"]["enabled"])


if __name__ == "__main__":
    unittest.main()
