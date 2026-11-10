#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端（离线 fixtures）：run.py 的采集→指标→候选→落盘链路。

运行：cd crypto-onchain && ../.venv/bin/python -m unittest tests.test_run_fixtures -v
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "libs", "crypto_core", "src"))

import holdings_collect  # noqa: E402
import run  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")

# 测试专用持仓白名单：与 fixtures/holdings.json 对应，不依赖生产 config（生产会持续换真实地址）
TEST_WATCHLIST = {
    "schema_version": 1,
    "entries": [
        {"chain": "btc", "address": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
         "label": "示例地址 A（未核实）", "source_note": "fixture：离线回放用，不冒充真实归属",
         "threshold_pct": 5.0},
        {"chain": "erc20", "address": "0x2222222222222222222222222222222222222222",
         "token": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                   "symbol": "USDT", "decimals": 6},
         "price_usd": 1.0, "label": "示例地址 B（未核实）",
         "source_note": "fixture：离线回放用，不冒充真实归属", "threshold_pct": 5.0},
    ],
}
FIXTURE_NOW = 1794177000  # 与 fixtures 里最大的成交 ts 对齐（官方 OI 历史 fixtures 用）


def _run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = run.main(argv)
    return rc, buf.getvalue()


def _files(root):
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            out.append(os.path.join(dirpath, name))
    return out


