#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""整轮流程（全部离线）：冷启动回补、线上逐轮 = backtest 回放、dry-run 不落盘、推送路径、退出码、接口失效标记。

推送一律用假的 send_signal，绝不连 Telegram（根 .env 是真凭据）。
"""
import argparse
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import backtest  # noqa: E402
import helpers  # noqa: E402
import outputs  # noqa: E402
import run  # noqa: E402
import series as S  # noqa: E402
from sources import Fetcher  # noqa: E402

import common  # noqa: E402
from crypto_core import contracts  # noqa: E402

APP = dict(common.load_config("app.yaml"), cache30d={"enabled": False})   # 单测不跑 30 天缓存（太慢）
CFG = common.load_config("rules.yaml")
FAKE_ENV = {"TELEGRAM_BOT_TOKEN": "123456789:" + "A" * 35, "TELEGRAM_CHAT_ID": "123456"}


def args(**kw):
    base = {"notify": False, "dry_run": False, "fixtures": helpers.FIXTURES}
    base.update(kw)
    return argparse.Namespace(**base)


class RoundCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="futures-test-")
        self.paths = common.ensure_dirs(common.data_paths(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def round(self, now, **kw):
        fetcher = Fetcher(APP, fixture_dir=helpers.FIXTURES, now=now)
        return run.run_round(args(**kw), APP, CFG, self.paths, now, fetcher, env=kw.get("env"),
                             sender=kw.get("sender"))


class TestColdStart(RoundCase):
    def test_backfills_7_days_then_alerts(self):
        result = self.round(helpers.BNB_P0 + 300)                # 01:10 周期在 01:15 收盘
        hist = S.load(os.path.join(self.paths["state"], "history-BNB.json"))
        self.assertGreaterEqual(len(hist["G"]), 2016)            # 冷启动回补了 7 天
        rules = [(d["card"]["asset"], d["card"]["rule"]) for d in result["cards"]]
        self.assertIn(("BNB", "crowding_build"), rules)          # 首轮 P0 规则就能用
        self.assertEqual(run.exit_code(result), 0)
        export = common.safe_json_load(os.path.join(self.paths["exports"], "latest.json"), None)
        self.assertEqual(contracts.validate("futures/snapshot/v1", export), [])

    def test_dry_run_writes_nothing(self):
        self.round(helpers.BNB_P0 + 300, dry_run=True)
        self.assertEqual(os.listdir(self.paths["state"]), [])
        self.assertEqual(os.listdir(self.paths["exports"]), [])


class TestReplayEquivalence(RoundCase):
    def test_live_rounds_match_backtest(self):
        t0, t1 = helpers.BNB_P0 - 2400, helpers.BNB_P0 + 1200
        live = []
        for now in range(t0, t1 + 1, 300):
            live += [d["card"] for d in self.round(now)["cards"]]
        # 线上 now 时刻只看到已收盘的点（≤ now-5m），首轮回看 1 小时 → 对应回放的评估区间
        all_series = backtest.load_series(helpers.FIXTURES, APP["assets"])
        replayed, _, _ = backtest.replay(all_series, CFG, start=t0 - 3600, end=t1 - 300)

        def key(c):
            return (c["ts"], c["asset"], c["rule"], tuple(c["also"]), c["escalated"])
        self.assertTrue(live)
        self.assertEqual([key(c) for c in live], [key(c) for c in replayed])


class TestNotify(RoundCase):
    def test_sends_through_budget_with_fake_sender(self):
        sent = []

        def fake_send_signal(token, chat_id, text, **kw):
            sent.append((kw["module"], kw["severity"], kw["assets"], kw.get("disable_preview")))
            return True, ""
        with mock.patch.object(run, "send_signal", fake_send_signal):
            result = self.round(helpers.BNB_P0 + 300, notify=True, env=FAKE_ENV)
        self.assertIn(("crypto-futures", "P0", ["BNB"], True), sent)
        self.assertTrue(all(d["sent"] for d in result["cards"]))

    def test_missing_credentials_is_config_error(self):
        with self.assertRaises(RuntimeError):
            self.round(helpers.BNB_P0 + 300, notify=True, env={})


class TestExitAndHealth(unittest.TestCase):
    def test_exit_codes(self):
        skip = {"kind": "skip"}
        err = {"kind": "error"}
        self.assertEqual(run.exit_code({"errors": [skip, skip], "requests": 2, "cards": []}), 0)
        self.assertEqual(run.exit_code({"errors": [err], "requests": 15, "cards": []}), 1)
        self.assertEqual(run.exit_code({"errors": [err, err], "requests": 2, "cards": []}), 3)
        failed_send = [{"error": "HTTP 500"}]
        self.assertEqual(run.exit_code({"errors": [], "requests": 15, "cards": failed_send}), 1)

    def test_endpoint_down_after_one_hour(self):
        now = helpers.T0
        s = helpers.flat_series(now, 50)
        for ts in [t for t in s["K"] if t > now - 90 * 60]:
            del s["K"][ts]                                         # 主动买卖量 90 分钟没新数据
        down = outputs.endpoints_down({"BTC": s}, {"BTC": now}, now, APP)
        self.assertEqual(down, {"BTC": ["主动买卖量"]})
        last = outputs.last_run(now, 15, [], down, [])
        self.assertFalse(last["primary_ok"])
        self.assertIn("BTC 主动买卖量", last["detail"])
        self.assertEqual(outputs.endpoints_down({"BTC": helpers.flat_series(now, 50)}, {"BTC": now}, now, APP), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