class TestFixturesE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onchain-e2e-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_round(self):
        orig_load = holdings_collect.common.load_config

        def fake_load(name):
            if name == holdings_collect.WATCHLIST_FILE:
                return TEST_WATCHLIST
            return orig_load(name)

        with mock.patch.object(holdings_collect.common, "load_config", side_effect=fake_load):
            rc, out = _run(["--fixtures", FIXTURES, "--data-dir", self.tmp, "--json"])
        self.assertEqual(rc, 0)
        snap = json.loads(out)
        self.assertEqual(snap["schema"], "onchain/shadow/v1")
        self.assertEqual(snap["source_module"], "crypto-onchain")
        self.assertEqual(set(snap["assets"]), {"BTC", "ETH", "BNB"})

        btc = snap["assets"]["BTC"]
        self.assertGreater(btc["taker_5m"]["sell_ratio"], 0.7)
        self.assertEqual(btc["taker_5m"]["large_sell_count"], 3)
        self.assertGreater(btc["liq_15m"]["total_usd"], 35_000_000)
        self.assertGreater(btc["liq_15m"]["long_ratio"], 0.6)
        self.assertGreater(btc["liq_15m"]["coverage_minutes"], 5)

        # ETH 主动卖占比低 → 不触发；BNB 清算额低于阈值 → 不触发
        self.assertLess(snap["assets"]["ETH"]["taker_5m"]["sell_ratio"], 0.58)
        self.assertLess(snap["assets"]["BNB"]["liq_15m"]["total_usd"], 8_000_000)

        self.assertLess(snap["cex_flows"]["net_24h_bps"], -50)
        self.assertLess(snap["stablecoins"]["net_issue_24h_usd"], -500_000_000)
        self.assertTrue(snap["price_context"]["ok"])

        got = {(c["signal"], c.get("asset")) for c in snap["candidates"]}
        self.assertIn(("taker_sell_pressure", "BTC"), got)
        self.assertIn(("liquidation_cascade", "BTC"), got)
        self.assertIn(("liquidation_cascade", "ETH"), got)
        self.assertIn(("cex_reserve_outflow", None), got)
        self.assertIn(("stablecoin_outflow", None), got)

        # M2 归因：BTC/ETH 多头被强平（官方 OI 历史显示下降 → high）、BNB 资金撤离
        self.assertEqual(snap["assets"]["BTC"]["oi_source"], "binance_hist")
        # 官方 OI 历史：past=101970（NOW-1500）、current=99000（NOW-600）→ -2.9%
        self.assertAlmostEqual(snap["assets"]["BTC"]["oi_change_15m_pct"], -2.9, places=1)
        att = snap["attribution"]
        self.assertEqual(att["assets"]["BTC"]["category_code"], "long_liquidation")
        self.assertEqual(att["assets"]["BTC"]["confidence"], "high")
        self.assertEqual(att["assets"]["ETH"]["category_code"], "long_liquidation")
        self.assertEqual(att["assets"]["BNB"]["category_code"], "capital_exit")
        self.assertEqual(att["market"]["category_code"], "capital_exit")
        self.assertTrue(att["assets"]["BTC"]["evidence"])
        self.assertTrue(att["assets"]["BTC"]["counter_evidence"])
        self.assertIn("无法判断卖方身份", att["assets"]["BTC"]["unknown"])

        # 候选带上归因
        btc_taker = [c for c in snap["candidates"] if c["signal"] == "taker_sell_pressure"
                     and c["asset"] == "BTC"][0]
        self.assertEqual(btc_taker["attribution"]["category_code"], "long_liquidation")

        # 链上大额转账（配置阈值 ETH $10M / BSC $5M：8M USDT 被过滤，12M USDC + 7M BSC USDT 保留）
        wf = snap["walletflow"]
        self.assertTrue(wf["enabled"])
        self.assertEqual(wf["n_large"], 2)
        self.assertAlmostEqual(wf["usd_total"], 19_000_000)
        self.assertEqual([c["name"] for c in wf["chains"]], ["ethereum", "bsc"])
        self.assertEqual({(r["chain"], r["symbol"]) for r in wf["recent"]},
                         {("ethereum", "USDC"), ("bsc", "USDT")})

        # 已知地址持仓（人工配置的示例地址；fixtures 离线回放，单地址失败不影响整轮）
        hold = snap["holdings"]
        self.assertTrue(hold["enabled"])
        self.assertFalse(hold["cached"])
        self.assertEqual(hold["n"], 2)
        self.assertEqual(hold["n_errors"], 0)
        self.assertAlmostEqual(hold["usd_total"], 18209130 / 1e8 * 74340.0 + 1.5, places=2)
        btc_hold = [e for e in hold["entries"] if e["chain"] == "btc"][0]
        self.assertAlmostEqual(btc_hold["balance"], 18209130 / 1e8)
        self.assertEqual(btc_hold["symbol"], "BTC")
        self.assertIn("未核实", btc_hold["label"])
        self.assertTrue(btc_hold["source_note"])
        usdt_hold = [e for e in hold["entries"] if e["chain"] == "erc20"][0]
        self.assertAlmostEqual(usdt_hold["balance"], 1.5)
        self.assertAlmostEqual(usdt_hold["usd"], 1.5)
        self.assertEqual(hold["changes"], [])
        state_dir = os.path.join(self.tmp, "state")
        self.assertTrue(os.path.isfile(os.path.join(state_dir, "holdings.json")))
        self.assertTrue(os.path.isfile(os.path.join(state_dir, "holdings-history.jsonl")))

        # 跨模块导出契约 onchain/pressure/v1
        export_path = os.path.join(self.tmp, "exports", "onchain-pressure.json")
        self.assertTrue(os.path.isfile(export_path))
        with open(export_path, encoding="utf-8") as f:
            export = json.load(f)
        self.assertEqual(export["schema"], "onchain/pressure/v1")
        self.assertEqual(export["source_module"], "crypto-onchain")
        self.assertEqual(export["assets"]["BTC"]["attribution"]["category_code"], "long_liquidation")
        self.assertIn("oi_change_15m_pct", export["assets"]["BTC"])
        self.assertEqual(export["market"]["cex_net_24h_bps"], snap["cex_flows"]["net_24h_bps"])
        self.assertEqual(export["walletflow"]["n_large"], 2)
        self.assertAlmostEqual(export["walletflow"]["usd_total"], 19_000_000)

        # 落盘：快照 + 候选 jsonl
        state = os.path.join(self.tmp, "state")
        self.assertTrue(os.path.isfile(os.path.join(state, "shadow-latest.json")))
        with open(os.path.join(state, "shadow-candidates.jsonl"), encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(records), len(snap["candidates"]))
        self.assertTrue(all("note" in r for r in records))

    def test_dry_run_writes_nothing(self):
        rc, out = _run(["--fixtures", FIXTURES, "--data-dir", self.tmp, "--dry-run", "--json"])
        self.assertEqual(rc, 0)
        self.assertIn('"schema"', out)
        # 连目录都不建（只读模式无副作用）
        self.assertEqual(os.listdir(self.tmp), [])

    def test_identity_words_absent(self):
        _, out = _run(["--fixtures", FIXTURES, "--data-dir", self.tmp, "--dry-run", "--json"])
        for word in ("某机构", "某巨鲸", "某基金", "某个人"):
            self.assertNotIn(word, out)

    def test_degraded_all_sources_down(self):
        empty = tempfile.mkdtemp(prefix="onchain-empty-")
        try:
            rc, _out = _run(["--fixtures", empty, "--data-dir", self.tmp, "--dry-run"])
            self.assertEqual(rc, 3)
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    def test_status_after_round(self):
        _run(["--fixtures", FIXTURES, "--data-dir", self.tmp])
        rc, out = _run(["--status", "--data-dir", self.tmp, "--json"])
        self.assertEqual(rc, 0)
        status = json.loads(out)
        self.assertTrue(status["snapshot_ok"])
        self.assertGreaterEqual(status["candidates_total"], 3)


if __name__ == "__main__":
    unittest.main()
